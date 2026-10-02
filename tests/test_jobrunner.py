"""Acceptance and unit tests for novui.jobrunner module."""

from pathlib import Path
import subprocess
import pytest

from novui.checks import CheckResult
from novui.config import Settings
from novui.container import ContainerError
from novui.gitinspect import GitError, run_git
from novui.jobrecord import load_job_record
from novui.jobrunner import (
    DraftJobRequest,
    JobRejected,
    RunLockBusy,
    jobs_dir,
    run_draft_job,
)
from novui.locks import RunLock
from novui.procrun import ProcResult
from novui.states import JobState
from novui.workrepo import head_commit
from novui.yamlio import load_yaml


def _init_repo(repo_path: Path) -> None:
    repo_path.mkdir(parents=True, exist_ok=True)
    run_git(repo_path, "init", "-b", "main")
    run_git(repo_path, "-c", "user.name=NovUI Tester", "-c", "user.email=tester@novui.local",
            "commit", "--allow-empty", "-m", "initial commit")

    # Add standard project files and protection targets
    (repo_path / "world").mkdir()
    (repo_path / "world" / "w.md").write_text("# World\nSetting", encoding="utf-8")
    (repo_path / ".gitignore").write_text("ignored.txt\n", encoding="utf-8")
    (repo_path / "chapters" / "ch-001").mkdir(parents=True)
    (repo_path / "chapters" / "ch-001" / "plan.yaml").write_text("title: Test\n", encoding="utf-8")

    run_git(repo_path, "add", "-A")
    run_git(repo_path, "-c", "user.name=NovUI Tester", "-c", "user.email=tester@novui.local",
            "commit", "-m", "setup initial structure")


def _make_settings(tmp_path: Path) -> Settings:
    data_dir = tmp_path / "data"
    worktree_root = data_dir / "worktrees"
    jobhome_root = data_dir / "jobhomes"
    token_path = tmp_path / "fake-token"
    token_path.write_text("fake-oauth-token", encoding="utf-8")
    return Settings(
        data_dir=data_dir,
        worktree_root=worktree_root,
        jobhome_root=jobhome_root,
        agy_image="localhost/novui-spike:agy-1.2.14",
        agy_token_path=token_path,
        timeouts={"agy_draft": 300},
        git_name="NovUI Tester",
        git_email="tester@novui.local",
    )


