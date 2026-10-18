"""Tests for novui.finalize (final approval, protected change audit, discard)."""

import shutil
import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_stateupdate import (  # noqa: E402
    CH,
    CHAR,
    REG,
    FakeSU,
    _proposed,
    good_su,
    rich_setup,
    run_su,
    summary_out,
)
from test_validatejob import (  # noqa: E402
    GOOD_TEXT,
    _agy_runner,
    _branch_yaml,
    ok_claude,
)

from novui.approvals import build_approval
from novui.chapters import ChapterError, read_chapter_meta
from novui.config import Settings
from novui.draftjob import run_chapter_draft_job
from novui.finalize import (
    FinalizeError,
    audit_protected_changes,
    discard_cycle,
    final_approve,
)
from novui.gitinspect import get_changes, run_git
from novui.jobrecord import apply_transition, load_job_record, new_job_record, now_iso, save_job_record
from novui.jobrunner import jobs_dir
from novui.locks import can_accept_write_job
from novui.proposals import find_proposals, pending_proposal
from novui.states import InvalidTransition, JobEvent
from novui.stateupdate import approve_state
from novui.validatejob import run_validate_job
from novui.workrepo import (
    CommitGuardError,
    JobWorktree,
    MergeConflict,
    chapter_branches,
    commit_all,
    head_commit,
    parse_trailers,
)
from novui.works import WorkInfo
from novui.yamlio import dumps_yaml, load_yaml

MARKER_TEXT = GOOD_TEXT.replace("門番が紋章に目を留めた。", "門番の名は【要確認：門番の名前】だった。").replace(
    "カイは理由が分からないまま門を通った。", "カイは【要確認：通行の許可】を得て門を通った。"
)


