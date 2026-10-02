"""Tests for novui.workrepo module."""

from pathlib import Path
import pytest

from novui.gitinspect import run_git
from novui.locks import can_accept_write_job
from novui.workrepo import (
    CommitGuardError,
    JobWorktree,
    MergeConflict,
    WorkRepoError,
    chapter_branches,
    check_commit_guard,
    commit_all,
    create_job_worktree,
    format_message,
    head_commit,
    merge_job_branch,
    parse_trailers,
    plan_merge,
    remove_job_worktree,
)


def create_test_repo(path: Path) -> Path:
    """Helper to initialize a sample novel git repository."""
    path.mkdir(parents=True, exist_ok=True)
    run_git(path, "init", "-b", "main")
    (path / "README.md").write_text("initial", encoding="utf-8")
    (path / "chapters/ch-001").mkdir(parents=True, exist_ok=True)
    (path / "chapters/ch-001/draft.md").write_text("initial draft", encoding="utf-8")
    (path / "world").mkdir(parents=True, exist_ok=True)
    (path / "world/w.md").write_text("world setting", encoding="utf-8")
    run_git(path, "add", ".")
    run_git(path, "-c", "user.name=test", "-c", "user.email=test@example.invalid", "commit", "-m", "Initial commit")
    return path


def test_format_message_and_parse_trailers() -> None:
    # 1. Round-trip
    trailers = [("NovUI-Job", "job-123"), ("Approval-Id", "A-0001")]
    msg = format_message("Test commit subject", trailers)
    parsed = parse_trailers(msg)
    assert parsed == {"NovUI-Job": ["job-123"], "Approval-Id": ["A-0001"]}

    # 2. No trailers
    msg_simple = format_message("Simple subject", [])
    assert msg_simple == "Simple subject"
    assert parse_trailers(msg_simple) == {}

    # 3. NovUI-Edit values
    msg_edit_typo = format_message("Typo fix", [("NovUI-Edit", "human-typo")])
    assert parse_trailers(msg_edit_typo) == {"NovUI-Edit": ["human-typo"]}

    msg_edit_content = format_message("Content edit", [("NovUI-Edit", "human-content")])
    assert parse_trailers(msg_edit_content) == {"NovUI-Edit": ["human-content"]}

    # 4. Error cases
    with pytest.raises(ValueError):
        format_message("", trailers)  # Empty subject

    with pytest.raises(ValueError):
        format_message("Line 1\nLine 2", trailers)  # Multiline subject

    with pytest.raises(ValueError):
        format_message("Subject", [("Invalid-Key", "job-1")])  # Unknown key

    with pytest.raises(ValueError):
        format_message("Subject", [("NovUI-Job", "bad_job_id")])  # Bad job_id pattern

    with pytest.raises(ValueError):
        format_message("Subject", [("NovUI-Edit", "invalid-edit")])  # Bad edit type

    with pytest.raises(ValueError):
        format_message("Subject", [("Approval-Id", "A-12")])  # Less than 4 digits


def test_check_commit_guard() -> None:
    # 1. 保護対象の変更で trailer なし -> FAIL
    res1 = check_commit_guard(["world/w.md"], [])
    assert res1.status == "FAIL"
    assert res1.details == ("world/w.md",)

    # 2. NovUI-Edit: human-content あり -> PASS
    res2 = check_commit_guard(["world/w.md"], [("NovUI-Edit", "human-content")])
    assert res2.status == "PASS"

    # 3. Approval-Id あり -> PASS
    res3 = check_commit_guard(["world/w.md"], [("Approval-Id", "A-0001")])
    assert res3.status == "PASS"

    # 4. NovUI-Job だけ -> FAIL
    res4 = check_commit_guard(["world/w.md"], [("NovUI-Job", "job-100")])
    assert res4.status == "FAIL"
    assert res4.details == ("world/w.md",)

    # 5. 保護対象なし -> PASS
    res5 = check_commit_guard(["chapters/ch-001/draft.md"], [("NovUI-Job", "job-100")])
    assert res5.status == "PASS"


def test_create_and_remove_job_worktree(tmp_path: Path) -> None:
    repo = create_test_repo(tmp_path / "repo")
    wt_root = tmp_path / "worktrees"

    # 1. Successful creation
    jw = create_job_worktree(repo, wt_root, "work-1", "ch-001", "job-101")
    assert jw.branch == "ai/ch-001/job-101"
    assert jw.path == wt_root / "work-1" / "job-101"
    assert jw.base_commit == head_commit(repo, "main")
    assert jw.path.is_dir()

    # 2. Duplicate creation raises WorkRepoError
    with pytest.raises(WorkRepoError):
        create_job_worktree(repo, wt_root, "work-1", "ch-001", "job-101")

    # 3. chapter_branches & can_accept_write_job
    assert chapter_branches(repo, "ch-001") == ["ai/ch-001/job-101"]
    assert can_accept_write_job(repo, "ch-001") is False

    # 4. Non-main branch in repo raises WorkRepoError
    run_git(repo, "checkout", "-b", "other-branch")
    with pytest.raises(WorkRepoError):
        create_job_worktree(repo, wt_root, "work-1", "ch-001", "job-102")
    run_git(repo, "checkout", "main")

    # 5. Remove worktree and branch
    remove_job_worktree(repo, jw, delete_branch=True)
    assert not jw.path.exists()
    assert can_accept_write_job(repo, "ch-001") is True
    assert chapter_branches(repo, "ch-001") == []


