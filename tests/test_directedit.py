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
from novui.draftjob import run_chapter_draft_job
from novui.finalize import final_approve
from novui.gitinspect import run_git
from novui.impactjob import run_impact_job
from novui.jobrecord import apply_transition, load_job_record, new_job_record, save_job_record
from novui.jobrunner import jobs_dir
from novui.planjob import approve_plan
from novui.procrun import ProcResult
from novui.proposals import pending_proposal
from novui.states import ChapterEvent, ChapterState, JobEvent, JobState
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


def test_run_impact_job_head_not_main_ancestor(tmp_path: Path) -> None:
    settings, work = setup_novel(tmp_path)
    repo = work.path
    base = head_commit(repo, "main")

    # Create a separate branch with a commit not merged into main
    run_git(repo, "checkout", "-b", "other-branch")
    c1 = load_yaml(repo / "characters" / "C001.yaml")
    c1["personality"] = ["情熱的"]
    write_yaml_atomic(repo / "characters" / "C001.yaml", c1)
    other_head = commit_all(repo, "change C001 on other branch", [("NovUI-Edit", "human-content")], name="Tester", email="tester@test")
    assert other_head is not None

    # Switch back to main
    run_git(repo, "checkout", "main")

    # head is on other-branch and not an ancestor of main
    with pytest.raises(ValueError, match="is not an ancestor of main"):
        run_impact_job(settings, work, base=base, head=other_head, reason="test")


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


# ==============================================================================
# 3-C 修正 01: R1 & R2 tests
# ==============================================================================

def test_direct_edit_content_with_running_subsequent_job_leaves_working_tree_clean(tmp_path: Path) -> None:
    """R1: If direct_edit fails because a subsequent chapter has a RUNNING job,

    chapter.yaml must not be modified in main working tree, git status shows only Human's draft.md,
    no commit is added on any branch. After completing the job, edit succeeds.
    """
    settings, work = setup_novel(tmp_path)
    repo = work.path
    setup_final_chapter(settings, work, "ch-001")

    # Add ch-002 with active worktree and job branch
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
    run_chapter_draft_job(settings, work, "ch-002", container_runner=_agy_runner(GOOD_TEXT))

    branches = chapter_branches(repo, "ch-002")
    assert len(branches) == 1
    ch2_branch = branches[0]
    jdir = jobs_dir(settings, work.work_key)

    # Create a RUNNING job record associated with ch2_branch
    running_job_id = "job-888"
    rec = new_job_record(running_job_id, "draft", chapter_id="ch-002")
    rec["branch"] = ch2_branch
    rec = apply_transition(rec, JobEvent.LOCK_ACQUIRED)
    assert rec["state"] == JobState.RUNNING.name
    save_job_record(jdir, rec)

    main_head_before = head_commit(repo, "main")
    ch2_head_before = head_commit(repo, ch2_branch)

    # Human edits draft.md of ch-001
    draft1_path = repo / "chapters/ch-001/draft.md"
    orig_draft1 = draft1_path.read_text(encoding="utf-8")
    new_draft1 = orig_draft1.replace("門を通った。", "門を通った。編集された内容。")
    draft1_path.write_text(new_draft1, encoding="utf-8")

    # Attempt direct_edit with content -> fails with ChapterError
    with pytest.raises(ChapterError, match=f"Active job {running_job_id} in state RUNNING"):
        direct_edit(settings, work, kind="content", note="先行章編集")

    # main working tree: chapter.yaml unchanged, only draft.md appears in git status
    status_out = run_git(repo, "-c", "diff.renames=false", "status", "--porcelain=v1").decode("utf-8").strip()
    assert status_out == "M chapters/ch-001/draft.md"
    meta1 = read_chapter_meta(repo, "ch-001")
    assert meta1 is not None
    assert meta1["state"] == ChapterState.FINAL.name
    assert meta1["review_required"] is False

    # No commit added on any branch
    assert head_commit(repo, "main") == main_head_before
    assert head_commit(repo, ch2_branch) == ch2_head_before

    # Now transition the running job record to COMPLETED
    rec = apply_transition(rec, JobEvent.CLI_EXITED)
    rec = apply_transition(rec, JobEvent.CHECK_PASSED, needs_validation=False)
    assert rec["state"] == JobState.COMPLETED.name
    save_job_record(jdir, rec)

    # Edit again -> succeeds
    res = direct_edit(settings, work, kind="content", note="先行章編集")
    assert res["commit"] is not None
    assert res["chapters_drafted"] == ["ch-001"]
    assert "ch-002" in res["review_marked"]
    assert head_commit(repo, "main") != main_head_before
    assert head_commit(repo, ch2_branch) != ch2_head_before


