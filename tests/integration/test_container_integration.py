"""Integration tests for containerized execution with Podman, AGY, and SELinux isolation."""

from dataclasses import dataclass
import os
from pathlib import Path
import secrets
import shutil
import subprocess
from typing import Iterator

import pytest

from novui.agy_output import markers_to_requests, parse_agy_text
from novui.checks import check_allowed_paths, check_cli_output
from novui.config import Settings, load_settings
from novui.container import (
    Mount,
    agy_command,
    build_agy_mounts,
    image_id,
    run_container,
)
from novui.gitinspect import get_changes, run_git
from novui.jobhome import job_home
from novui.mountinfo import find_unexpected_mounts, parse_mountinfo

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("NOVUI_INTEGRATION") != "1",
        reason="Requires NOVUI_INTEGRATION=1 environment variable",
    ),
]


@dataclass
class IntegrationEnv:
    root_dir: Path
    data_dir: Path
    worktree_root: Path
    jobhome_root: Path
    repo_dir: Path
    worktree_dir: Path
    log_dir: Path
    settings: Settings
    fake_token_path: Path


def _check_no_containers() -> None:
    proc = subprocess.run(
        ["podman", "ps", "-a", "--filter", "name=novui-itest", "--format", "{{.Names}}"],
        capture_output=True,
        text=True,
        check=True,
    )
    names = proc.stdout.strip()
    assert names == "", f"Remaining containers found: {names}"


@pytest.fixture
def itest_env() -> Iterator[IntegrationEnv]:
    random_hex = secrets.token_hex(4)
    root_dir = Path.home() / ".local/share/novui-itest" / random_hex
    data_dir = root_dir / "data"
    worktree_root = data_dir / "worktrees"
    jobhome_root = data_dir / "jobhomes"
    repo_dir = root_dir / "repo"
    log_dir = root_dir / "logs"

    root_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    data_dir.mkdir(mode=0o700)
    worktree_root.mkdir(mode=0o700)
    jobhome_root.mkdir(mode=0o700)
    repo_dir.mkdir(mode=0o700)
    log_dir.mkdir(mode=0o700)

    # 1. Initialize sample novel repository
    subprocess.run(["git", "-C", str(repo_dir), "init", "-b", "main"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo_dir), "config", "user.name", "test"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo_dir), "config", "user.email", "test@example.invalid"], check=True, capture_output=True)

    (repo_dir / "project.yaml").write_text("title: Integration Test Novel\n", encoding="utf-8")
    (repo_dir / "world").mkdir()
    (repo_dir / "world/w.md").write_text("# World Setting\n", encoding="utf-8")
    (repo_dir / "characters").mkdir()
    (repo_dir / "characters/C001.yaml").write_text("id: C001\nname: Hero\n", encoding="utf-8")
    (repo_dir / ".novui/approvals").mkdir(parents=True)
    (repo_dir / ".novui/approvals/.keep").write_text("", encoding="utf-8")
    (repo_dir / "chapters/ch-001").mkdir(parents=True)
    (repo_dir / "chapters/ch-001/draft.md").write_text("# Chapter 1\n\nInitial draft text.\n", encoding="utf-8")

    subprocess.run(["git", "-C", str(repo_dir), "add", "."], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo_dir), "-c", "user.name=test", "-c", "user.email=test@example.invalid", "commit", "-m", "Initial commit"],
        check=True,
        capture_output=True,
    )

    # 2. Create linked worktree
    wt_name = f"itest-{secrets.token_hex(4)}"
    worktree_dir = worktree_root / wt_name
    run_git(repo_dir, "worktree", "add", str(worktree_dir), "-b", wt_name)

    # 3. Load settings and verify image
    settings = load_settings()
    img_check = subprocess.run(["podman", "image", "exists", settings.agy_image])
    if img_check.returncode != 0:
        pytest.fail(f"Required Podman image does not exist: {settings.agy_image}")

    # 4. Create fake token file
    fake_token = root_dir / "fake_token"
    fake_token.write_text(f"FAKE-TOKEN-{secrets.token_hex(8)}\n", encoding="utf-8")

    env_obj = IntegrationEnv(
        root_dir=root_dir,
        data_dir=data_dir,
        worktree_root=worktree_root,
        jobhome_root=jobhome_root,
        repo_dir=repo_dir,
        worktree_dir=worktree_dir,
        log_dir=log_dir,
        settings=settings,
        fake_token_path=fake_token,
    )

    try:
        yield env_obj
    finally:
        # Cleanup temporary test directory
        shutil.rmtree(root_dir, ignore_errors=True)
        _check_no_containers()