@pytest.fixture(autouse=True)
def mock_container_meta(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("novui.jobrunner.image_label", lambda img, key: "1.2.14")
    monkeypatch.setattr(
        "novui.jobrunner.image_id",
        lambda img: "sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
    )


def _assert_cleanup_and_valid_record(
    settings: Settings,
    work_key: str,
    job_id: str,
    run_lock: RunLock,
    *,
    expect_record: bool = True,
) -> dict | None:
    # 1. run_lock is released
    assert run_lock.holder(work_key) is None

    # 2. No leftover job home directory
    if settings.jobhome_root.exists():
        entries = [e for e in settings.jobhome_root.iterdir() if e.name.startswith("job-") or e.name.startswith("itest-")]
        assert len(entries) == 0, f"Stale job home found: {entries}"

    # 3. Check record file on disk
    record_file = jobs_dir(settings, work_key) / f"{job_id}.yaml"
    if expect_record:
        assert record_file.is_file(), f"Job record file not found at {record_file}"
        rec = load_job_record(record_file)
        return rec
    else:
        assert not record_file.exists(), f"Job record unexpectedly exists at {record_file}"
        return None


# 1. 正常系
def test_jobrunner_normal_success(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    settings = _make_settings(tmp_path)
    run_lock = RunLock()

    main_before = head_commit(repo, "main")

    def mock_runner(*args, **kwargs) -> ProcResult:
        return ProcResult(
            exit_code=0,
            elapsed_seconds=3.5,
            timed_out=False,
            signal_sent=None,
            group_remaining=False,
            stdout="第1章の草稿本文です。\n主人公が港に降り立った。".encode("utf-8"),
            stderr=b"",
        )

    req = DraftJobRequest(
        work_key="my-work",
        repo=repo,
        chapter_id="ch-001",
        job_id="job-0001",
        model="gemini-3.8-flash-high",
        context_paths=("world/w.md",),
        instruction="第1章を執筆してください。",
    )

    record = run_draft_job(settings, req, run_lock, container_runner=mock_runner)

    assert record["state"] == JobState.COMPLETED.name
    # checks all PASS
    assert all(c["status"] == "PASS" for c in record["checks"])

    # main branch unchanged
    assert head_commit(repo, "main") == main_before

    # work branch committed
    branch_ref = "refs/heads/ai/ch-001/job-0001"
    branch_commit = run_git(repo, "rev-parse", branch_ref).decode("utf-8").strip()
    assert branch_commit != main_before

    # check commit message trailer
    msg = run_git(repo, "log", "-1", "--format=%B", branch_commit).decode("utf-8")
    assert "NovUI-Job: job-0001" in msg

    _assert_cleanup_and_valid_record(settings, req.work_key, req.job_id, run_lock)


# 2. 設定不足 (markers)
def test_jobrunner_undefined_settings_marker(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    settings = _make_settings(tmp_path)
    run_lock = RunLock()

    def mock_runner(*args, **kwargs) -> ProcResult:
        out = "主人公の名前は【要確認：主人公の名前】である。\n旅を始めた。"
        return ProcResult(
            exit_code=0,
            elapsed_seconds=2.0,
            timed_out=False,
            signal_sent=None,
            group_remaining=False,
            stdout=out.encode("utf-8"),
            stderr=b"",
        )

    req = DraftJobRequest(
        work_key="my-work",
        repo=repo,
        chapter_id="ch-001",
        job_id="job-0002",
        model="gemini-3.8-flash-high",
        context_paths=("world/w.md",),
        instruction="第1章を執筆してください。",
    )

    record = run_draft_job(settings, req, run_lock, container_runner=mock_runner)

    assert record["state"] == JobState.WAITING_HUMAN.name
    assert record["history"][-1]["event"] == "NEEDS_HUMAN_INPUT"
    assert "undefined_settings: 1" in record["history"][-1]["reason"]

    # Check work branch has requests.yaml committed
    branch_ref = "refs/heads/ai/ch-001/job-0002"
    req_yaml_str = run_git(repo, "show", f"{branch_ref}:chapters/ch-001/requests.yaml").decode("utf-8")
    import yaml
    req_data = yaml.safe_load(req_yaml_str)
    assert len(req_data) == 1
    assert req_data[0]["kind"] == "undefined_setting"
    assert req_data[0]["message"] == "主人公の名前"

    _assert_cleanup_and_valid_record(settings, req.work_key, req.job_id, run_lock)


# 3. 空出力
def test_jobrunner_empty_output_fails(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    settings = _make_settings(tmp_path)
    run_lock = RunLock()

    def mock_runner(*args, **kwargs) -> ProcResult:
        return ProcResult(
            exit_code=0,
            elapsed_seconds=1.0,
            timed_out=False,
            signal_sent=None,
            group_remaining=False,
            stdout=b"   \n\t  ",
            stderr=b"",
        )

    req = DraftJobRequest(
        work_key="my-work",
        repo=repo,
        chapter_id="ch-001",
        job_id="job-0003",
        model="gemini-3.8-flash-high",
        context_paths=(),
        instruction="第1章を執筆してください。",
    )

    record = run_draft_job(settings, req, run_lock, container_runner=mock_runner)

    assert record["state"] == JobState.FAILED.name
    # cli_output is FAIL
    cli_check = [c for c in record["checks"] if c["name"] == "cli_output"][0]
    assert cli_check["status"] == "FAIL"

    # draft.md not written
    wt_path = Path(record["worktree"])
    assert not (wt_path / "chapters" / "ch-001" / "draft.md").exists()

    _assert_cleanup_and_valid_record(settings, req.work_key, req.job_id, run_lock)


# 4. 許可範囲外の変更
def test_jobrunner_disallowed_paths_tampering(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    settings = _make_settings(tmp_path)
    run_lock = RunLock()

    def mock_runner(*args, **kwargs) -> ProcResult:
        # Simulate unauthorized write in worktree
        wt = settings.worktree_root / "my-work" / "job-0004"
        unauth = wt / "chapters" / "ch-002" / "draft.md"
        unauth.parent.mkdir(parents=True, exist_ok=True)
        unauth.write_text("Unauthorized draft", encoding="utf-8")

        return ProcResult(
            exit_code=0,
            elapsed_seconds=1.0,
            timed_out=False,
            signal_sent=None,
            group_remaining=False,
            stdout=b"Valid draft text.",
            stderr=b"",
        )

    req = DraftJobRequest(
        work_key="my-work",
        repo=repo,
        chapter_id="ch-001",
        job_id="job-0004",
        model="gemini-3.8-flash-high",
        context_paths=(),
        instruction="執筆指示",
    )

    record = run_draft_job(settings, req, run_lock, container_runner=mock_runner)

    assert record["state"] == JobState.FAILED.name
    allowed_check = [c for c in record["checks"] if c["name"] == "allowed_paths"][0]
    assert allowed_check["status"] == "FAIL"

    # Commit should not have been made on branch
    branch_ref = "refs/heads/ai/ch-001/job-0004"
    branch_commit = run_git(repo, "rev-parse", branch_ref).decode("utf-8").strip()
    assert branch_commit == record["base_commit"]

    _assert_cleanup_and_valid_record(settings, req.work_key, req.job_id, run_lock)


# 5. 保護対象の変更
def test_jobrunner_protected_target_tampering(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    settings = _make_settings(tmp_path)
    run_lock = RunLock()

    def mock_runner(*args, **kwargs) -> ProcResult:
        wt = settings.worktree_root / "my-work" / "job-0005"
        (wt / "world" / "w.md").write_text("# Tampered world", encoding="utf-8")
        return ProcResult(
            exit_code=0,
            elapsed_seconds=1.0,
            timed_out=False,
            signal_sent=None,
            group_remaining=False,
            stdout=b"Valid draft text.",
            stderr=b"",
        )

    req = DraftJobRequest(
        work_key="my-work",
        repo=repo,
        chapter_id="ch-001",
        job_id="job-0005",
        model="gemini-3.8-flash-high",
        context_paths=(),
        instruction="執筆指示",
    )

    record = run_draft_job(settings, req, run_lock, container_runner=mock_runner)

    assert record["state"] == JobState.FAILED.name
    allowed_chk = [c for c in record["checks"] if c["name"] == "allowed_paths"][0]
    assert allowed_chk["status"] == "FAIL"
    assert "world/w.md" in allowed_chk["details"]

    _assert_cleanup_and_valid_record(settings, req.work_key, req.job_id, run_lock)


# 6. .git の改変
def test_jobrunner_git_link_tampering(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    settings = _make_settings(tmp_path)
    run_lock = RunLock()

    def mock_runner(*args, **kwargs) -> ProcResult:
        wt = settings.worktree_root / "my-work" / "job-0006"
        git_file = wt / ".git"
        git_file.write_bytes(git_file.read_bytes() + b"\n")
        return ProcResult(
            exit_code=0,
            elapsed_seconds=1.0,
            timed_out=False,
            signal_sent=None,
            group_remaining=False,
            stdout=b"Valid draft text.",
            stderr=b"",
        )

    req = DraftJobRequest(
        work_key="my-work",
        repo=repo,
        chapter_id="ch-001",
        job_id="job-0006",
        model="gemini-3.8-flash-high",
        context_paths=(),
        instruction="執筆指示",
    )

    record = run_draft_job(settings, req, run_lock, container_runner=mock_runner)

    assert record["state"] == JobState.FAILED.name
    hash_chk = [c for c in record["checks"] if c["name"] == "hash_compare"][0]
    assert hash_chk["status"] == "FAIL"

    _assert_cleanup_and_valid_record(settings, req.work_key, req.job_id, run_lock)


# 7. hooks の改変
def test_jobrunner_hooks_tampering(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    settings = _make_settings(tmp_path)
    run_lock = RunLock()

    def mock_runner(*args, **kwargs) -> ProcResult:
        hook_file = repo / ".git" / "hooks" / "pre-commit"
        hook_file.parent.mkdir(parents=True, exist_ok=True)
        hook_file.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        return ProcResult(
            exit_code=0,
            elapsed_seconds=1.0,
            timed_out=False,
            signal_sent=None,
            group_remaining=False,
            stdout=b"Valid draft text.",
            stderr=b"",
        )

    req = DraftJobRequest(
        work_key="my-work",
        repo=repo,
        chapter_id="ch-001",
        job_id="job-0007",
        model="gemini-3.8-flash-high",
        context_paths=(),
        instruction="執筆指示",
    )

    record = run_draft_job(settings, req, run_lock, container_runner=mock_runner)

    assert record["state"] == JobState.FAILED.name
    hash_chk = [c for c in record["checks"] if c["name"] == "hash_compare"][0]
    assert hash_chk["status"] == "FAIL"

    _assert_cleanup_and_valid_record(settings, req.work_key, req.job_id, run_lock)


# 8. ignored の追加
def test_jobrunner_ignored_file_added(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    settings = _make_settings(tmp_path)
    run_lock = RunLock()

    def mock_runner(*args, **kwargs) -> ProcResult:
        wt = settings.worktree_root / "my-work" / "job-0008"
        (wt / "ignored.txt").write_text("sneak ignored content", encoding="utf-8")
        return ProcResult(
            exit_code=0,
            elapsed_seconds=1.0,
            timed_out=False,
            signal_sent=None,
            group_remaining=False,
            stdout=b"Valid draft text.",
            stderr=b"",
        )

    req = DraftJobRequest(
        work_key="my-work",
        repo=repo,
        chapter_id="ch-001",
        job_id="job-0008",
        model="gemini-3.8-flash-high",
        context_paths=(),
        instruction="執筆指示",
    )

    record = run_draft_job(settings, req, run_lock, container_runner=mock_runner)

    assert record["state"] == JobState.FAILED.name
    ign_chk = [c for c in record["checks"] if c["name"] == "ignored_files"][0]
    assert ign_chk["status"] == "FAIL"

    _assert_cleanup_and_valid_record(settings, req.work_key, req.job_id, run_lock)


# 9. timeout
def test_jobrunner_timeout(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    settings = _make_settings(tmp_path)
    run_lock = RunLock()

    def mock_runner(*args, **kwargs) -> ProcResult:
        return ProcResult(
            exit_code=124,
            elapsed_seconds=300.1,
            timed_out=True,
            signal_sent="SIGKILL",
            group_remaining=False,
            stdout=b"",
            stderr=b"Timeout exceeded",
        )

    req = DraftJobRequest(
        work_key="my-work",
        repo=repo,
        chapter_id="ch-001",
        job_id="job-0009",
        model="gemini-3.8-flash-high",
        context_paths=(),
        instruction="執筆指示",
    )

    record = run_draft_job(settings, req, run_lock, container_runner=mock_runner)

    assert record["state"] == JobState.FAILED.name
    assert record["history"][-1]["event"] == "TIMED_OUT"

    _assert_cleanup_and_valid_record(settings, req.work_key, req.job_id, run_lock)


# 10. コード片 (code fence)
def test_jobrunner_code_fence_fails(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    settings = _make_settings(tmp_path)
    run_lock = RunLock()

    def mock_runner(*args, **kwargs) -> ProcResult:
        return ProcResult(
            exit_code=0,
            elapsed_seconds=1.0,
            timed_out=False,
            signal_sent=None,
            group_remaining=False,
            stdout=b"```markdown\n# Code fence content\n```",
            stderr=b"",
        )

    req = DraftJobRequest(
        work_key="my-work",
        repo=repo,
        chapter_id="ch-001",
        job_id="job-0010",
        model="gemini-3.8-flash-high",
        context_paths=(),
        instruction="執筆指示",
    )

    record = run_draft_job(settings, req, run_lock, container_runner=mock_runner)

    assert record["state"] == JobState.FAILED.name
    agy_chk = [c for c in record["checks"] if c["name"] == "agy_output"][0]
    assert agy_chk["status"] == "FAIL"
    assert "code_fence" in agy_chk["details"]

    _assert_cleanup_and_valid_record(settings, req.work_key, req.job_id, run_lock)


# 11. プロンプト超過
def test_jobrunner_prompt_too_large(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    settings = _make_settings(tmp_path)
    run_lock = RunLock()

    huge_instruction = "a" * 120_001

    req = DraftJobRequest(
        work_key="my-work",
        repo=repo,
        chapter_id="ch-001",
        job_id="job-0011",
        model="gemini-3.8-flash-high",
        context_paths=(),
        instruction=huge_instruction,
    )

    record = run_draft_job(settings, req, run_lock)

    assert record["state"] == JobState.WAITING_HUMAN.name
    assert record["history"][-1]["event"] == "NEEDS_HUMAN_INPUT"
    assert "prompt_too_large" in record["history"][-1]["reason"]

    # Worktree and branch must be removed
    wt_dir = settings.worktree_root / "my-work" / "job-0011"
    assert not wt_dir.exists()
    branch_ref = "refs/heads/ai/ch-001/job-0011"
    with pytest.raises(GitError):
        run_git(repo, "rev-parse", "--verify", branch_ref)

    _assert_cleanup_and_valid_record(settings, req.work_key, req.job_id, run_lock)


# 12. 章 lock (unmerged chapter branch exists)
def test_jobrunner_chapter_lock_rejected(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    settings = _make_settings(tmp_path)
    run_lock = RunLock()

    # Create prior branch on same chapter
    run_git(repo, "branch", "ai/ch-001/job-prior")

    req = DraftJobRequest(
        work_key="my-work",
        repo=repo,
        chapter_id="ch-001",
        job_id="job-0012",
        model="gemini-3.8-flash-high",
        context_paths=(),
        instruction="執筆指示",
    )

    with pytest.raises(JobRejected):
        run_draft_job(settings, req, run_lock)

    # Job record must NOT be created
    _assert_cleanup_and_valid_record(settings, req.work_key, req.job_id, run_lock, expect_record=False)


# 13. 実行 lock (run lock busy)
def test_jobrunner_run_lock_busy(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    settings = _make_settings(tmp_path)
    run_lock = RunLock()

    # Pre-acquire run_lock with another job
    assert run_lock.acquire("my-work", "job-busy") is True

    req = DraftJobRequest(
        work_key="my-work",
        repo=repo,
        chapter_id="ch-001",
        job_id="job-0013",
        model="gemini-3.8-flash-high",
        context_paths=(),
        instruction="執筆指示",
    )

    with pytest.raises(RunLockBusy):
        run_draft_job(settings, req, run_lock)

    # Record must remain QUEUED on disk
    rec = load_job_record(jobs_dir(settings, req.work_key) / "job-0013.yaml")
    assert rec["state"] == JobState.QUEUED.name

    # Release manually to leave clean state
    run_lock.release("my-work", "job-busy")
    assert run_lock.holder("my-work") is None


# 14. 予期しない例外 (ContainerError)
def test_jobrunner_unexpected_exception(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    settings = _make_settings(tmp_path)
    run_lock = RunLock()

    def mock_runner(*args, **kwargs) -> ProcResult:
        raise ContainerError("Simulated sudden container failure")

    req = DraftJobRequest(
        work_key="my-work",
        repo=repo,
        chapter_id="ch-001",
        job_id="job-0014",
        model="gemini-3.8-flash-high",
        context_paths=(),
        instruction="執筆指示",
    )

    with pytest.raises(ContainerError, match="Simulated sudden container failure"):
        run_draft_job(settings, req, run_lock, container_runner=mock_runner)

    # State must be transitioned to FAILED and saved
    rec = _assert_cleanup_and_valid_record(settings, req.work_key, req.job_id, run_lock)
    assert rec["state"] == JobState.FAILED.name
