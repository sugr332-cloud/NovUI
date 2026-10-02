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
from novui.jobrunner import DraftJobRequest, jobs_dir, run_draft_job
from novui.locks import RunLock
from novui.models import (
    fetch_agy_models,
    resolve_model,
    save_catalog,
    select_model,
)
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

    # Add world/w.md and chapters/ch-001/draft.md (as required by instruction)
    (repo_dir / "world").mkdir()
    (repo_dir / "world" / "w.md").write_text("# World\n\n舞台は架空の港町。\n", encoding="utf-8")
    (repo_dir / "world" / "setting.yaml").write_text("location: 架空の港町\nclimate: 温暖で潮風が強い\n", encoding="utf-8")

    (repo_dir / "chapters" / "ch-001").mkdir(parents=True)
    (repo_dir / "chapters" / "ch-001" / "draft.md").write_text("# Chapter 1\n\nInitial draft text.\n", encoding="utf-8")
    (repo_dir / "chapters" / "ch-001" / "plan.yaml").write_text("title: 第1章 到着\n", encoding="utf-8")

    run_git(repo_dir, "add", "-A")
    run_git(repo_dir, "-c", "user.name=NovUI Integration", "-c", "user.email=itest@novui.local",
            "commit", "-m", "setup project files")

    token_path = Path.home() / ".gemini/antigravity-cli/antigravity-oauth-token"
    agy_image = os.environ.get("NOVUI_AGY_IMAGE", "localhost/novui-agy:1.2.14")

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
        context_paths=("world/w.md", "chapters/ch-001/draft.md"),
        instruction="これまでの本文の続きとして、「結合試験」という1行だけを書いてください。",
    )

    record = run_draft_job(settings, req, run_lock)

    # 1. 状態の確認：COMPLETED
    assert record["state"] == JobState.COMPLETED.name

    # 2. checks に FAIL がないこと
    assert not any(c["status"] == "FAIL" for c in record["checks"])

    # 3. 作業ブランチが作成され commit されていること
    branch_ref = f"refs/heads/ai/ch-001/{job_id}"
    branch_commit = run_git(repo, "rev-parse", branch_ref).decode("utf-8").strip()
    assert branch_commit != main_before

    # 4. 作業ブランチの draft.md に「結合試験」を含むこと
    draft_content = run_git(repo, "show", f"{branch_ref}:chapters/ch-001/draft.md").decode("utf-8")
    assert "結合試験" in draft_content

    # 5. main の HEAD は進んでいないこと
    assert head_commit(repo, "main") == main_before

    # 6. run_lock が解放されていること
    assert run_lock.holder(work_key) is None

    # 7. Job用HOME（settings.jobhome_root の中）が空であること
    if settings.jobhome_root.exists():
        job_homes = [e for e in settings.jobhome_root.iterdir() if e.name.startswith("job-") or e.name.startswith("itest-")]
        assert len(job_homes) == 0, f"Leftover job_home: {job_homes}"

    # 8. podman ps -a --filter name=<コンテナ名> が空であること
    container_name = f"novui-{work_key}-{job_id}"
    proc = subprocess.run(
        ["podman", "ps", "-a", "--filter", f"name={container_name}", "--format", "{{.Names}}"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert proc.stdout.strip() == "", f"Container {container_name} is still present"

    # 9. 実際のトークンの内容（bytes）が jobs_dir 配下の全ファイルと worktree 内の全ファイルに含まれないこと
    token_bytes = settings.agy_token_path.read_bytes().strip()
    assert len(token_bytes) > 0

    jdir = jobs_dir(settings, work_key)
    if jdir.exists():
        for f in jdir.rglob("*"):
            if f.is_file():
                assert token_bytes not in f.read_bytes(), f"Token found in job file {f.name}"

    wt_dir = settings.worktree_root / work_key
    if wt_dir.exists():
        for f in wt_dir.rglob("*"):
            if f.is_file():
                assert token_bytes not in f.read_bytes(), f"Token found in worktree file {f.name}"


def test_integration_j2_model_catalog(itest_env: tuple[Settings, Path, RunLock]) -> None:
    settings, repo, run_lock = itest_env

    # 1. fetch_agy_models を実行
    catalog = fetch_agy_models(settings)
    model_ids = [m["id"] for m in catalog.get("models", [])]
    print(f"\n[J2 MODELS] Total {len(model_ids)} models fetched: {model_ids}")

    # 2. models の id に gemini-3.8-flash-high を含むこと
    assert "gemini-3.8-flash-high" in model_ids, f"gemini-3.8-flash-high not in fetched models: {model_ids}"

    # 3. save_catalog の後、select_model を行い、resolve_model が一致すること
    saved_path = save_catalog(settings, catalog)
    assert saved_path.is_file()

    select_model(settings, "draft", "gemini-3.8-flash-high")
    resolved = resolve_model(settings, "draft")
    assert resolved == "gemini-3.8-flash-high"


def test_integration_j3_undefined_settings(itest_env: tuple[Settings, Path, RunLock]) -> None:
    settings, repo, run_lock = itest_env
    model = os.environ.get("NOVUI_AGY_MODEL", "gemini-3.8-flash-high")
    work_key = f"itest-work-{secrets.token_hex(2)}"
    job_id = "job-0003"

    req = DraftJobRequest(
        work_key=work_key,
        repo=repo,
        chapter_id="ch-001",
        job_id=job_id,
        model=model,
        context_paths=("world/w.md",),
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