def test_i1_mountinfo_inspection(itest_env: IntegrationEnv) -> None:
    """I1: Run cat /proc/self/mountinfo and verify no unexpected mounts."""
    jh_id = f"itest-{secrets.token_hex(4)}"
    with job_home(itest_env.jobhome_root, jh_id, itest_env.fake_token_path) as jh:
        mounts = build_agy_mounts(
            job_home=jh,
            jobhome_root=itest_env.jobhome_root,
        )
        name = f"novui-itest-{secrets.token_hex(4)}"
        res = run_container(
            name=name,
            image=itest_env.settings.agy_image,
            mounts=mounts,
            command=["cat", "/proc/self/mountinfo"],
            timeout_seconds=30.0,
            log_dir=itest_env.log_dir,
        )
        assert res.exit_code == 0
        entries = parse_mountinfo(res.stdout.decode("utf-8"))
        unexpected = find_unexpected_mounts(entries, allowed=["/home/agy"])
        assert unexpected == []

    _check_no_containers()


def test_i2_container_visibility(itest_env: IntegrationEnv) -> None:
    """I2: Verify container visibility and home permissions with AGY mounts."""
    jh_id = f"itest-{secrets.token_hex(4)}"
    script = (
        "(touch /home/agy/x && echo 'HOME_WRITE=OK') || echo 'HOME_WRITE=DENIED'\n"
        "echo \"TOKEN_MODE=$(stat -c %a /home/agy/.gemini/antigravity-cli/antigravity-oauth-token)\"\n"
        "echo \"WORKSPACE_ENTRIES=$(ls -A /workspace 2>/dev/null | wc -l)\"\n"
    )

    with job_home(itest_env.jobhome_root, jh_id, itest_env.fake_token_path) as jh:
        mounts = build_agy_mounts(
            job_home=jh,
            jobhome_root=itest_env.jobhome_root,
        )
        name = f"novui-itest-{secrets.token_hex(4)}"
        res = run_container(
            name=name,
            image=itest_env.settings.agy_image,
            mounts=mounts,
            command=["sh", "-c", script],
            timeout_seconds=30.0,
            log_dir=itest_env.log_dir,
        )
        assert res.exit_code == 0
        output_lines = [line.strip() for line in res.stdout.decode("utf-8").splitlines() if line.strip()]

        expected_map = {
            "HOME_WRITE": "OK",
            "TOKEN_MODE": "600",
            "WORKSPACE_ENTRIES": "0",
        }

        actual_map = {}
        for line in output_lines:
            if "=" in line:
                k, v = line.split("=", 1)
                actual_map[k] = v

        assert actual_map == expected_map

    _check_no_containers()


def test_i3_timeout_and_container_cleanup(itest_env: IntegrationEnv) -> None:
    """I3: Verify container timeout and guaranteed removal."""
    jh_id = f"itest-{secrets.token_hex(4)}"
    with job_home(itest_env.jobhome_root, jh_id, itest_env.fake_token_path) as jh:
        mounts = build_agy_mounts(
            job_home=jh,
            jobhome_root=itest_env.jobhome_root,
        )
        name = f"novui-itest-{secrets.token_hex(4)}"
        res = run_container(
            name=name,
            image=itest_env.settings.agy_image,
            mounts=mounts,
            command=["sleep", "60"],
            timeout_seconds=3.0,
            kill_grace_seconds=3.0,
            log_dir=itest_env.log_dir,
        )
        assert res.timed_out is True

    _check_no_containers()


