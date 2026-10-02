"""Integration tests for draft Job runner using real Podman and AGY."""

import os
from pathlib import Path
import secrets
import shutil
import subprocess
from typing import Iterator

import pytest

from novui.config import Settings
from novui.gitinspect import run_git
from novui.jobrunner import DraftJobRequest, run_draft_job
from novui.locks import RunLock
from novui.schema import validate_or_raise
from novui.states import JobState
from novui.workrepo import head_commit
from novui.yamlio import load_yaml

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("NOVUI_INTEGRATION") != "1",
        reason="Requires NOVUI_INTEGRATION=1 environment variable",
    ),
]


@pytest.fixture
def itest_env() -> Iterator[tuple[Settings, Path, RunLock]]:
    random_hex = secrets.token_hex(4)
    root_dir = Path.home() / ".local/share/novui-itest" / f"itest-{random_hex}"
    data_dir = root_dir / "data"
    worktree_root = data_dir / "worktrees"
    jobhome_root = data_dir / "jobhomes"
    repo_dir = root_dir / "repo"

    root_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    data_dir.mkdir(mode=0o700)
    worktree_root.mkdir(mode=0o700)
    jobhome_root.mkdir(mode=0o700)
    repo_dir.mkdir(mode=0o700)

    # Initialize repository on main branch
    run_git(repo_dir, "init", "-b", "main")
    run_git(repo_dir, "-c", "user.name=NovUI Integration", "-c", "user.email=itest@novui.local",
            "commit", "--allow-empty", "-m", "init")

    # Add setting and plan
    (repo_dir / "world").mkdir()
    setting_text = "location: 架空の港町\nclimate: 温暖で潮風が強い\n"
    (repo_dir / "world" / "setting.yaml").write_text(setting_text, encoding="utf-8")

    (repo_dir / "chapters" / "ch-001").mkdir(parents=True)
    plan_text = "title: 第1章 到着\ntarget_chars:\n  min: 200\n  max: 1000\n"
    (repo_dir / "chapters" / "ch-001" / "plan.yaml").write_text(plan_text, encoding="utf-8")

    run_git(repo_dir, "add", "-A")
    run_git(repo_dir, "-c", "user.name=NovUI Integration", "-c", "user.email=itest@novui.local",
            "commit", "-m", "setup project files")

    token_path = Path.home() / ".gemini/antigravity-cli/antigravity-oauth-token"
    agy_image = os.environ.get("NOVUI_AGY_IMAGE", "localhost/novui-spike:agy-1.2.14")

    settings = Settings(
        data_dir=data_dir,
        worktree_root=worktree_root,
        jobhome_root=jobhome_root,
        agy_image=agy_image,
        agy_token_path=token_path,
        timeouts={"agy_draft": 300},
        git_name="NovUI Integration",
        git_email="itest@novui.local",
    )
    run_lock = RunLock()

    try:
        yield settings, repo_dir, run_lock
    finally:
        shutil.rmtree(root_dir, ignore_errors=True)


def test_integration_j1_normal_draft(itest_env: tuple[Settings, Path, RunLock]) -> None:
    settings, repo, run_lock = itest_env
    main_before = head_commit(repo, "main")
    model = os.environ.get("NOVUI_AGY_MODEL", "gemini-3.8-flash-high")
    work_key = f"itest-work-{secrets.token_hex(2)}"
    job_id = "job-0001"

    req = DraftJobRequest(
        work_key=work_key,
        repo=repo,
        chapter_id="ch-001",
        job_id=job_id,
        model=model,
        context_paths=("world/setting.yaml", "chapters/ch-001/plan.yaml"),
        instruction="主人公が港に着いて船を降りる短い場面（300字程度）を書いてください。",
        target_chars=(100, 1000),
    )

    record = run_draft_job(settings, req, run_lock)

    # 1. 状態の確認：COMPLETED または WAITING_HUMAN（markers が出た場合）
    assert record["state"] in (JobState.COMPLETED.name, JobState.WAITING_HUMAN.name)

    # 2. checks に FAIL がないこと
    assert not any(c["status"] == "FAIL" for c in record["checks"])

    # 3. 作業ブランチが作成され commit されていること
    branch_ref = f"refs/heads/ai/ch-001/{job_id}"
    branch_commit = run_git(repo, "rev-parse", branch_ref).decode("utf-8").strip()
    assert branch_commit != main_before

    # 4. draft.md に日本語本文が100文字以上書かれていること
    draft_content = run_git(repo, "show", f"{branch_ref}:chapters/ch-001/draft.md").decode("utf-8")
    assert len(draft_content.strip()) >= 100

    # 5. main の HEAD は進んでいないこと
    assert head_commit(repo, "main") == main_before

    # 6. run_lock が解放されていること
    assert run_lock.holder(work_key) is None


def test_integration_j2_undefined_settings(itest_env: tuple[Settings, Path, RunLock]) -> None:
    settings, repo, run_lock = itest_env
    model = os.environ.get("NOVUI_AGY_MODEL", "gemini-3.8-flash-high")
    work_key = f"itest-work-{secrets.token_hex(2)}"
    job_id = "job-0002"

    req = DraftJobRequest(
        work_key=work_key,
        repo=repo,
        chapter_id="ch-001",
        job_id=job_id,
        model=model,
        context_paths=("world/setting.yaml",),
        instruction="主人公が名前を名乗る短い場面を書いてください。名前は設定に書かれていなければ絶対に推測せず【要確認：主人公の名前】としてください。",
    )

    record = run_draft_job(settings, req, run_lock)

    # 1. WAITING_HUMAN 状態
    assert record["state"] == JobState.WAITING_HUMAN.name
    assert record["history"][-1]["event"] == "NEEDS_HUMAN_INPUT"

    # 2. checks に FAIL がないこと
    assert not any(c["status"] == "FAIL" for c in record["checks"])

    # 3. requests.yaml が作成され schema に適合すること
    branch_ref = f"refs/heads/ai/ch-001/{job_id}"
    req_yaml_str = run_git(repo, "show", f"{branch_ref}:chapters/ch-001/requests.yaml").decode("utf-8")
    import yaml
    req_doc = yaml.safe_load(req_yaml_str)
    validate_or_raise(req_doc, "requests")
    assert len(req_doc) >= 1
    assert any("主人公の名前" in r.get("message", "") for r in req_doc)

    # 4. run_lock が解放されていること
    assert run_lock.holder(work_key) is None