def test_commit_all(tmp_path: Path) -> None:
    repo = create_test_repo(tmp_path / "repo")
    wt_root = tmp_path / "worktrees"
    jw = create_job_worktree(repo, wt_root, "work-1", "ch-001", "job-102")

    # 1. No changes -> None
    res = commit_all(jw.path, "No-op commit", [], name="bot", email="bot@test")
    assert res is None

    # 2. Modify draft.md -> commit success and trailer parsable
    draft_file = jw.path / "chapters/ch-001/draft.md"
    draft_file.write_text("updated draft text", encoding="utf-8")
    commit_hash = commit_all(
        jw.path,
        "Update draft",
        [("NovUI-Job", "job-102")],
        name="bot",
        email="bot@test",
    )
    assert commit_hash is not None
    assert len(commit_hash) == 40

    log_msg = run_git(jw.path, "log", "-1", "--format=%B").decode("utf-8")
    trailers = parse_trailers(log_msg)
    assert trailers == {"NovUI-Job": ["job-102"]}

    # 3. Modify protected path (world/w.md) with NovUI-Job only -> CommitGuardError & clean index
    world_file = jw.path / "world/w.md"
    world_file.write_text("modified world", encoding="utf-8")
    with pytest.raises(CommitGuardError) as exc_info:
        commit_all(jw.path, "Illegal world update", [("NovUI-Job", "job-102")], name="bot", email="bot@test")
    assert "world/w.md" in exc_info.value.paths

    cached_diff = run_git(jw.path, "diff", "--cached").decode("utf-8").strip()
    assert cached_diff == ""  # reset -q successfully cleared index


def test_merge_job_branch_fast_forward(tmp_path: Path) -> None:
    repo = create_test_repo(tmp_path / "repo")
    wt_root = tmp_path / "worktrees"
    jw = create_job_worktree(repo, wt_root, "work-1", "ch-001", "job-103")

    # Modify draft in worktree
    (jw.path / "chapters/ch-001/draft.md").write_text("new draft content", encoding="utf-8")
    commit_all(jw.path, "Job 103 draft", [("NovUI-Job", "job-103")], name="bot", email="bot@test")

    # Plan merge -> fast_forward
    plan = plan_merge(repo, jw)
    assert plan.kind == "fast_forward"
    assert "chapters/ch-001/draft.md" in plan.branch_changed

    # Merge into main
    merge_commit = merge_job_branch(
        repo,
        jw,
        "Merge ch-001 job-103",
        [("NovUI-Job", "job-103")],
        name="bot",
        email="bot@test",
    )
    assert merge_commit == head_commit(repo, "main")

    main_draft = (repo / "chapters/ch-001/draft.md").read_text(encoding="utf-8")
    assert main_draft == "new draft content"

    log_msg = run_git(repo, "log", "-1", "--format=%B").decode("utf-8")
    assert parse_trailers(log_msg) == {"NovUI-Job": ["job-103"]}


def test_merge_job_branch_no_overlap(tmp_path: Path) -> None:
    repo = create_test_repo(tmp_path / "repo")
    wt_root = tmp_path / "worktrees"
    jw = create_job_worktree(repo, wt_root, "work-1", "ch-001", "job-104")

    # 1. Modify ch-001 draft in worktree
    (jw.path / "chapters/ch-001/draft.md").write_text("ch-001 new draft", encoding="utf-8")
    commit_all(jw.path, "Job 104 ch-001", [("NovUI-Job", "job-104")], name="bot", email="bot@test")

    # 2. Modify another file in main (e.g. chapters/ch-002/draft.md)
    (repo / "chapters/ch-002").mkdir(parents=True, exist_ok=True)
    (repo / "chapters/ch-002/draft.md").write_text("ch-002 draft", encoding="utf-8")
    run_git(repo, "add", "chapters/ch-002")
    run_git(repo, "-c", "user.name=test", "-c", "user.email=test@example.invalid", "commit", "-m", "Main update ch-002")

    # 3. Plan merge -> no_overlap
    plan = plan_merge(repo, jw)
    assert plan.kind == "no_overlap"
    assert len(plan.overlap) == 0

    # 4. Merge succeeds and both changes exist in main
    merge_job_branch(
        repo,
        jw,
        "Merge non-overlapping job-104",
        [("NovUI-Job", "job-104")],
        name="bot",
        email="bot@test",
    )
    assert (repo / "chapters/ch-001/draft.md").read_text(encoding="utf-8") == "ch-001 new draft"
    assert (repo / "chapters/ch-002/draft.md").read_text(encoding="utf-8") == "ch-002 draft"