def test_i4_agy_execution_and_token_protection(itest_env: IntegrationEnv) -> None:
    """I4: Run actual AGY model execution, verify text output, Controller write, and token isolation."""
    model = os.environ.get("NOVUI_AGY_MODEL")
    if not model:
        pytest.fail("NOVUI_AGY_MODEL environment variable is not set")

    token_path = itest_env.settings.agy_token_path
    if not token_path.is_file():
        pytest.fail(f"Real AGY token file not found: {token_path}")

    real_token_bytes = token_path.read_bytes().strip()
    if not real_token_bytes:
        pytest.fail("Real token file is empty")

    jh_id = f"itest-{secrets.token_hex(4)}"
    name = f"novui-itest-{secrets.token_hex(4)}"

    draft_file = itest_env.worktree_dir / "chapters/ch-001/draft.md"
    original_draft = draft_file.read_text(encoding="utf-8")

    prompt = (
        "あなたはシェルコマンドを実行できず、ファイルを読むこともできません。ツールは一切使わないでください。必要な情報はすべて以下にあります。\n"
        "設定に書かれていないこと（人物の名前、地名、出来事など）を、推測で決めてはいけません。必要なのに設定にない場合は、その箇所に【要確認：何が不明か】と書いてください。\n"
        "【これまでの本文】\n"
        f"{original_draft}"
        "【指示】\n"
        "これまでの本文の続きとして、「結合試験」という1行だけを書いてください。前置き・説明・コードブロックは不要です。本文だけを出力してください。"
    )

    captured_jh_dir: Path | None = None

    with job_home(itest_env.jobhome_root, jh_id, token_path) as jh:
        captured_jh_dir = jh
        mounts = build_agy_mounts(
            job_home=jh,
            jobhome_root=itest_env.jobhome_root,
        )
        cmd = agy_command(model=model, prompt=prompt)

        res = run_container(
            name=name,
            image=itest_env.settings.agy_image,
            mounts=mounts,
            command=cmd,
            timeout_seconds=300.0,
            log_dir=itest_env.log_dir,
        )

    # === DIAGNOSTIC OUTPUT FOR TEST_I4 ===
    print("\n--- [DIAGNOSTIC I4 START] ---")
    print(f"exit_code: {res.exit_code}")
    print(f"elapsed_seconds: {res.elapsed_seconds}")
    print(f"timed_out: {res.timed_out}")
    print(f"len(stdout): {len(res.stdout)}")
    print(f"len(stderr): {len(res.stderr)}")

    if real_token_bytes in res.stderr:
        print("stderr: TOKEN_FOUND_IN_STDERR")
    else:
        print(f"stderr (head 3000): {res.stderr[:3000].decode('utf-8', errors='replace')!r}")

    if real_token_bytes in res.stdout:
        print("stdout: TOKEN_FOUND_IN_STDOUT")
    else:
        print(f"stdout (head 3000): {res.stdout[:3000].decode('utf-8', errors='replace')!r}")

    print("--- [DIAGNOSTIC I4 END] ---\n")

    # 1. Process and CLI output validation
    assert res.exit_code == 0
    cli_check = check_cli_output(res.exit_code, res.stdout)
    assert cli_check.status == "PASS"

    agy_text = parse_agy_text(res.stdout)
    assert "結合試験" in agy_text.text
    assert len(agy_text.markers) == 0

    # 2. Controller writes output to worktree draft.md
    updated_draft = original_draft + agy_text.text
    draft_file.write_text(updated_draft, encoding="utf-8")

    changes = get_changes(itest_env.worktree_dir)
    assert [c.path for c in changes] == ["chapters/ch-001/draft.md"]

    allowed_check = check_allowed_paths(changes, allowed=["chapters/ch-001/draft.md"])
    assert allowed_check.status == "PASS"

    # 3. Job home cleanup validation
    assert captured_jh_dir is not None
    assert not captured_jh_dir.exists()

    # 4. Token leakage check (byte comparison without echoing token in failure message)
    assert real_token_bytes not in res.stdout, "Token leaked in stdout"
    assert real_token_bytes not in res.stderr, "Token leaked in stderr"

    for log_file in itest_env.log_dir.glob("*"):
        if log_file.is_file():
            assert real_token_bytes not in log_file.read_bytes(), f"Token leaked in log file: {log_file.name}"

    for wt_file in itest_env.worktree_dir.rglob("*"):
        if wt_file.is_file():
            assert real_token_bytes not in wt_file.read_bytes(), f"Token leaked in worktree file: {wt_file.name}"

    # 5. Extract metadata and print
    img_id = image_id(itest_env.settings.agy_image)

    print(f"\n[I4 RESULT] Model: {model}")
    print(f"[I4 RESULT] Image ID: {img_id}")

    _check_no_containers()