@pytest.fixture(autouse=True)
def mock_container_meta(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("novui.jobrunner.image_label", lambda img, key: "1.2.14")
    monkeypatch.setattr(
        "novui.jobrunner.image_id",
        lambda img: "sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
    )


# ---------- 準備 ----------

def approved(tmp_path: Path, *, patches: bool = True) -> tuple[Settings, WorkInfo, dict[str, Any]]:
    """A chapter cycle driven to HUMAN_APPROVED (fake AGY, fake Claude)."""
    settings, work = rich_setup(tmp_path)
    draft = run_chapter_draft_job(settings, work, CH, container_runner=_agy_runner(GOOD_TEXT))
    assert draft["state"] == "COMPLETED"
    val = run_validate_job(settings, work, CH, claude_runner=ok_claude())
    assert val["state"] == "COMPLETED"
    if patches:
        fake = good_su()
    else:
        s = summary_out(foreshadowing=[])
        s["characters"][0]["knowledge_added"] = []
        s["characters"][0]["relationship_changes"] = []
        fake = FakeSU({"summary": [s]})
    su = run_su(settings, work, fake)
    assert su["state"] == "WAITING_HUMAN"
    done = approve_state(settings, work, CH)
    assert done["state"] == "COMPLETED"
    ids = {"draft": draft["job_id"], "validate": val["job_id"], "state_update": su["job_id"]}
    return settings, work, {"draft": draft, "ids": ids, "su": su}


def _jw(ctx: dict[str, Any]) -> JobWorktree:
    d = ctx["draft"]
    return JobWorktree(d["branch"], Path(d["worktree"]), d["base_commit"])


def raw_commit(wt: Path, files: dict[str, str], message: str) -> str:
    """Commit without the commit guard (to build branches that violate the rules)."""
    for rel, text in files.items():
        (wt / rel).parent.mkdir(parents=True, exist_ok=True)
        (wt / rel).write_text(text, encoding="utf-8")
    run_git(wt, "add", "-A")
    run_git(wt, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", message)
    return head_commit(wt, "HEAD")


def main_edit(work: WorkInfo, files: dict[str, str], subject: str = "human edit") -> None:
    for rel, text in files.items():
        (work.path / rel).parent.mkdir(parents=True, exist_ok=True)
        (work.path / rel).write_text(text, encoding="utf-8")
    commit_all(work.path, subject, [("NovUI-Edit", "human-content")], name="t", email="t@t")


def main_state(repo: Path) -> tuple[str, str]:
    return head_commit(repo, "main"), run_git(repo, "status", "--porcelain", "--untracked-files=all").decode()


def _flag_review_required(meta: dict[str, Any]) -> dict[str, Any]:
    meta = dict(meta)
    meta["review_required"] = True
    meta["review_reasons"] = [{"code": "setting_changed", "detail": "設定が変わった", "at": now_iso()}]
    return meta


def marker_draft(tmp_path: Path, text: str = MARKER_TEXT) -> tuple[Settings, WorkInfo, dict[str, Any]]:
    settings, work = rich_setup(tmp_path)
    rec = run_chapter_draft_job(settings, work, CH, container_runner=_agy_runner(text))
    return settings, work, rec


def _record(settings: Settings, work: WorkInfo, job_id: str) -> dict[str, Any]:
    return load_job_record(jobs_dir(settings, work.work_key) / f"{job_id}.yaml")


# ---------- final_approve ----------

def test_final_approve_with_patches(tmp_path: Path) -> None:
    settings, work, ctx = approved(tmp_path)
    repo = work.path
    wt = Path(ctx["draft"]["worktree"])
    branch_char = _branch_yaml(repo, CHAR)
    branch_summary = _branch_yaml(repo, f"chapters/{CH}/summary.yaml")
    assert audit_protected_changes(repo, _jw(ctx)) == []

    commit = final_approve(settings, work, CH)

    assert commit == head_commit(repo, "main")
    parents = run_git(repo, "rev-list", "--parents", "-n", "1", "HEAD").decode().split()
    assert len(parents) == 3  # a merge commit
    message = run_git(repo, "log", "-1", "--format=%B", commit).decode()
    assert message.splitlines()[0] == f"final approve {CH}"
    trailers = parse_trailers(message)
    assert set(trailers["NovUI-Job"]) == set(ctx["ids"].values())
    assert len(trailers["NovUI-Job"]) == len(set(trailers["NovUI-Job"]))
    assert trailers["Approval-Id"] == ["A-0001"]

    meta = read_chapter_meta(repo, CH)
    assert meta["state"] == "FINAL" and meta["last_job"] == ctx["ids"]["state_update"]
    assert load_yaml(repo / CHAR) == branch_char
    assert load_yaml(repo / "chapters" / CH / "summary.yaml") == branch_summary
    assert (repo / ".novui" / "approvals" / "A-0001.yaml").is_file()
    assert (repo / "chapters" / CH / "draft.md").is_file()

    assert chapter_branches(repo, CH) == []
    assert not wt.exists()
    assert can_accept_write_job(repo, CH)
    assert main_state(repo)[1] == ""


def test_final_approve_without_patches(tmp_path: Path) -> None:
    settings, work, ctx = approved(tmp_path, patches=False)
    commit = final_approve(settings, work, CH)
    trailers = parse_trailers(run_git(work.path, "log", "-1", "--format=%B", commit).decode())
    assert "Approval-Id" not in trailers
    assert set(trailers["NovUI-Job"]) == set(ctx["ids"].values())
    assert read_chapter_meta(work.path, CH)["state"] == "FINAL"
    assert chapter_branches(work.path, CH) == []


def test_main_advanced_without_overlap_merges(tmp_path: Path) -> None:
    settings, work, ctx = approved(tmp_path)
    repo = work.path
    main_edit(work, {"world/setting.md": "# 世界観\n\n港町ミナト。追記あり。\n"})
    commit = final_approve(settings, work, CH)
    assert (repo / "world" / "setting.md").read_text(encoding="utf-8").endswith("追記あり。\n")
    assert read_chapter_meta(repo, CH)["state"] == "FINAL"
    assert (repo / ".novui" / "approvals" / "A-0001.yaml").is_file()
    assert commit == head_commit(repo, "main")


def test_overlap_leaves_main_unchanged(tmp_path: Path) -> None:
    settings, work, ctx = approved(tmp_path)
    repo = work.path
    wt = Path(ctx["draft"]["worktree"])
    main_edit(work, {CHAR: dumps_yaml({"id": "C001", "name": "カイ（改）", "speech": {"first_person": "俺"}})})
    before = main_state(repo)
    with pytest.raises(MergeConflict):
        final_approve(settings, work, CH)
    assert main_state(repo) == before
    assert read_chapter_meta(repo, CH)["state"] == "PLAN_APPROVED"
    assert len(chapter_branches(repo, CH)) == 1 and wt.is_dir()
    assert _branch_yaml(repo, f"chapters/{CH}/chapter.yaml")["state"] == "HUMAN_APPROVED"
    assert get_changes(wt) == []


def test_requires_human_approved(tmp_path: Path) -> None:
    settings, work, rec = _proposed(tmp_path)  # AI_VALIDATED with a pending proposal
    before = main_state(work.path)
    with pytest.raises(ChapterError, match="HUMAN_APPROVED"):
        final_approve(settings, work, CH)
    assert main_state(work.path) == before


def test_no_branch(tmp_path: Path) -> None:
    settings, work = rich_setup(tmp_path)
    with pytest.raises(ChapterError, match="no job branch"):
        final_approve(settings, work, CH)


@pytest.mark.parametrize("side", ["main", "branch"])
def test_review_required_blocks_final(tmp_path: Path, side: str) -> None:
    settings, work, ctx = approved(tmp_path)
    repo = work.path
    wt = Path(ctx["draft"]["worktree"])
    rel = f"chapters/{CH}/chapter.yaml"
    if side == "main":
        main_edit(work, {rel: dumps_yaml(_flag_review_required(read_chapter_meta(repo, CH)))})
    else:
        meta = _flag_review_required(load_yaml(wt / rel))
        (wt / rel).write_text(dumps_yaml(meta), encoding="utf-8")
        commit_all(wt, "flag review", [("NovUI-Job", "job-98")], name="t", email="t@t")
    before = main_state(repo)
    with pytest.raises(InvalidTransition):
        final_approve(settings, work, CH)
    assert main_state(repo) == before
    assert len(chapter_branches(repo, CH)) == 1 and wt.is_dir()


def test_waiting_job_on_branch_blocks_final(tmp_path: Path) -> None:
    settings, work, ctx = approved(tmp_path)
    rec = new_job_record("job-99", "validate", chapter_id=CH)
    rec["branch"] = ctx["draft"]["branch"]
    save_job_record(jobs_dir(settings, work.work_key), apply_transition(rec, JobEvent.NEEDS_HUMAN_INPUT, reason="x"))
    before = main_state(work.path)
    with pytest.raises(ChapterError, match="job-99"):
        final_approve(settings, work, CH)
    assert main_state(work.path) == before
    assert len(chapter_branches(work.path, CH)) == 1


def test_dirty_main_is_refused(tmp_path: Path) -> None:
    settings, work, ctx = approved(tmp_path)
    (work.path / "stray.txt").write_text("x\n", encoding="utf-8")
    with pytest.raises(ChapterError, match="uncommitted"):
        final_approve(settings, work, CH)
    assert len(chapter_branches(work.path, CH)) == 1
    (work.path / "stray.txt").unlink()


def test_dirty_worktree_is_refused(tmp_path: Path) -> None:
    settings, work, ctx = approved(tmp_path)
    wt = Path(ctx["draft"]["worktree"])
    (wt / "stray.txt").write_text("x\n", encoding="utf-8")
    before = main_state(work.path)
    with pytest.raises(ChapterError, match="uncommitted"):
        final_approve(settings, work, CH)
    assert main_state(work.path) == before
    (wt / "stray.txt").unlink()


# ---------- 監査 ----------

def _approval_for(ctx: dict[str, Any], approval_id: str, target: str = CHAR) -> str:
    patch = {
        "type": "state_patch", "target": target, "base_hash": "sha256:" + "0" * 64,
        "operations": [{"op": "test", "path": "/id", "value": "C001"}], "reason": "x",
    }
    return dumps_yaml(build_approval(approval_id, job_id=ctx["ids"]["state_update"], patches=[patch]))


def _c002(text: str = "改変") -> dict[str, str]:
    return {"characters/C002.yaml": dumps_yaml({"id": "C002", "name": text, "speech": {"first_person": "私"}})}


def _violate_edit_only(work: WorkInfo, wt: Path, ctx: dict[str, Any]) -> str:
    raw_commit(wt, _c002(), "stray\n\nNovUI-Edit: human-content")
    return "without an Approval-Id trailer"


def _violate_no_record(work: WorkInfo, wt: Path, ctx: dict[str, Any]) -> str:
    raw_commit(wt, _c002(), f"stray\n\nNovUI-Job: {ctx['ids']['state_update']}\nApproval-Id: A-0007")
    return "was not added in the same commit"


def _violate_targets(work: WorkInfo, wt: Path, ctx: dict[str, Any]) -> str:
    files = {".novui/approvals/A-0007.yaml": _approval_for(ctx, "A-0007", CHAR), **_c002()}
    raw_commit(wt, files, f"stray\n\nNovUI-Job: {ctx['ids']['state_update']}\nApproval-Id: A-0007")
    return "characters/C002.yaml is not in the targets"


def _violate_used_on_main(work: WorkInfo, wt: Path, ctx: dict[str, Any]) -> str:
    main_edit(work, {".novui/approvals/A-0001.yaml": "approval_id: A-0001\n"})
    return "already exists on main"


def _violate_record_earlier(work: WorkInfo, wt: Path, ctx: dict[str, Any]) -> str:
    trailers = f"NovUI-Job: {ctx['ids']['state_update']}\nApproval-Id: A-0008"
    raw_commit(wt, {".novui/approvals/A-0008.yaml": _approval_for(ctx, "A-0008", CHAR)}, f"record only\n\n{trailers}")
    raw_commit(wt, _c002(), f"later change\n\n{trailers}")
    return "was not added in the same commit"


def _violate_merge_commit(work: WorkInfo, wt: Path, ctx: dict[str, Any]) -> str:
    run_git(wt, "checkout", "-q", "-b", "side")
    raw_commit(wt, {"chapters/ch-001/side.txt": "x\n"}, "side\n\nNovUI-Job: job-9")
    run_git(wt, "checkout", "-q", ctx["draft"]["branch"])
    raw_commit(wt, {"chapters/ch-001/main.txt": "y\n"}, "main side\n\nNovUI-Job: job-9")
    run_git(wt, "-c", "user.name=t", "-c", "user.email=t@t", "merge", "-q", "--no-ff", "-m", "merge side", "side")
    return "merge commit"


@pytest.mark.parametrize("violate", [
    _violate_edit_only, _violate_no_record, _violate_targets, _violate_used_on_main,
    _violate_record_earlier, _violate_merge_commit,
])
def test_audit_violation_blocks_final(tmp_path: Path, violate: Any) -> None:
    settings, work, ctx = approved(tmp_path)
    repo = work.path
    wt = Path(ctx["draft"]["worktree"])
    expected = violate(work, wt, ctx)

    violations = audit_protected_changes(repo, _jw(ctx))
    assert violations and any(expected in v for v in violations), violations

    before = main_state(repo)
    with pytest.raises(FinalizeError, match="audit failed"):
        final_approve(settings, work, CH)
    assert main_state(repo) == before
    assert len(chapter_branches(repo, CH)) == 1 and wt.is_dir()


def test_merge_guard_failure_leaves_main_unchanged(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Even if the audit were bypassed, the existing commit guard of merge_job_branch stops the merge."""
    settings, work, ctx = approved(tmp_path, patches=False)  # no Approval-Id on this branch
    repo = work.path
    wt = Path(ctx["draft"]["worktree"])
    raw_commit(wt, _c002(), "stray\n\nNovUI-Edit: human-content")
    assert audit_protected_changes(repo, _jw(ctx))  # the real audit sees it

    monkeypatch.setattr("novui.finalize.audit_protected_changes", lambda repo, jw: [])
    before = main_state(repo)
    with pytest.raises(CommitGuardError):
        final_approve(settings, work, CH)
    assert main_state(repo) == before
    assert read_chapter_meta(repo, CH)["state"] == "PLAN_APPROVED"
    assert len(chapter_branches(repo, CH)) == 1 and wt.is_dir()


# ---------- 後始末と再実行 ----------

def test_cleanup_failure_then_rerun_repeats_only_cleanup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from novui.gitinspect import GitError

    settings, work, ctx = approved(tmp_path)
    repo = work.path
    wt = Path(ctx["draft"]["worktree"])

    def boom(repo_: Path, jw: JobWorktree) -> None:
        raise GitError("cannot remove")

    with monkeypatch.context() as m:
        m.setattr("novui.finalize.remove_worktree_and_branch", boom)
        with pytest.raises(FinalizeError, match="run final-approve again"):
            final_approve(settings, work, CH)

    merged = head_commit(repo, "main")
    assert read_chapter_meta(repo, CH)["state"] == "FINAL"
    assert len(chapter_branches(repo, CH)) == 1 and wt.is_dir()
    assert not can_accept_write_job(repo, CH)

    again = final_approve(settings, work, CH)  # cleanup only: no new commit
    assert again == merged == head_commit(repo, "main")
    assert chapter_branches(repo, CH) == [] and not wt.exists()
    assert can_accept_write_job(repo, CH)


# ---------- Job 記録との照合（D2） ----------

@pytest.mark.parametrize("field,value", [("worktree", "/tmp/elsewhere/job-x"), ("branch", "ai/ch-001/job-77")])
@pytest.mark.parametrize("operation", ["final", "discard"])
def test_record_mismatch_stops_and_deletes_nothing(tmp_path: Path, field: str, value: str, operation: str) -> None:
    settings, work, ctx = approved(tmp_path)
    repo = work.path
    wt = Path(ctx["draft"]["worktree"])
    rec = _record(settings, work, ctx["ids"]["draft"])
    rec[field] = value
    save_job_record(jobs_dir(settings, work.work_key), rec)
    before = main_state(repo)

    with pytest.raises(ChapterError, match="does not match its draft job record"):
        if operation == "final":
            final_approve(settings, work, CH)
        else:
            discard_cycle(settings, work, CH, force=True)

    assert main_state(repo) == before
    assert len(chapter_branches(repo, CH)) == 1 and wt.is_dir()


def test_missing_draft_record_stops(tmp_path: Path) -> None:
    settings, work, ctx = approved(tmp_path)
    (jobs_dir(settings, work.work_key) / f"{ctx['ids']['draft']}.yaml").unlink()
    with pytest.raises(ChapterError, match="draft job record not found"):
        discard_cycle(settings, work, CH, force=True)
    assert len(chapter_branches(work.path, CH)) == 1


# ---------- discard_cycle ----------

def test_discard_marker_draft(tmp_path: Path) -> None:
    settings, work, rec = marker_draft(tmp_path)
    assert rec["state"] == "WAITING_HUMAN"
    repo = work.path
    wt = Path(rec["worktree"])
    before = main_state(repo)

    discard_cycle(settings, work, CH)

    assert chapter_branches(repo, CH) == [] and not wt.exists()
    assert main_state(repo) == before
    assert read_chapter_meta(repo, CH)["state"] == "PLAN_APPROVED"
    assert can_accept_write_job(repo, CH)
    done = _record(settings, work, rec["job_id"])
    assert done["state"] == "CANCELLED" and done["history"][-1]["reason"] == "discarded"
    with pytest.raises(ChapterError, match="no job branch"):
        discard_cycle(settings, work, CH)


def test_discard_failed_draft_keeps_history(tmp_path: Path) -> None:
    settings, work = rich_setup(tmp_path)
    rec = run_chapter_draft_job(settings, work, CH, container_runner=_agy_runner(""))
    assert rec["state"] == "FAILED"
    assert len(chapter_branches(work.path, CH)) == 1  # the dead end: a failed draft keeps its branch
    assert not can_accept_write_job(work.path, CH)

    discard_cycle(settings, work, CH)

    assert chapter_branches(work.path, CH) == [] and not Path(rec["worktree"]).exists()
    assert _record(settings, work, rec["job_id"])["state"] == "FAILED"  # history is kept
    again = run_chapter_draft_job(settings, work, CH, container_runner=_agy_runner(GOOD_TEXT))
    assert again["state"] == "COMPLETED"


def test_discard_human_approved_needs_force(tmp_path: Path) -> None:
    settings, work, ctx = approved(tmp_path)
    repo = work.path
    wt = Path(ctx["draft"]["worktree"])
    before = main_state(repo)
    with pytest.raises(ChapterError, match="force"):
        discard_cycle(settings, work, CH)
    assert len(chapter_branches(repo, CH)) == 1 and wt.is_dir()

    discard_cycle(settings, work, CH, force=True)
    assert chapter_branches(repo, CH) == [] and not wt.exists()
    assert main_state(repo) == before
    assert read_chapter_meta(repo, CH)["state"] == "PLAN_APPROVED"
    # finished Jobs and the approved proposal stay as they are
    assert _record(settings, work, ctx["ids"]["state_update"])["state"] == "COMPLETED"
    assert find_proposals(settings, work.work_key, CH)[-1]["status"] == "approved"


def test_discard_pending_proposal_and_waiting_jobs(tmp_path: Path) -> None:
    settings, work, su = _proposed(tmp_path)
    repo = work.path
    assert pending_proposal(settings, work.work_key, CH) is not None
    discard_cycle(settings, work, CH)  # AI_VALIDATED: no force needed
    assert chapter_branches(repo, CH) == []
    done = _record(settings, work, su["job_id"])
    assert done["state"] == "CANCELLED" and done["history"][-1]["reason"] == "discarded"
    stored = find_proposals(settings, work.work_key, CH)[-1]
    assert stored["status"] == "rejected" and stored["status_reason"] == "discarded"
    assert pending_proposal(settings, work.work_key, CH) is None


def test_discard_cancels_only_waiting_jobs_of_this_branch(tmp_path: Path) -> None:
    settings, work, rec = marker_draft(tmp_path)
    jdir = jobs_dir(settings, work.work_key)
    other = new_job_record("job-90", "validate", chapter_id="ch-002")
    other["branch"] = "ai/ch-002/job-5"
    save_job_record(jdir, apply_transition(other, JobEvent.NEEDS_HUMAN_INPUT, reason="elsewhere"))
    stopped = new_job_record("job-91", "validate", chapter_id=CH)
    stopped["branch"] = rec["branch"]
    stopped = apply_transition(apply_transition(stopped, JobEvent.LOCK_ACQUIRED), JobEvent.HUMAN_STOP)
    save_job_record(jdir, stopped)

    discard_cycle(settings, work, CH)

    assert _record(settings, work, "job-90")["state"] == "WAITING_HUMAN"  # another branch: untouched
    assert _record(settings, work, "job-91")["state"] == "STOPPED"  # history is kept
    assert _record(settings, work, rec["job_id"])["state"] == "CANCELLED"


def test_discard_with_missing_worktree_directory(tmp_path: Path) -> None:
    settings, work, rec = marker_draft(tmp_path)
    shutil.rmtree(rec["worktree"])
    discard_cycle(settings, work, CH)
    assert chapter_branches(work.path, CH) == []
    assert can_accept_write_job(work.path, CH)
    assert "ai/ch-001" not in run_git(work.path, "worktree", "list", "--porcelain").decode()


def test_discard_refuses_dirty_main(tmp_path: Path) -> None:
    settings, work, rec = marker_draft(tmp_path)
    (work.path / "stray.txt").write_text("x\n", encoding="utf-8")
    with pytest.raises(ChapterError, match="uncommitted"):
        discard_cycle(settings, work, CH)
    assert len(chapter_branches(work.path, CH)) == 1
    (work.path / "stray.txt").unlink()