def test_analyze_impact_ancestor_commit_and_non_ancestor_reject(tmp_path: Path) -> None:
    """R2: edit (impact failed) -> another non-protected commit -> analyze-impact --commit <edit commit>

    succeeds and marks review_required. A non-ancestor commit raises ValueError.
    """
    settings, work = setup_novel(tmp_path)
    repo = work.path
    setup_final_chapter(settings, work, "ch-001")

    # 1. Edit protected character C001
    c1 = load_yaml(repo / "characters/C001.yaml")
    c1["personality"] = ["極めて冷静沈着"]
    write_yaml_atomic(repo / "characters/C001.yaml", c1)

    # 2. direct_edit with impact failure
    def fail_claude(**kwargs: Any) -> ProcResult:
        return ProcResult(1, 1.0, False, None, False, b"", b"error")

    res = direct_edit(settings, work, kind="content", note="カイ性格変更", claude_runner=fail_claude)
    edit_commit = res["commit"]
    assert edit_commit is not None
    assert res["impact_error"] is not None
    assert read_chapter_meta(repo, "ch-001")["review_required"] is False

    # 3. Create another non-protected commit on main
    outline1_path = repo / "chapters/ch-001/outline.md"
    orig_outline = outline1_path.read_text(encoding="utf-8")
    outline1_path.write_text(orig_outline + "\nメモ追記\n", encoding="utf-8")
    res_typo = direct_edit(settings, work, kind="typo", note="メモ追記")
    later_commit = res_typo["commit"]
    assert later_commit != edit_commit
    assert head_commit(repo, "main") == later_commit

    # 4. analyze-impact --commit <edit_commit> succeeds and marks review_required
    impact_resp = {
        "type": "impact_analysis",
        "affected": [{"chapter_id": "ch-001", "reason": "性格変更影響"}],
        "notes": [],
    }
    fake = FakeClaude([impact_resp])

    parents_out = run_git(repo, "rev-list", "--parents", "-n", "1", edit_commit).decode("utf-8").strip()
    parent_of_edit = parents_out.split()[1]

    rec, affected, notes = run_impact_job(
        settings,
        work,
        base=parent_of_edit,
        head=edit_commit,
        reason="カイ性格変更の分析やり直し",
        claude_runner=fake,
    )
    assert rec["state"] == JobState.COMPLETED.name
    meta1 = read_chapter_meta(repo, "ch-001")
    assert meta1 is not None
    assert meta1["review_required"] is True

    # 5. Non-ancestor commit raises ValueError
    run_git(repo, "checkout", "-b", "other-branch")
    c2 = load_yaml(repo / "characters/C002.yaml")
    c2["personality"] = ["豪快"]
    write_yaml_atomic(repo / "characters/C002.yaml", c2)
    other_commit = commit_all(repo, "other branch commit", [("NovUI-Edit", "human-content")], name="Tester", email="tester@test")
    assert other_commit is not None
    run_git(repo, "checkout", "main")

    with pytest.raises(ValueError, match="is not an ancestor of main"):
        run_impact_job(
            settings,
            work,
            base=parent_of_edit,
            head=other_commit,
            reason="test other branch",
        )


def test_direct_edit_content_plan_subsequent_chapter_retains_planned_state(tmp_path: Path) -> None:
    """R3: In a single content edit, editing draft.md of ch-001 and plan.yaml of subsequent ch-003

    (PLAN_APPROVED, no branch) correctly reverts ch-003 to PLANNED and sets review_required on it.
    ch-001 becomes DRAFTED, and only 1 commit is created.
    """
    settings, work = setup_novel(tmp_path)
    repo = work.path
    setup_final_chapter(settings, work, "ch-001")
    setup_final_chapter(settings, work, "ch-002")

    # Setup ch-003 in PLAN_APPROVED state without work branch
    add_chapter(settings, work, "ch-003", "第三章", "第三章アウトライン\n")
    plan3 = make_plan("ch-003")
    transition_on_main(
        settings, work, "ch-003", ChapterEvent.PLAN_WRITTEN,
        subject="plan ch-003",
        trailers=[("NovUI-Job", "job-3")],
        extra_files={"chapters/ch-003/plan.yaml": dumps_yaml(plan3).encode("utf-8")},
        last_job="job-3",
    )
    approve_plan(settings, work, "ch-003")
    meta3_init = read_chapter_meta(repo, "ch-003")
    assert meta3_init is not None
    assert meta3_init["state"] == ChapterState.PLAN_APPROVED.name
    assert meta3_init["review_required"] is False

    # 1. Edit draft.md of ch-001 (FINAL -> DRAFTED)
    draft1_p = repo / "chapters/ch-001/draft.md"
    orig_text = draft1_p.read_text(encoding="utf-8")
    new_text = orig_text.replace("門を通った。", "門を通った。改訂。")
    draft1_p.write_text(new_text, encoding="utf-8")

    # 2. Edit plan.yaml of ch-003 (PLAN_APPROVED -> PLANNED)
    plan3_doc = load_yaml(repo / "chapters/ch-003/plan.yaml")
    plan3_doc["pov"] = "一人称視点"
    write_yaml_atomic(repo / "chapters/ch-003/plan.yaml", plan3_doc)

    head_before = head_commit(repo, "main")

    # 3. Direct edit (content)
    res = direct_edit(settings, work, kind="content", note="ch-001 draft & ch-003 plan edit")

    # Verify single commit
    commit_hash = res["commit"]
    assert commit_hash is not None
    assert head_commit(repo, "main") == commit_hash
    parents_out = run_git(repo, "rev-list", "--parents", "-n", "1", commit_hash).decode("utf-8").strip()
    assert parents_out.split()[1] == head_before

    assert res["chapters_drafted"] == ["ch-001"]
    assert res["chapters_replanned"] == ["ch-003"]
    assert res["review_marked"] == ["ch-002", "ch-003"]

    # ch-001: DRAFTED and review_required
    meta1 = read_chapter_meta(repo, "ch-001")
    assert meta1 is not None
    assert meta1["state"] == ChapterState.DRAFTED.name
    assert meta1["review_required"] is True

    # ch-002: FINAL and review_required
    meta2 = read_chapter_meta(repo, "ch-002")
    assert meta2 is not None
    assert meta2["state"] == ChapterState.FINAL.name
    assert meta2["review_required"] is True

    # ch-003: PLANNED (reverted from PLAN_APPROVED) and review_required
    meta3 = read_chapter_meta(repo, "ch-003")
    assert meta3 is not None
    assert meta3["state"] == ChapterState.PLANNED.name
    assert meta3["review_required"] is True
    assert any(
        r["code"] == "previous_chapter_changed" and "ch-001 content edited" in r["detail"]
        for r in meta3["review_reasons"]
    )