def test_i5_missing_setting_marker(itest_env: IntegrationEnv) -> None:
    """I5: Run AGY with missing setting prompt (P1), verify marker extraction and schema validation."""
    model = os.environ.get("NOVUI_AGY_MODEL")
    if not model:
        pytest.fail("NOVUI_AGY_MODEL environment variable is not set")

    token_path = itest_env.settings.agy_token_path
    if not token_path.is_file():
        pytest.fail(f"Real AGY token file not found: {token_path}")

    real_token_bytes = token_path.read_bytes().strip()
    if not real_token_bytes:
        pytest.fail("Real token file is empty")

    jh_id = f"itest-{secrets.token_hex(4)}"
    name = f"novui-itest-{secrets.token_hex(4)}"

    p1_prompt = (
        "あなたはシェルコマンドを実行できず、ファイルを読むこともできません。ツールは一切使わないでください。必要な情報はすべて以下にあります。\n"
        "設定に書かれていないこと（人物の名前、地名、出来事など）を、推測で決めてはいけません。必要なのに設定にない場合は、その箇所に【要確認：何が不明か】と書いてください。\n"
        "【世界観】\n"
        "舞台は架空の港町。\n"
        "【これまでの本文】\n"
        "# Chapter 1\n\n"
        "Initial draft text.\n"
        "【指示】\n"
        "主人公が名乗る場面を、続きとして1〜2文で書いてください。前置き・説明・コードブロックは不要です。本文だけを出力してください。"
    )

    captured_jh_dir: Path | None = None

    with job_home(itest_env.jobhome_root, jh_id, token_path) as jh:
        captured_jh_dir = jh
        mounts = build_agy_mounts(
            job_home=jh,
            jobhome_root=itest_env.jobhome_root,
        )
        cmd = agy_command(model=model, prompt=p1_prompt)

        res = run_container(
            name=name,
            image=itest_env.settings.agy_image,
            mounts=mounts,
            command=cmd,
            timeout_seconds=300.0,
            log_dir=itest_env.log_dir,
        )

    # 1. Process and CLI output validation
    assert res.exit_code == 0
    cli_check = check_cli_output(res.exit_code, res.stdout)
    assert cli_check.status == "PASS"

    # 2. Parse text and verify markers
    agy_text = parse_agy_text(res.stdout)
    assert len(agy_text.markers) >= 1

    # 3. Convert to requests and verify schema
    reqs = markers_to_requests(agy_text.markers, job_id="job-1", chapter_id="ch-001")
    assert isinstance(reqs, list) and len(reqs) >= 1

    # 4. Job home cleanup validation
    assert captured_jh_dir is not None
    assert not captured_jh_dir.exists()

    # 5. Token leakage check
    assert real_token_bytes not in res.stdout, "Token leaked in stdout"
    assert real_token_bytes not in res.stderr, "Token leaked in stderr"

    print(f"\n[I5 RESULT] Markers found: {len(agy_text.markers)}")
    for idx, m in enumerate(agy_text.markers):
        print(f"  Marker #{idx + 1}: message={m.message!r}, start={m.start}, end={m.end}")

    _check_no_containers()