def test_merge_job_branch_overlap_disallowed(tmp_path: Path) -> None:
    repo = create_test_repo(tmp_path / "repo")
    wt_root = tmp_path / "worktrees"
    jw = create_job_worktree(repo, wt_root, "work-1", "ch-001", "job-105")

    # 1. Modify ch-001 draft in worktree
    (jw.path / "chapters/ch-001/draft.md").write_text("worktree draft edit", encoding="utf-8")
    commit_all(jw.path, "Worktree edit", [("NovUI-Job", "job-105")], name="bot", email="bot@test")

    # 2. Modify same file in main
    (repo / "chapters/ch-001/draft.md").write_text("main draft edit", encoding="utf-8")
    run_git(repo, "add", "chapters/ch-001/draft.md")
    run_git(repo, "-c", "user.name=test", "-c", "user.email=test@example.invalid", "commit", "-m", "Main concurrent edit")

    # 3. Plan merge -> overlap
    plan = plan_merge(repo, jw)
    assert plan.kind == "overlap"
    assert "chapters/ch-001/draft.md" in plan.overlap

    main_head_before = head_commit(repo, "main")

    # 4. Merge raises MergeConflict and main HEAD & working tree unchanged
    with pytest.raises(MergeConflict) as exc_info:
        merge_job_branch(
            repo,
            jw,
            "Merge overlapping",
            [("NovUI-Job", "job-105")],
            name="bot",
            email="bot@test",
        )
    assert "chapters/ch-001/draft.md" in exc_info.value.paths
    assert head_commit(repo, "main") == main_head_before
    assert run_git(repo, "status", "--porcelain").decode("utf-8").strip() == ""


def test_merge_job_branch_extra_writes_and_guard(tmp_path: Path) -> None:
    repo = create_test_repo(tmp_path / "repo")
    wt_root = tmp_path / "worktrees"
    jw = create_job_worktree(repo, wt_root, "work-1", "ch-001", "job-106")

    # Worktree commit
    (jw.path / "chapters/ch-001/draft.md").write_text("draft for 106", encoding="utf-8")
    commit_all(jw.path, "Job 106 draft", [("NovUI-Job", "job-106")], name="bot", email="bot@test")

    # 1. extra_writes with chapter.yaml (non-protected) -> included in merge commit
    chapter_content = b"id: ch-001\nstate: DRAFTED\n"
    merge_commit = merge_job_branch(
        repo,
        jw,
        "Merge with chapter metadata",
        [("NovUI-Job", "job-106")],
        name="bot",
        email="bot@test",
        extra_writes={"chapters/ch-001/chapter.yaml": chapter_content},
    )
    assert (repo / "chapters/ch-001/chapter.yaml").read_bytes() == chapter_content

    # 2. extra_writes with protected path (world/w.md) and NovUI-Job only -> CommitGuardError & aborted merge
    jw2 = create_job_worktree(repo, wt_root, "work-1", "ch-001", "job-107")
    (jw2.path / "chapters/ch-001/draft.md").write_text("draft for 107", encoding="utf-8")
    commit_all(jw2.path, "Job 107 draft", [("NovUI-Job", "job-107")], name="bot", email="bot@test")

    main_head_before = head_commit(repo, "main")
    with pytest.raises(CommitGuardError) as exc_info:
        merge_job_branch(
            repo,
            jw2,
            "Illegal extra write merge",
            [("NovUI-Job", "job-107")],
            name="bot",
            email="bot@test",
            extra_writes={"world/w.md": b"illegal world change"},
        )
    assert "world/w.md" in exc_info.value.paths
    assert head_commit(repo, "main") == main_head_before

    # Verify no MERGE_HEAD remains
    assert not (repo / ".git/MERGE_HEAD").exists()
    assert run_git(repo, "status", "--porcelain").decode("utf-8").strip() == ""


def test_merge_job_branch_dirty_working_tree(tmp_path: Path) -> None:
    repo = create_test_repo(tmp_path / "repo")
    wt_root = tmp_path / "worktrees"
    jw = create_job_worktree(repo, wt_root, "work-1", "ch-001", "job-108")

    (jw.path / "chapters/ch-001/draft.md").write_text("draft for 108", encoding="utf-8")
    commit_all(jw.path, "Job 108 draft", [("NovUI-Job", "job-108")], name="bot", email="bot@test")

    # Dirty main working tree with untracked file
    (repo / "dirty_untracked.txt").write_text("dirty", encoding="utf-8")
    with pytest.raises(WorkRepoError, match="Working tree is not clean"):
        merge_job_branch(
            repo,
            jw,
            "Merge with dirty repo",
            [("NovUI-Job", "job-108")],
            name="bot",
            email="bot@test",
        )
