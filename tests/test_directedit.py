"""Tests for Phase 3-C: direct edit, reopen, and impact analysis job."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from novui.chapters import (
    ChapterError,
    add_chapter,
    clear_review_required,
    read_chapter_meta,
    read_chapters_order,
    review_marked,
    transition_on_main,
)
from novui.config import Settings
from novui.directedit import direct_edit, reopen_chapter
from novui.finalize import final_approve
from novui.gitinspect import run_git
from novui.impactjob import run_impact_job
from novui.jobrecord import load_job_record
from novui.jobrunner import jobs_dir
from novui.planjob import approve_plan
from novui.procrun import ProcResult
from novui.proposals import pending_proposal
from novui.states import ChapterEvent, ChapterState, JobState
from novui.stateupdate import approve_state, run_state_update
from novui.validatejob import (
    find_chapter_branch,
    run_validate_job,
    skip_validation,
)
from novui.workrepo import chapter_branches, commit_all, head_commit
from novui.works import WorkInfo
from novui.yamlio import dumps_yaml, load_yaml, write_yaml_atomic

from test_draftjob import make_plan
from test_validatejob import CH, GOOD_TEXT, envelope, setup_work, _agy_runner


@pytest.fixture(autouse=True)
def mock_container_meta(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("novui.jobrunner.image_label", lambda img, key: "1.2.14")
    monkeypatch.setattr(
        "novui.jobrunner.image_id",
        lambda img: "sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
    )


class FakeClaude:
    def __init__(self, responses: list[Any] | None = None) -> None:
        self.responses = list(responses) if responses else []
        self.prompts: list[str] = []

    def __call__(self, **kwargs: Any) -> ProcResult:
        self.prompts.append(kwargs["prompt"])
        if not self.responses:
            raise RuntimeError(f"FakeClaude ran out of responses! Last prompt: {kwargs.get('prompt')[:200]}")
        resp = self.responses.pop(0)
        body = resp if isinstance(resp, bytes) else envelope(resp)
        return ProcResult(0, 1.0, False, None, False, body, b"")


def setup_novel(tmp_path: Path) -> tuple[Settings, WorkInfo]:
    """Setup a work with C001, C002, and registry with F001."""
    settings, work = setup_work(tmp_path)
    repo = work.path
    c1 = {
        "id": "C001",
        "name": "カイ",
        "personality": ["冷静"],
        "speech": {"first_person": "俺", "forbidden": ["僕"]},
        "relationships": [{"with": "C002", "state": "仲間"}],
        "knowledge": [{"id": "K001", "fact": "既知の事実", "source_chapter": "ch-001"}],
    }
    (repo / "characters" / "C001.yaml").write_text(dumps_yaml(c1), encoding="utf-8")
    c2 = {
        "id": "C002",
        "name": "ミサキ",
        "personality": ["明るい"],
        "speech": {"first_person": "私"},
    }
    (repo / "characters" / "C002.yaml").write_text(dumps_yaml(c2), encoding="utf-8")
    commit_all(repo, "setup characters", [("NovUI-Edit", "human-content")], name="Tester", email="tester@test")
    return settings, work


def setup_final_chapter(settings: Settings, work: WorkInfo, cid: str = "ch-001") -> None:
    """Setup a chapter that has reached FINAL on main."""
    repo = work.path
    if cid not in read_chapters_order(repo):
        add_chapter(settings, work, cid, f"章 {cid}", f"{cid}のアウトライン\n")
        plan = make_plan(cid)
        transition_on_main(
            settings, work, cid, ChapterEvent.PLAN_WRITTEN,
            subject=f"plan {cid}",
            trailers=[("NovUI-Job", "job-1")],
            extra_files={f"chapters/{cid}/plan.yaml": dumps_yaml(plan).encode("utf-8")},
            last_job="job-1",
        )
        approve_plan(settings, work, cid)

    # Draft content & summary
    draft_text = GOOD_TEXT
    summary = {
        "chapter_id": cid,
        "summary": f"{cid}のあらすじ要約",
        "characters": [
            {"id": "C001", "state": "通常", "new_knowledge": ["K001"]},
        ],
        "foreshadowing": [],
        "active_flags": [],
    }

    # Transition chapter to FINAL on main
    transition_on_main(
        settings, work, cid, ChapterEvent.DRAFT_COMPLETED,
        subject=f"draft {cid}",
        trailers=[("NovUI-Job", "job-10")],
        extra_files={f"chapters/{cid}/draft.md": draft_text.encode("utf-8")},
        last_job="job-10",
    )
    transition_on_main(
        settings, work, cid, ChapterEvent.VALIDATION_SKIPPED,
        subject=f"validation skip {cid}",
        trailers=[("NovUI-Job", "job-11")],
        last_job="job-11",
    )
    transition_on_main(
        settings, work, cid, ChapterEvent.STATE_PATCH_APPROVED,
        subject=f"state patch {cid}",
        trailers=[("NovUI-Job", "job-12")],
        extra_files={f"chapters/{cid}/summary.yaml": dumps_yaml(summary).encode("utf-8")},
        last_job="job-12",
    )
    transition_on_main(
        settings, work, cid, ChapterEvent.FINAL_APPROVED,
        subject=f"final approve {cid}",
        trailers=[("NovUI-Job", "job-13")],
        last_job="job-13",
    )


# ==============================================================================
# 3-C1: run_impact_job tests
# ==============================================================================

def test_run_impact_job_no_protected_change(tmp_path: Path) -> None:
    settings, work = setup_novel(tmp_path)
    repo = work.path
    setup_final_chapter(settings, work, "ch-001")
    base = head_commit(repo, "main")
    # Commit non-protected change (draft.md)
    draft_p = repo / "chapters/ch-001/draft.md"
    draft_p.write_text(draft_p.read_text(encoding="utf-8").replace("門を通った。", "門を静かに通った。"), encoding="utf-8")
    commit_all(repo, "edit draft ch-001", [("NovUI-Edit", "human-typo")], name="Tester", email="tester@test")
    head = head_commit(repo, "main")

    with pytest.raises(ValueError, match="no protected change between"):
        run_impact_job(settings, work, base=base, head=head, reason="test")


def test_run_impact_job_base_not_ancestor(tmp_path: Path) -> None:
    settings, work = setup_novel(tmp_path)
    repo = work.path
    head = head_commit(repo, "main")
    bogus_base = "0123456789abcdef0123456789abcdef01234567"

    with pytest.raises(ValueError, match="is not an ancestor"):
        run_impact_job(settings, work, base=bogus_base, head=head, reason="test")


def test_run_impact_job_head_not_main_head(tmp_path: Path) -> None:
    settings, work = setup_novel(tmp_path)
    repo = work.path
    base = head_commit(repo, "main")
    # Modify a protected file
    c1 = load_yaml(repo / "characters" / "C001.yaml")
    c1["personality"] = ["情熱的"]
    write_yaml_atomic(repo / "characters" / "C001.yaml", c1)
    head = commit_all(repo, "change C001", [("NovUI-Edit", "human-content")], name="Tester", email="tester@test")
    assert head is not None

    # Make another commit on main
    add_chapter(settings, work, "ch-002", "第二章", "アウトライン\n")

    # head is no longer main HEAD
    with pytest.raises(ValueError, match="is not main HEAD"):
        run_impact_job(settings, work, base=base, head=head, reason="test")


def test_run_impact_job_success_with_affected_and_notes(tmp_path: Path) -> None:
    settings, work = setup_novel(tmp_path)
    repo = work.path
    setup_final_chapter(settings, work, "ch-001")
    base = head_commit(repo, "main")

    # Modify protected character
    c1 = load_yaml(repo / "characters" / "C001.yaml")
    c1["personality"] = ["情熱的"]
    write_yaml_atomic(repo / "characters" / "C001.yaml", c1)
    head = commit_all(repo, "change C001", [("NovUI-Edit", "human-content")], name="Tester", email="tester@test")
    assert head is not None

    impact_resp = {
        "type": "impact_analysis",
        "affected": [{"chapter_id": "ch-001", "reason": "カイの性格変更による影響"}],
        "notes": ["第1章での台詞口調を確認すること"],
    }
    fake = FakeClaude([impact_resp])
    rec, affected, notes = run_impact_job(
        settings, work, base=base, head=head, reason="性格変更", claude_runner=fake
    )

    assert rec["state"] == JobState.COMPLETED.name
    assert len(affected) == 1
    assert affected[0]["chapter_id"] == "ch-001"
    assert notes == ["第1章での台詞口調を確認すること"]

    # Verify review_required on ch-001
    meta = read_chapter_meta(repo, "ch-001")
    assert meta is not None
    assert meta["review_required"] is True
    reasons = meta["review_reasons"]
    assert any(r["code"] == "setting_changed" and "性格変更" in r["detail"] for r in reasons)

    # Verify checks in job record
    chk_names = {c["name"] for c in rec["checks"]}
    assert "impact_affected" in chk_names
    assert "impact_notes" in chk_names


# ==============================================================================
# 3-C2: direct_edit tests
# ==============================================================================

def test_direct_edit_typo(tmp_path: Path) -> None:
    settings, work = setup_novel(tmp_path)
    repo = work.path
    setup_final_chapter(settings, work, "ch-001")

    # Fix typo in draft.md
    draft_p = repo / "chapters/ch-001/draft.md"
    orig_text = draft_p.read_text(encoding="utf-8")
    new_text = orig_text.replace("港町ミナト", "港町みなと")
    draft_p.write_text(new_text, encoding="utf-8")

    res = direct_edit(settings, work, kind="typo", note="誤字修正")
    assert res["commit"] is not None
    assert res["paths"] == ["chapters/ch-001/draft.md"]
    assert res["chapters_drafted"] == []
    assert res["chapters_replanned"] == []
    assert res["review_marked"] == []

    # Chapter state should still be FINAL and review_required False
    meta = read_chapter_meta(repo, "ch-001")
    assert meta is not None
    assert meta["state"] == ChapterState.FINAL.name
    assert meta["review_required"] is False

    # Check commit trailer
    log_msg = run_git(repo, "log", "-1", "--format=%B").decode("utf-8")
    assert "NovUI-Edit: human-typo" in log_msg
    assert "human edit (typo): 誤字修正" in log_msg


def test_direct_edit_typo_with_protected_and_impact(tmp_path: Path) -> None:
    settings, work = setup_novel(tmp_path)
    repo = work.path
    setup_final_chapter(settings, work, "ch-001")

    # Fix typo in character yaml
    c1_p = repo / "characters/C001.yaml"
    c1 = load_yaml(c1_p)
    c1["personality"] = ["極めて冷静"]
    write_yaml_atomic(c1_p, c1)

    impact_resp = {
        "type": "impact_analysis",
        "affected": [{"chapter_id": "ch-001", "reason": "性格強調"}],
        "notes": [],
    }
    fake = FakeClaude([impact_resp])
    res = direct_edit(settings, work, kind="typo", note="誤字", run_impact=True, claude_runner=fake)
    assert res["impact_job"] is not None
    assert res["impact_error"] is None

    # With --no-impact (run_impact=False)
    c2_p = repo / "characters/C002.yaml"
    c2 = load_yaml(c2_p)
    c2["personality"] = ["極めて明るい"]
    write_yaml_atomic(c2_p, c2)

    res2 = direct_edit(settings, work, kind="typo", note="誤字2", run_impact=False)
    assert res2["impact_job"] is None
    assert res2["impact_error"] is None


def test_direct_edit_content_draft(tmp_path: Path) -> None:
    settings, work = setup_novel(tmp_path)
    repo = work.path
    setup_final_chapter(settings, work, "ch-001")
    setup_final_chapter(settings, work, "ch-002")

    # Edit draft.md of ch-001 (content change)
    draft_p = repo / "chapters/ch-001/draft.md"
    orig_text = draft_p.read_text(encoding="utf-8")
    new_text = orig_text.replace("門を通った。", "門を通った。新しい展開の追加。")
    draft_p.write_text(new_text, encoding="utf-8")

    res = direct_edit(settings, work, kind="content", note="本文内容変更")
    assert res["chapters_drafted"] == ["ch-001"]
    assert res["review_marked"] == ["ch-002"]

    # ch-001 is DRAFTED and has review_required
    meta1 = read_chapter_meta(repo, "ch-001")
    assert meta1 is not None
    assert meta1["state"] == ChapterState.DRAFTED.name
    assert meta1["review_required"] is True
    assert any(r["code"] == "previous_chapter_changed" and "ch-001 content edited" in r["detail"] for r in meta1["review_reasons"])

    # ch-002 (subsequent chapter without branch) has review_required in the same commit
    meta2 = read_chapter_meta(repo, "ch-002")
    assert meta2 is not None
    assert meta2["state"] == ChapterState.FINAL.name
    assert meta2["review_required"] is True
    assert any(r["code"] == "previous_chapter_changed" and "ch-001 content edited" in r["detail"] for r in meta2["review_reasons"])


def test_direct_edit_content_draft_subsequent_with_branch(tmp_path: Path) -> None:
    settings, work = setup_novel(tmp_path)
    repo = work.path
    setup_final_chapter(settings, work, "ch-001")

    # ch-002 is on a job branch (e.g. after draft or plan_approved)
    add_chapter(settings, work, "ch-002", "第二章", "第二章アウトライン\n")
    plan2 = make_plan("ch-002")
    transition_on_main(
        settings, work, "ch-002", ChapterEvent.PLAN_WRITTEN,
        subject="plan ch-002",
        trailers=[("NovUI-Job", "job-2")],
        extra_files={"chapters/ch-002/plan.yaml": dumps_yaml(plan2).encode("utf-8")},
        last_job="job-2",
    )
    approve_plan(settings, work, "ch-002")

    from novui.draftjob import run_chapter_draft_job
    rec = run_chapter_draft_job(settings, work, "ch-002", container_runner=_agy_runner(GOOD_TEXT))
    assert rec["state"] == JobState.COMPLETED.name
    assert chapter_branches(repo, "ch-002")

    # Now edit ch-001 draft.md on main
    draft1 = repo / "chapters/ch-001/draft.md"
    draft1.write_text(draft1.read_text(encoding="utf-8").replace("門を通った。", "門を通った。展開変更。"), encoding="utf-8")

    res = direct_edit(settings, work, kind="content", note="展開変更")
    assert res["chapters_drafted"] == ["ch-001"]
    assert res["review_marked"] == ["ch-002"]

    # ch-002 review_required was marked on branch, not on main
    meta2_main = read_chapter_meta(repo, "ch-002")
    assert meta2_main is not None
    assert meta2_main["review_required"] is False

    from novui.validatejob import _read_branch_chapter_meta
    branch2 = chapter_branches(repo, "ch-002")[0]
    meta2_branch = _read_branch_chapter_meta(repo, branch2, "ch-002")
    assert meta2_branch["review_required"] is True


def test_direct_edit_content_plan(tmp_path: Path) -> None:
    settings, work = setup_novel(tmp_path)
    repo = work.path
    setup_final_chapter(settings, work, "ch-001")

    # ch-002 in PLAN_APPROVED state
    add_chapter(settings, work, "ch-002", "第二章", "第二章アウトライン\n")
    plan2 = make_plan("ch-002")
    transition_on_main(
        settings, work, "ch-002", ChapterEvent.PLAN_WRITTEN,
        subject="plan ch-002",
        trailers=[("NovUI-Job", "job-2")],
        extra_files={"chapters/ch-002/plan.yaml": dumps_yaml(plan2).encode("utf-8")},
        last_job="job-2",
    )
    approve_plan(settings, work, "ch-002")
    assert read_chapter_meta(repo, "ch-002")["state"] == ChapterState.PLAN_APPROVED.name

    # Edit plan.yaml of ch-002 -> should return to PLANNED
    plan2_doc = load_yaml(repo / "chapters/ch-002/plan.yaml")
    plan2_doc["pov"] = "一人称視点"
    write_yaml_atomic(repo / "chapters/ch-002/plan.yaml", plan2_doc)

    res = direct_edit(settings, work, kind="content", note="plan改訂")
    assert res["chapters_replanned"] == ["ch-002"]
    assert read_chapter_meta(repo, "ch-002")["state"] == ChapterState.PLANNED.name

    # Edit plan.yaml of FINAL ch-001 -> state unchanged, subsequent chapter marked review_required
    plan1_doc = load_yaml(repo / "chapters/ch-001/plan.yaml")
    plan1_doc["pov"] = "一人称視点"
    write_yaml_atomic(repo / "chapters/ch-001/plan.yaml", plan1_doc)

    res2 = direct_edit(settings, work, kind="content", note="ch-001 plan改訂")
    assert res2["chapters_replanned"] == []
    assert read_chapter_meta(repo, "ch-001")["state"] == ChapterState.FINAL.name
    assert res2["review_marked"] == ["ch-002"]


def test_direct_edit_refused(tmp_path: Path) -> None:
    settings, work = setup_novel(tmp_path)
    repo = work.path
    setup_final_chapter(settings, work, "ch-001")

    # 1. Disallowed file (e.g. summary.yaml)
    (repo / "chapters/ch-001/summary.yaml").write_text("dummy", encoding="utf-8")
    with pytest.raises(ChapterError, match="Direct edit disallowed"):
        direct_edit(settings, work, kind="typo")
    run_git(repo, "checkout", "--", "chapters/ch-001/summary.yaml")

    # 2. File deletion
    run_git(repo, "rm", "chapters/ch-001/draft.md")
    with pytest.raises(ChapterError, match="Direct edit does not allow deleting files"):
        direct_edit(settings, work, kind="typo")
    run_git(repo, "checkout", "HEAD", "--", "chapters/ch-001/draft.md")

    # 3. Chapter with job branch
    add_chapter(settings, work, "ch-002", "第二章", "第二章アウトライン\n")
    plan2 = make_plan("ch-002")
    transition_on_main(
        settings, work, "ch-002", ChapterEvent.PLAN_WRITTEN,
        subject="plan ch-002",
        trailers=[("NovUI-Job", "job-2")],
        extra_files={"chapters/ch-002/plan.yaml": dumps_yaml(plan2).encode("utf-8")},
        last_job="job-2",
    )
    approve_plan(settings, work, "ch-002")
    from novui.draftjob import run_chapter_draft_job
    run_chapter_draft_job(settings, work, "ch-002", container_runner=_agy_runner(GOOD_TEXT))

    (repo / "chapters/ch-002/draft.md").write_text("change", encoding="utf-8")
    with pytest.raises(ChapterError, match="Direct edit disallowed"):
        direct_edit(settings, work, kind="content")
    (repo / "chapters/ch-002/draft.md").unlink(missing_ok=True)

    # 4. Schema violation in character
    c1 = load_yaml(repo / "characters/C001.yaml")
    c1["invalid_field"] = "bad"
    write_yaml_atomic(repo / "characters/C001.yaml", c1)
    with pytest.raises(ChapterError, match="Content validation failed"):
        direct_edit(settings, work, kind="content")
    run_git(repo, "checkout", "--", "characters/C001.yaml")

    # 5. Scene marker broken in draft.md
    draft_p = repo / "chapters/ch-001/draft.md"
    orig_text = draft_p.read_text(encoding="utf-8")
    broken_text = orig_text.replace("<!-- scene: S1 -->", "<!-- broken marker -->")
    draft_p.write_text(broken_text, encoding="utf-8")
    with pytest.raises(ChapterError, match="Scene markers check failed"):
        direct_edit(settings, work, kind="content")
    run_git(repo, "checkout", "--", "chapters/ch-001/draft.md")

    # 6. Nothing to edit
    with pytest.raises(ChapterError, match="nothing to edit"):
        direct_edit(settings, work, kind="typo")


def test_direct_edit_impact_failure_and_subsequent_analyze(tmp_path: Path) -> None:
    settings, work = setup_novel(tmp_path)
    repo = work.path
    setup_final_chapter(settings, work, "ch-001")

    # Edit character C001
    c1 = load_yaml(repo / "characters/C001.yaml")
    c1["personality"] = ["非常に情熱的"]
    write_yaml_atomic(repo / "characters/C001.yaml", c1)

    # Claude fails (returns non-zero exit code or timeout)
    def fail_claude(**kwargs: Any) -> ProcResult:
        return ProcResult(1, 1.0, False, None, False, b"", b"error")

    res = direct_edit(settings, work, kind="content", note="性格変更", claude_runner=fail_claude)
    assert res["commit"] is not None
    assert res["impact_error"] is not None

    # Now rerun with analyze-impact via run_impact_job successfully
    parents_out = run_git(repo, "rev-list", "--parents", "-n", "1", res["commit"]).decode("utf-8").strip()
    parent = parents_out.split()[1]

    impact_resp = {
        "type": "impact_analysis",
        "affected": [{"chapter_id": "ch-001", "reason": "性格変更影響"}],
        "notes": [],
    }
    fake = FakeClaude([impact_resp])
    rec, affected, notes = run_impact_job(
        settings, work, base=parent, head=res["commit"], reason="性格変更", claude_runner=fake
    )
    assert rec["state"] == JobState.COMPLETED.name
    meta = read_chapter_meta(repo, "ch-001")
    assert meta is not None
    assert meta["review_required"] is True


# ==============================================================================
# 3-C3: reopen test (full cycle)
# ==============================================================================

def test_reopen_full_cycle(tmp_path: Path) -> None:
    """Test full cycle: edit content on FINAL -> DRAFTED -> reopen -> skip-validation -> state-update -> approve-state -> review-done -> final-approve."""
    settings, work = setup_novel(tmp_path)
    repo = work.path
    setup_final_chapter(settings, work, "ch-001")

    # 1. Edit draft.md of FINAL ch-001
    draft_p = repo / "chapters/ch-001/draft.md"
    orig_text = draft_p.read_text(encoding="utf-8")
    new_text = orig_text.replace("門を通った。", "門を通った。新しいエピソードの追加。")
    draft_p.write_text(new_text, encoding="utf-8")

    res = direct_edit(settings, work, kind="content", note="新しいエピソード追加")
    assert res["chapters_drafted"] == ["ch-001"]
    assert read_chapter_meta(repo, "ch-001")["state"] == ChapterState.DRAFTED.name

    # 2. Reopen chapter
    rec = reopen_chapter(settings, work, "ch-001")
    assert rec["job_type"] == "draft"
    assert rec["state"] == JobState.COMPLETED.name
    assert rec["chapter_id"] == "ch-001"
    last_h = rec["history"][-1]
    assert last_h["reason"] == "reopened after human content edit"

    # Branch and worktree exist
    branches = chapter_branches(repo, "ch-001")
    assert len(branches) == 1
    branch = branches[0]

    # 3. validate (Claude unavailable -> WAITING_HUMAN) then skip-validation
    val_rec = run_validate_job(
        settings,
        work,
        "ch-001",
        claude_runner=FakeClaude([envelope(None, is_error=True)] * 3),
    )
    assert val_rec["state"] == JobState.WAITING_HUMAN.name

    skip_rec = skip_validation(settings, work, "ch-001", reason="human edit skip")
    assert skip_rec["state"] == JobState.COMPLETED.name

    # 4. state-update (with fake claude)
    valid_summary = load_yaml(Path(__file__).parent / "fixtures" / "valid" / "summary.yaml")
    new_summary = copy.deepcopy(valid_summary)
    new_summary["chapter_id"] = "ch-001"
    new_summary["events"] = ["第一章の改訂版あらすじ要約"]
    new_summary["characters"][0]["knowledge_added"] = []
    new_summary["foreshadowing"] = []

    fake_claude = FakeClaude([new_summary])
    su_rec = run_state_update(settings, work, "ch-001", claude_runner=fake_claude)
    assert su_rec["state"] == JobState.WAITING_HUMAN.name

    # 5. approve-state
    prop = pending_proposal(settings, work.work_key, "ch-001")
    assert prop is not None
    appr_rec = approve_state(settings, work, "ch-001")
    assert appr_rec["state"] == JobState.COMPLETED.name

    # 6. review-done (clear review_required on branch)
    clear_review_required(settings, work, "ch-001")

    # 7. final-approve
    final_c = final_approve(settings, work, "ch-001")
    assert final_c is not None

    # Verify ch-001 is back to FINAL on main and summary is updated
    meta_final = read_chapter_meta(repo, "ch-001")
    assert meta_final is not None
    assert meta_final["state"] == ChapterState.FINAL.name
    assert meta_final["review_required"] is False

    summary_final = load_yaml(repo / "chapters/ch-001/summary.yaml")
    assert summary_final["events"] == ["第一章の改訂版あらすじ要約"]


def test_reopen_precondition_errors(tmp_path: Path) -> None:
    settings, work = setup_novel(tmp_path)
    repo = work.path
    setup_final_chapter(settings, work, "ch-001")

    # 1. Chapter not in DRAFTED state (it is FINAL)
    with pytest.raises(ChapterError, match="must be in DRAFTED state"):
        reopen_chapter(settings, work, "ch-001")

    # Transition to DRAFTED
    transition_on_main(
        settings, work, "ch-001", ChapterEvent.CONTENT_CHANGED,
        subject="content edit ch-001",
        trailers=[("NovUI-Edit", "human-content")],
    )

    # 2. Existing branch
    from novui.workrepo import create_job_worktree
    jw = create_job_worktree(repo, settings.worktree_root, work.work_key, "ch-001", "job-999")
    with pytest.raises(ChapterError, match="already has active job branch"):
        reopen_chapter(settings, work, "ch-001")

    from novui.workrepo import remove_job_worktree
    remove_job_worktree(repo, jw, delete_branch=True)

    # 3. Chapter not in chapters-order.yaml
    with pytest.raises(ChapterError, match="not found in chapters-order.yaml"):
        reopen_chapter(settings, work, "ch-999")
