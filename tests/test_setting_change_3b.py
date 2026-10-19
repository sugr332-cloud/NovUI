"""Tests for Phase 3-B: setting_change Job, impact analysis, follow main, and CLI commands.

Uses fake Claude and fake AGY runners; strictly no live external AI calls.
"""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from typing import Any, Callable

import pytest

from novui import cli
from novui.approvals import ApprovalError, approval_rel
from novui.chapters import (
    ChapterError,
    add_chapter,
    clear_review_required,
    read_chapter_meta,
    transition_on_main,
)
from novui.config import Settings
from novui.finalize import final_approve
from novui.follow import FollowConflict, follow_main
from novui.gitinspect import get_changes, run_git
from novui.impact import analyze_impact, impact_chapters
from novui.jobqueue import load_queue, run_queue
from novui.jobrecord import load_job_record
from novui.jobrunner import jobs_dir
from novui.models import save_catalog, select_model
from novui.planjob import approve_plan
from novui.procrun import ProcResult
from novui.proposals import ProposalError, pending_proposal
from novui.routingjob import (
    approve_routing,
    check_proposed_jobs,
    load_routing,
    reject_routing,
    run_routing_job,
)
from novui.settingchange import (
    approve_setting,
    load_setting_proposal,
    reject_setting,
    run_setting_change_job,
)
from novui.settingcheck import (
    check_setting_patch_policy,
    check_setting_result,
    is_setting_target,
)
from novui.statepatch import file_sha256, patch_sha256
from novui.states import ChapterEvent, JobEvent, JobState
from novui.stateupdate import approve_state, run_state_update
from novui.validatejob import find_chapter_branch, run_validate_job
from novui.workrepo import commit_all, head_commit
from novui.works import WorkInfo, get_work
from novui.yamlio import dumps_yaml, load_yaml

from test_validatejob import CH, GOOD_TEXT, envelope, setup_work, make_plan, _agy_runner


@pytest.fixture(autouse=True)
def mock_container_meta(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("novui.jobrunner.image_label", lambda img, key: "1.2.14")
    monkeypatch.setattr(
        "novui.jobrunner.image_id",
        lambda img: "sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
    )


class MultiFakeClaude:
    """Fake Claude runner yielding responses in order."""

    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self.responses = list(responses)
        self.prompts: list[str] = []

    def __call__(self, **kwargs: Any) -> ProcResult:
        self.prompts.append(kwargs["prompt"])
        if not self.responses:
            raise RuntimeError(f"MultiFakeClaude ran out of responses! Last prompt: {kwargs.get('prompt')[:200]}")
        resp = self.responses.pop(0)
        return ProcResult(0, 1.0, False, None, False, envelope(resp), b"")


def setup_novel(tmp_path: Path) -> tuple[Settings, WorkInfo]:
    """Setup a work with C001 (having personality), C002, and registry with F001."""
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


# ==============================================================================
# Group 1: Routing scope setting
# ==============================================================================

def test_routing_scope_setting_with_and_without_chapter(tmp_path: Path) -> None:
    settings, work = setup_novel(tmp_path)

    # 1. Scope setting with chapter
    out_with_ch = {
        "type": "instruction_routing",
        "instruction_id": "INS-0001",
        "scope": "setting",
        "chapter_id": CH,
        "instruction": "カイの性格を追加",
        "scope_mismatch": False,
        "scope_mismatch_reason": None,
        "proposed_jobs": [
            {
                "job_type": "setting_change",
                "summary": "カイの性格設定変更",
                "instruction": "性格を追加する",
                "anchor": None,
                "depends_on": None,
            }
        ],
        "impact": [],
        "questions": [],
    }
    record1, doc1 = run_routing_job(
        settings, work, CH, "setting", "カイの性格を追加",
        claude_runner=MultiFakeClaude([out_with_ch]),
    )
    assert record1["state"] == "COMPLETED"
    assert doc1 is not None and doc1["status"] == "pending"
    assert doc1["chapter_id"] == CH

    queued = approve_routing(settings, work, "INS-0001")
    assert len(queued) == 1
    assert queued[0]["job_type"] == "setting_change"
    assert queued[0]["chapter_id"] == CH

    # 2. Scope setting without chapter
    out_no_ch = copy.deepcopy(out_with_ch)
    out_no_ch["instruction_id"] = "INS-0002"
    out_no_ch["chapter_id"] = None
    record2, doc2 = run_routing_job(
        settings, work, None, "setting", "設定変更",
        claude_runner=MultiFakeClaude([out_no_ch]),
    )
    assert record2["state"] == "COMPLETED"
    assert doc2 is not None and doc2["chapter_id"] is None

    queued2 = approve_routing(settings, work, "INS-0002")
    assert len(queued2) == 1
    assert queued2[0]["chapter_id"] is None


def test_routing_scope_setting_mismatch_and_questions(tmp_path: Path) -> None:
    settings, work = setup_novel(tmp_path)

    # Questions -> status "questions", cannot approve
    out_q = {
        "type": "instruction_routing",
        "instruction_id": "INS-0001",
        "scope": "setting",
        "chapter_id": None,
        "instruction": "変更",
        "scope_mismatch": False,
        "scope_mismatch_reason": None,
        "proposed_jobs": [],
        "impact": [],
        "questions": ["どの設定を変更しますか？"],
    }
    record, doc = run_routing_job(
        settings, work, None, "setting", "変更",
        claude_runner=MultiFakeClaude([out_q]),
    )
    assert record["state"] == "WAITING_HUMAN"
    assert doc is not None and doc["status"] == "questions"
    with pytest.raises(ChapterError, match="only a pending routing"):
        approve_routing(settings, work, "INS-0001")


def test_routing_setting_change_in_other_scope_fails(tmp_path: Path) -> None:
    settings, work = setup_novel(tmp_path)
    out = {
        "type": "instruction_routing",
        "instruction_id": "INS-0001",
        "scope": "chapter",
        "chapter_id": CH,
        "instruction": "変更",
        "scope_mismatch": False,
        "scope_mismatch_reason": None,
        "proposed_jobs": [
            {
                "job_type": "setting_change",
                "summary": "不適切な設定変更",
                "instruction": "設定変更",
                "anchor": None,
                "depends_on": None,
            }
        ],
        "impact": [],
        "questions": [],
    }
    record, doc = run_routing_job(
        settings, work, CH, "chapter", "変更",
        claude_runner=MultiFakeClaude([out]),
    )
    assert record["state"] == "FAILED"
    assert doc is None
    check = next(c for c in record["checks"] if c["name"] == "routing_jobs")
    assert check["status"] == "FAIL"


def test_routing_approve_rejected_when_main_moved(tmp_path: Path) -> None:
    settings, work = setup_novel(tmp_path)
    out = {
        "type": "instruction_routing",
        "instruction_id": "INS-0001",
        "scope": "setting",
        "chapter_id": None,
        "instruction": "変更",
        "scope_mismatch": False,
        "scope_mismatch_reason": None,
        "proposed_jobs": [
            {
                "job_type": "setting_change",
                "summary": "設定変更",
                "instruction": "設定変更",
                "anchor": None,
                "depends_on": None,
            }
        ],
        "impact": [],
        "questions": [],
    }
    record, doc = run_routing_job(
        settings, work, None, "setting", "変更",
        claude_runner=MultiFakeClaude([out]),
    )
    assert doc["status"] == "pending"

    # Advance main
    (work.path / "world" / "setting.md").write_text("advance", encoding="utf-8")
    commit_all(work.path, "advance main", [("NovUI-Edit", "human-content")], name="Tester", email="tester@test")

    with pytest.raises(ChapterError, match="main changed since"):
        approve_routing(settings, work, "INS-0001")


# ==============================================================================
# Group 2: setting_change Job & Proposals
# ==============================================================================

def test_setting_change_character_patch(tmp_path: Path) -> None:
    settings, work = setup_novel(tmp_path)
    repo = work.path
    c_hash = file_sha256(repo / "characters" / "C001.yaml")

    plan_out = {
        "type": "setting_change_plan",
        "instruction_id": "INS-0001",
        "targets": [{"target": "characters/C001.yaml", "change": "性格特徴を追加"}],
        "new_foreshadowing": 0,
        "questions": [],
    }
    patch_out = {
        "type": "state_patch",
        "target": "characters/C001.yaml",
        "base_hash": c_hash,
        "operations": [
            {"op": "add", "path": "/personality/-", "value": "洞察力"}
        ],
        "reason": "カイの性格を追加",
    }
    impact_out = {
        "type": "impact_analysis",
        "affected": [],
        "notes": [],
    }

    record = run_setting_change_job(
        settings, work, instruction_id="INS-0001", instruction="カイの性格を追加",
        claude_runner=MultiFakeClaude([plan_out, patch_out, impact_out]),
    )
    assert record["state"] == "COMPLETED"
    assert record["job_type"] == "setting_change"

    # Main worktree must not be modified
    assert not get_changes(repo)

    # Proposal is pending
    prop = load_setting_proposal(settings, work.work_key, "INS-0001")
    assert prop["status"] == "pending"
    assert [p["patch"]["target"] for p in prop["patches"]] == ["characters/C001.yaml"]
    assert len(prop["patches"]) == 1


def test_setting_change_registry_cancelled_and_resolution(tmp_path: Path) -> None:
    settings, work = setup_novel(tmp_path)
    repo = work.path
    reg_hash = file_sha256(repo / "foreshadowing" / "registry.yaml")

    plan_out = {
        "type": "setting_change_plan",
        "instruction_id": "INS-0001",
        "targets": [{"target": "foreshadowing/registry.yaml", "change": "F001を取り下げ"}],
        "new_foreshadowing": 0,
        "questions": [],
    }
    patch_out = {
        "type": "state_patch",
        "target": "foreshadowing/registry.yaml",
        "base_hash": reg_hash,
        "operations": [
            {"op": "replace", "path": "/0/status", "value": "cancelled"},
            {"op": "replace", "path": "/0/planned_resolution", "value": {"chapter": "ch-002", "scene": "S01"}},
        ],
        "reason": "F001の回収予定を変更し取り下げ",
    }
    impact_out = {
        "type": "impact_analysis",
        "affected": [],
        "notes": [],
    }

    record = run_setting_change_job(
        settings, work, instruction_id="INS-0001", instruction="F001取り下げ",
        claude_runner=MultiFakeClaude([plan_out, patch_out, impact_out]),
    )
    assert record["state"] == "COMPLETED"
    prop = load_setting_proposal(settings, work.work_key, "INS-0001")
    assert prop["status"] == "pending"


def test_setting_change_new_foreshadowing_id_sequence(tmp_path: Path) -> None:
    settings, work = setup_novel(tmp_path)
    repo = work.path
    reg_hash = file_sha256(repo / "foreshadowing" / "registry.yaml")

    # Sequential: F002 when F001 exists -> PASS
    plan_pass = {
        "type": "setting_change_plan",
        "instruction_id": "INS-0001",
        "targets": [{"target": "foreshadowing/registry.yaml", "change": "新しい伏線を追加"}],
        "new_foreshadowing": 1,
        "questions": [],
    }
    patch_pass = {
        "type": "state_patch",
        "target": "foreshadowing/registry.yaml",
        "base_hash": reg_hash,
        "operations": [
            {
                "op": "add",
                "path": "/-",
                "value": {
                    "id": "F002",
                    "name": "新しい伏線",
                    "status": "planned",
                    "importance": "normal",
                    "introduced": [],
                    "hints": [],
                    "developments": [],
                    "planned_resolution": None,
                    "resolved": None,
                    "notes": "",
                },
            }
        ],
        "reason": "F002の追加",
    }
    impact_out = {"type": "impact_analysis", "affected": [], "notes": []}

    rec = run_setting_change_job(
        settings, work, instruction_id="INS-0001", instruction="伏線追加",
        claude_runner=MultiFakeClaude([plan_pass, patch_pass, impact_out]),
    )
    assert rec["state"] == "COMPLETED"

    # Non-sequential: F004 when F001 exists -> FAIL
    plan_fail = copy.deepcopy(plan_pass)
    plan_fail["instruction_id"] = "INS-0002"
    patch_fail = copy.deepcopy(patch_pass)
    patch_fail["operations"][0]["value"]["id"] = "F004"

    rec_fail = run_setting_change_job(
        settings, work, instruction_id="INS-0002", instruction="不正な伏線追加",
        claude_runner=MultiFakeClaude([plan_fail, patch_fail]),
    )
    assert rec_fail["state"] == "FAILED"
    chk = next(c for c in rec_fail["checks"] if c["name"] == "setting_patch_policy")
    assert chk["status"] == "FAIL"


def test_setting_change_rejects_modifying_id(tmp_path: Path) -> None:
    settings, work = setup_novel(tmp_path)
    repo = work.path
    c_hash = file_sha256(repo / "characters" / "C001.yaml")

    plan_out = {
        "type": "setting_change_plan",
        "instruction_id": "INS-0001",
        "targets": [{"target": "characters/C001.yaml", "change": "ID変更"}],
        "new_foreshadowing": 0,
        "questions": [],
    }
    patch_out = {
        "type": "state_patch",
        "target": "characters/C001.yaml",
        "base_hash": c_hash,
        "operations": [{"op": "replace", "path": "/id", "value": "C999"}],
        "reason": "IDの変更",
    }
    rec = run_setting_change_job(
        settings, work, instruction_id="INS-0001", instruction="ID変更",
        claude_runner=MultiFakeClaude([plan_out, patch_out]),
    )
    assert rec["state"] == "FAILED"
    chk = next(c for c in rec["checks"] if c["name"] == "setting_patch_policy")
    assert chk["status"] == "FAIL"


def test_setting_change_questions_waiting_human(tmp_path: Path) -> None:
    settings, work = setup_novel(tmp_path)
    plan_out = {
        "type": "setting_change_plan",
        "instruction_id": "INS-0001",
        "targets": [],
        "new_foreshadowing": 0,
        "questions": ["どのように変更しますか？"],
    }
    rec = run_setting_change_job(
        settings, work, instruction_id="INS-0001", instruction="適当に変更",
        claude_runner=MultiFakeClaude([plan_out]),
    )
    assert rec["state"] == "WAITING_HUMAN"
    assert rec["history"][-1]["reason"] == "questions: 1"
    # No proposal created
    with pytest.raises(ChapterError, match="not found"):
        load_setting_proposal(settings, work.work_key, "INS-0001")


def test_setting_change_noop_fails(tmp_path: Path) -> None:
    settings, work = setup_novel(tmp_path)
    repo = work.path
    c_hash = file_sha256(repo / "characters" / "C001.yaml")

    plan_out = {
        "type": "setting_change_plan",
        "instruction_id": "INS-0001",
        "targets": [{"target": "characters/C001.yaml", "change": "同じ値に設定"}],
        "new_foreshadowing": 0,
        "questions": [],
    }
    # No-op: test op or replacing with same value
    patch_out = {
        "type": "state_patch",
        "target": "characters/C001.yaml",
        "base_hash": c_hash,
        "operations": [{"op": "test", "path": "/id", "value": "C001"}],
        "reason": "確認のみ",
    }
    rec = run_setting_change_job(
        settings, work, instruction_id="INS-0001", instruction="変更なし",
        claude_runner=MultiFakeClaude([plan_out, patch_out]),
    )
    assert rec["state"] == "FAILED"
    assert rec["history"][-1]["reason"] == "no setting change"


def test_setting_change_supersedes_old_proposal(tmp_path: Path) -> None:
    settings, work = setup_novel(tmp_path)
    repo = work.path
    c_hash = file_sha256(repo / "characters" / "C001.yaml")

    # First proposal
    plan1 = {
        "type": "setting_change_plan",
        "instruction_id": "INS-0001",
        "targets": [{"target": "characters/C001.yaml", "change": "変更1"}],
        "new_foreshadowing": 0,
        "questions": [],
    }
    patch1 = {
        "type": "state_patch",
        "target": "characters/C001.yaml",
        "base_hash": c_hash,
        "operations": [{"op": "add", "path": "/personality/-", "value": "洞察力"}],
        "reason": "特徴1追加",
    }
    impact1 = {"type": "impact_analysis", "affected": [], "notes": []}
    rec1 = run_setting_change_job(
        settings, work, instruction_id="INS-0001", instruction="変更1",
        claude_runner=MultiFakeClaude([plan1, patch1, impact1]),
    )
    assert rec1["state"] == "COMPLETED"
    prop1 = load_setting_proposal(settings, work.work_key, "INS-0001")
    assert prop1["status"] == "pending"

    # Second proposal targeting C001
    plan2 = copy.deepcopy(plan1)
    plan2["instruction_id"] = "INS-0002"
    patch2 = copy.deepcopy(patch1)
    patch2["operations"][0]["value"] = "大胆"
    impact2 = copy.deepcopy(impact1)

    rec2 = run_setting_change_job(
        settings, work, instruction_id="INS-0002", instruction="変更2",
        claude_runner=MultiFakeClaude([plan2, patch2, impact2]),
    )
    assert rec2["state"] == "COMPLETED"

    # Old proposal must now be superseded
    prop1_after = load_setting_proposal(settings, work.work_key, "INS-0001")
    assert prop1_after["status"] == "superseded"
    assert "superseded by INS-0002" in prop1_after["status_reason"]

    # New proposal is pending
    prop2 = load_setting_proposal(settings, work.work_key, "INS-0002")
    assert prop2["status"] == "pending"


def test_setting_change_queue_execution(tmp_path: Path) -> None:
    settings, work = setup_novel(tmp_path)
    repo = work.path
    c_hash = file_sha256(repo / "characters" / "C001.yaml")

    out_routing = {
        "type": "instruction_routing",
        "instruction_id": "INS-0001",
        "scope": "setting",
        "chapter_id": None,
        "instruction": "カイの設定変更",
        "scope_mismatch": False,
        "scope_mismatch_reason": None,
        "proposed_jobs": [
            {
                "job_type": "setting_change",
                "summary": "カイの性格変更",
                "instruction": "特徴を追加",
                "anchor": None,
                "depends_on": None,
            }
        ],
        "impact": [],
        "questions": [],
    }
    run_routing_job(settings, work, None, "setting", "設定変更", claude_runner=MultiFakeClaude([out_routing]))
    approve_routing(settings, work, "INS-0001")

    # Queue has setting_change item
    q = load_queue(settings, work.work_key)
    assert len(q) == 1
    assert q[0]["job_type"] == "setting_change"

    # Run queue
    plan_out = {
        "type": "setting_change_plan",
        "instruction_id": "INS-0001",
        "targets": [{"target": "characters/C001.yaml", "change": "特徴追加"}],
        "new_foreshadowing": 0,
        "questions": [],
    }
    patch_out = {
        "type": "state_patch",
        "target": "characters/C001.yaml",
        "base_hash": c_hash,
        "operations": [{"op": "add", "path": "/personality/-", "value": "洞察力"}],
        "reason": "特徴追加",
    }
    impact_out = {"type": "impact_analysis", "affected": [], "notes": []}

    records = run_queue(
        settings, work, claude_runner=MultiFakeClaude([plan_out, patch_out, impact_out]),
    )
    assert len(records) == 1
    assert records[0]["state"] == "COMPLETED"

    # Queue item is done
    q_after = load_queue(settings, work.work_key)
    assert q_after[0]["status"] == "done"


# ==============================================================================
# Group 3: Impact Analysis
# ==============================================================================

def test_impact_analysis_chapter_selection_and_cleanup(tmp_path: Path) -> None:
    settings, work = setup_novel(tmp_path)
    repo = work.path

    # ch-001: PLANNED (has plan.yaml) -> included as "plan"
    add_chapter(settings, work, "ch-002", "第2章", "アウトライン")
    # ch-002: OUTLINE_ONLY -> excluded

    chapters = impact_chapters(repo)
    assert chapters == [(CH, "plan")]

    # If non-existent chapter returned by Claude -> fails impact check
    def mock_call(expected_type: str, task_text: str, log_name: str, check_name: str, **kwargs: Any):
        doc = {
            "type": "impact_analysis",
            "affected": [{"chapter_id": "ch-999", "reason": "存在しない章"}],
            "notes": [],
        }
        from novui.claudejob import ClaudeCallOutcome
        return ClaudeCallOutcome(
            data=doc,
            attempts=(),
            is_error_flags=(False,),
            last_result=ProcResult(0, 1.0, False, None, False, envelope(doc), b""),
            actual_model="opus",
            timed_out=False,
        )

    affected, notes, checks = analyze_impact(
        settings, repo=repo, instruction="変更", diff_text="diff", call=mock_call,
    )
    ref_check = next(c for c in checks if c.name == "impact_refs")
    assert ref_check.status == "FAIL"

    # No leftover temporary directories under data_dir
    assert not list(settings.data_dir.glob("temp-setting-*"))


# ==============================================================================
# Group 4: approve-setting / reject-setting
# ==============================================================================

def test_approve_setting_and_reject_setting(tmp_path: Path) -> None:
    settings, work = setup_novel(tmp_path)
    repo = work.path

    # Add second chapter on main (unbranched, with plan so it qualifies for impact analysis)
    add_chapter(settings, work, "ch-002", "第2章", "アウトラインのみ")
    p2 = load_yaml(repo / "chapters" / CH / "plan.yaml")
    p2["chapter_id"] = "ch-002"
    (repo / "chapters" / "ch-002" / "plan.yaml").write_text(dumps_yaml(p2), encoding="utf-8")
    commit_all(repo, "add plan for ch-002", [("NovUI-Edit", "human-content")], name="Tester", email="tester@test")
    # Create draft on ch-001 so it has a job branch
    from novui.draftjob import run_chapter_draft_job
    draft_rec = run_chapter_draft_job(settings, work, CH, container_runner=_agy_runner(GOOD_TEXT))
    assert draft_rec["state"] == "COMPLETED"
    b_ch001 = find_chapter_branch(repo, CH)
    assert b_ch001 is not None

    c_hash = file_sha256(repo / "characters" / "C001.yaml")
    plan_out = {
        "type": "setting_change_plan",
        "instruction_id": "INS-0001",
        "targets": [{"target": "characters/C001.yaml", "change": "特徴追加"}],
        "new_foreshadowing": 0,
        "questions": [],
    }
    patch_out = {
        "type": "state_patch",
        "target": "characters/C001.yaml",
        "base_hash": c_hash,
        "operations": [{"op": "add", "path": "/personality/-", "value": "洞察力"}],
        "reason": "洞察力を追加",
    }
    impact_out = {
        "type": "impact_analysis",
        "affected": [
            {"chapter_id": CH, "reason": "ch-001に影響"},
            {"chapter_id": "ch-002", "reason": "ch-002に影響"},
        ],
        "notes": [],
    }

    run_setting_change_job(
        settings, work, instruction_id="INS-0001", instruction="カイ変更",
        claude_runner=MultiFakeClaude([plan_out, patch_out, impact_out]),
    )

    # Approve setting change
    res = approve_setting(settings, work, "INS-0001")
    assert res["status"] == "approved"
    assert res["approval_id"] is not None

    # Verification 1: Single commit on main
    main_head = head_commit(repo, "main")
    assert res["commit"] == main_head

    # Verification 2: Branched chapter CH has review_required on its branch
    branch_meta = load_yaml(Path(draft_rec["worktree"]) / "chapters" / CH / "chapter.yaml")
    assert branch_meta["review_required"] is True
    assert any(r["code"] == "setting_changed" for r in branch_meta["review_reasons"])

    # Verification 3: Unbranched chapter ch-002 has review_required on main
    main_meta_2 = load_yaml(repo / "chapters" / "ch-002" / "chapter.yaml")
    assert main_meta_2["review_required"] is True

    # Verification 4: Approval record on main
    app_path = repo / approval_rel(res["approval_id"])
    assert app_path.is_file()
    app_doc = load_yaml(app_path)
    assert app_doc["source"] == "instruction"
    assert app_doc["instruction_id"] == "INS-0001"
    assert app_doc["job_id"] is None

    # Test reject_setting with another proposal
    plan2 = copy.deepcopy(plan_out)
    plan2["instruction_id"] = "INS-0002"
    patch2 = copy.deepcopy(patch_out)
    patch2["base_hash"] = file_sha256(repo / "characters" / "C001.yaml")
    run_setting_change_job(
        settings, work, instruction_id="INS-0002", instruction="さらに変更",
        claude_runner=MultiFakeClaude([plan2, patch2, {"type": "impact_analysis", "affected": [], "notes": []}]),
    )
    rej = reject_setting(settings, work, "INS-0002", reason="不要な変更")
    assert rej["status"] == "rejected"
    assert rej["status_reason"] == "不要な変更"


def test_approve_setting_rejects_if_job_branch_already_changed_target(tmp_path: Path) -> None:
    settings, work = setup_novel(tmp_path)
    repo = work.path

    from novui.draftjob import run_chapter_draft_job
    draft_rec = run_chapter_draft_job(settings, work, CH, container_runner=_agy_runner(GOOD_TEXT))
    wt = Path(draft_rec["worktree"])

    # Job branch modifies characters/C001.yaml
    c_branch = load_yaml(wt / "characters" / "C001.yaml")
    c_branch["personality"] = ["先行変更"]
    (wt / "characters" / "C001.yaml").write_text(dumps_yaml(c_branch), encoding="utf-8")
    commit_all(wt, "branch modified C001", [("NovUI-Edit", "human-content")], name="Tester", email="tester@test")

    # Now create setting change proposal on main for characters/C001.yaml
    c_hash = file_sha256(repo / "characters" / "C001.yaml")
    plan_out = {
        "type": "setting_change_plan",
        "instruction_id": "INS-0001",
        "targets": [{"target": "characters/C001.yaml", "change": "特徴追加"}],
        "new_foreshadowing": 0,
        "questions": [],
    }
    patch_out = {
        "type": "state_patch",
        "target": "characters/C001.yaml",
        "base_hash": c_hash,
        "operations": [{"op": "add", "path": "/personality/-", "value": "main変更"}],
        "reason": "main追加",
    }
    impact_out = {"type": "impact_analysis", "affected": [], "notes": []}

    run_setting_change_job(
        settings, work, instruction_id="INS-0001", instruction="カイ変更",
        claude_runner=MultiFakeClaude([plan_out, patch_out, impact_out]),
    )

    # approve_setting must raise ChapterError because branch already changed characters/C001.yaml
    with pytest.raises(ChapterError, match="already changed characters/C001.yaml"):
        approve_setting(settings, work, "INS-0001")


# ==============================================================================
# Group 5: Follow Main (Full Sequence and edge cases)
# ==============================================================================

def test_follow_main_full_sequence(tmp_path: Path) -> None:
    """Full sequence: AI_VALIDATED -> approve setting -> state-update rebases & WARNING -> approve state -> final-approve merge."""
    from test_stateupdate import validated, summary_out, char_patch
    settings, work, draft_rec = validated(tmp_path)
    repo = work.path

    # 2. Setting change on main (changes C002, does not overlap with draft branch changes)
    c2_hash = file_sha256(repo / "characters" / "C002.yaml")
    plan_out = {
        "type": "setting_change_plan",
        "instruction_id": "INS-0001",
        "targets": [{"target": "characters/C002.yaml", "change": "ミサキの性格設定追加"}],
        "new_foreshadowing": 0,
        "questions": [],
    }
    patch_out = {
        "type": "state_patch",
        "target": "characters/C002.yaml",
        "base_hash": c2_hash,
        "operations": [{"op": "add", "path": "/personality", "value": ["優しい"]}],
        "reason": "ミサキの性格追加",
    }
    impact_out = {
        "type": "impact_analysis",
        "affected": [{"chapter_id": CH, "reason": "ch-001に影響"}],
        "notes": [],
    }
    run_setting_change_job(
        settings, work, instruction_id="INS-0001", instruction="ミサキ変更",
        claude_runner=MultiFakeClaude([plan_out, patch_out, impact_out]),
    )
    app_res = approve_setting(settings, work, "INS-0001")
    assert app_res["status"] == "approved"
    main_after_setting = head_commit(repo, "main")

    # 3. state-update for chapter CH
    # Setting changed C002 on main; state-update should rebase branch, emit follow_main PASS and context_followed_main WARNING
    from test_stateupdate import good_su
    su_rec = run_state_update(
        settings, work, CH,
        claude_runner=good_su(),
    )
    assert su_rec["state"] == "WAITING_HUMAN"

    # Verify checks on su_rec:
    # follow_main PASS
    follow_chk = next(c for c in su_rec["checks"] if c["name"] == "follow_main")
    assert follow_chk["status"] == "PASS"
    assert f"rebased onto {main_after_setting[:12]}" in follow_chk["details"][0]

    # context_followed_main WARNING
    ctx_chk = next((c for c in su_rec["checks"] if c["name"] == "context_followed_main"), None)
    assert ctx_chk is not None
    assert ctx_chk["status"] == "WARNING"
    assert "characters/C002.yaml" in ctx_chk["details"]

    # 4. Human approves state proposal
    app_state_rec = approve_state(settings, work, CH)
    assert app_state_rec["state"] == "COMPLETED"

    # Clear review_required if needed
    clear_review_required(settings, work, CH)

    # 5. final-approve merge succeeds without overlap conflict
    commit = final_approve(settings, work, CH)
    assert commit is not None
    meta_final = read_chapter_meta(repo, CH)
    assert meta_final["state"] == "FINAL"


def test_follow_main_conflict_waiting_human(tmp_path: Path) -> None:
    from test_stateupdate import validated
    settings, work, draft_rec = validated(tmp_path)
    repo = work.path
    wt = Path(draft_rec["worktree"])

    # Branch modifies characters/C001.yaml
    c1_branch = load_yaml(wt / "characters" / "C001.yaml")
    c1_branch["personality"] = ["ブランチ変更"]
    (wt / "characters" / "C001.yaml").write_text(dumps_yaml(c1_branch), encoding="utf-8")
    commit_all(wt, "branch changed C001", [("NovUI-Edit", "human-content")], name="Tester", email="tester@test")

    # Main also modifies characters/C001.yaml (via direct commit for test simplicity)
    c1_main = load_yaml(repo / "characters" / "C001.yaml")
    c1_main["personality"] = ["main変更"]
    (repo / "characters" / "C001.yaml").write_text(dumps_yaml(c1_main), encoding="utf-8")
    commit_all(repo, "main changed C001", [("NovUI-Edit", "human-content")], name="Tester", email="tester@test")

    # run_state_update should detect FollowConflict and enter WAITING_HUMAN
    su_rec = run_state_update(settings, work, CH, claude_runner=MultiFakeClaude([]))
    assert su_rec["state"] == "WAITING_HUMAN"
    chk = next(c for c in su_rec["checks"] if c["name"] == "follow_main")
    assert chk["status"] == "FAIL"
    assert "characters/C001.yaml" in chk["details"]
    assert "main changed characters/C001.yaml that the job branch also changed" in su_rec["history"][-1]["reason"]


def test_approve_state_invalid_if_main_target_changed(tmp_path: Path) -> None:
    from test_stateupdate import validated, good_su
    settings, work, draft_rec = validated(tmp_path)
    repo = work.path
    wt = Path(draft_rec["worktree"])

    # Run state update to create pending proposal
    run_state_update(settings, work, CH, claude_runner=good_su())

    # Now main modifies characters/C001.yaml
    c1_main = load_yaml(repo / "characters" / "C001.yaml")
    c1_main["personality"] = ["mainで変更"]
    (repo / "characters" / "C001.yaml").write_text(dumps_yaml(c1_main), encoding="utf-8")
    commit_all(repo, "main modified C001 after proposal", [("NovUI-Edit", "human-content")], name="Tester", email="tester@test")

    # approve_state should invalidate the proposal
    with pytest.raises(ProposalError, match="characters/C001.yaml changed on main since the job branch"):
        approve_state(settings, work, CH)


# ==============================================================================
# Group 6: CLI Commands
# ==============================================================================

def test_cli_setting_commands(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    settings, work = setup_novel(tmp_path)
    repo = work.path
    c_hash = file_sha256(repo / "characters" / "C001.yaml")

    # 1. instruct --scope setting (without --chapter)
    out_routing = {
        "type": "instruction_routing",
        "instruction_id": "INS-0001",
        "scope": "setting",
        "chapter_id": None,
        "instruction": "カイの性格設定変更",
        "scope_mismatch": False,
        "scope_mismatch_reason": None,
        "proposed_jobs": [
            {
                "job_type": "setting_change",
                "summary": "カイ設定変更",
                "instruction": "特徴追加",
                "anchor": None,
                "depends_on": None,
            }
        ],
        "impact": [],
        "questions": [],
    }
    rc = cli.main(
        ["instruct", "--work", work.work_key, "--scope", "setting", "カイの性格設定変更"],
        settings=settings,
        claude_runner=MultiFakeClaude([out_routing]),
    )
    assert rc == 0
    captured = capsys.readouterr()
    assert "Routing INS-0001 [pending] for work (scope setting)" in captured.out

    # Approve routing via CLI
    rc_app = cli.main(["approve-routing", "--work", work.work_key, "--id", "INS-0001"], settings=settings)
    assert rc_app == 0

    # Run queue via CLI to run setting_change job
    plan_out = {
        "type": "setting_change_plan",
        "instruction_id": "INS-0001",
        "targets": [{"target": "characters/C001.yaml", "change": "特徴追加"}],
        "new_foreshadowing": 0,
        "questions": [],
    }
    patch_out = {
        "type": "state_patch",
        "target": "characters/C001.yaml",
        "base_hash": c_hash,
        "operations": [{"op": "add", "path": "/personality/-", "value": "冷静"}],
        "reason": "冷静を追加",
    }
    impact_out = {"type": "impact_analysis", "affected": [], "notes": ["設定変更メモ"]}
    cli.main(
        ["run-queue", "--work", work.work_key],
        settings=settings,
        claude_runner=MultiFakeClaude([plan_out, patch_out, impact_out]),
    )

    # 2. setting-proposal --work <key> --id <INS>
    rc_sp = cli.main(["setting-proposal", "--work", work.work_key, "--id", "INS-0001"], settings=settings)
    assert rc_sp == 0
    sp_out = capsys.readouterr().out
    assert "Setting Proposal INS-0001 [pending]" in sp_out
    assert "Targets: characters/C001.yaml" in sp_out
    assert "Patch: characters/C001.yaml (reason: 冷静を追加)" in sp_out
    assert "Notes: 設定変更メモ" in sp_out

    # 3. approve-setting --work <key> --id <INS>
    rc_aset = cli.main(["approve-setting", "--work", work.work_key, "--id", "INS-0001"], settings=settings)
    assert rc_aset == 0
    aset_out = capsys.readouterr().out
    assert "Setting proposal INS-0001 approved" in aset_out

    # 4. reject-setting --work <key> --id <INS> --reason <text>
    # Create another proposal first
    plan2 = copy.deepcopy(plan_out)
    plan2["instruction_id"] = "INS-0002"
    patch2 = copy.deepcopy(patch_out)
    patch2["base_hash"] = file_sha256(repo / "characters" / "C001.yaml")
    run_setting_change_job(
        settings, work, instruction_id="INS-0002", instruction="不要な変更",
        claude_runner=MultiFakeClaude([plan2, patch2, {"type": "impact_analysis", "affected": [], "notes": []}]),
    )
    rc_rset = cli.main(
        ["reject-setting", "--work", work.work_key, "--id", "INS-0002", "--reason", "不要"],
        settings=settings,
    )
    assert rc_rset == 0
    rset_out = capsys.readouterr().out
    assert "Setting proposal INS-0002 rejected" in rset_out

    # 5. cancel-job for WAITING_HUMAN setting_change job
    plan_q = {
        "type": "setting_change_plan",
        "instruction_id": "INS-0003",
        "targets": [],
        "new_foreshadowing": 0,
        "questions": ["どうしますか？"],
    }
    rec_q = run_setting_change_job(
        settings, work, instruction_id="INS-0003", instruction="質問あり",
        claude_runner=MultiFakeClaude([plan_q]),
    )
    assert rec_q["state"] == "WAITING_HUMAN"

    rc_cj = cli.main(["cancel-job", "--work", work.work_key, "--job", rec_q["job_id"]], settings=settings)
    assert rc_cj == 0
    cj_out = capsys.readouterr().out
    assert f"Job {rec_q['job_id']} finished with state: CANCELLED" in cj_out


# ==============================================================================
# Phase 3-B Review Fixes 01: R1, R2, R3
# ==============================================================================

def test_r1_patch_policy_rejects_whole_doc_and_id_override(tmp_path: Path) -> None:
    settings, work = setup_novel(tmp_path)
    repo = work.path
    c_doc = load_yaml(repo / "characters" / "C001.yaml")
    reg_doc = load_yaml(repo / "foreshadowing" / "registry.yaml")

    # 1. 伏線: {"op": "replace", "path": "", "value": [...]}
    p1 = {"operations": [{"op": "replace", "path": "", "value": []}]}
    res1 = check_setting_patch_policy(p1, reg_doc, target="foreshadowing/registry.yaml", new_foreshadow_ids=[])
    assert res1.status == "FAIL"
    assert "whole document" in res1.details[0]

    # 2. 伏線: {"op": "add", "path": "", "value": [...]}
    p2 = {"operations": [{"op": "add", "path": "", "value": []}]}
    res2 = check_setting_patch_policy(p2, reg_doc, target="foreshadowing/registry.yaml", new_foreshadow_ids=[])
    assert res2.status == "FAIL"
    assert "whole document" in res2.details[0]

    # 3. 伏線: {"op": "add", "path": "/0/id", "value": "F099"}
    p3 = {"operations": [{"op": "add", "path": "/0/id", "value": "F099"}]}
    res3 = check_setting_patch_policy(p3, reg_doc, target="foreshadowing/registry.yaml", new_foreshadow_ids=[])
    assert res3.status == "FAIL"
    assert "/0/id" in res3.details[0]

    # 4. 人物: {"op": "add", "path": "/id", "value": "C099"}
    p4 = {"operations": [{"op": "add", "path": "/id", "value": "C099"}]}
    res4 = check_setting_patch_policy(p4, c_doc, target="characters/C001.yaml", new_foreshadow_ids=[])
    assert res4.status == "FAIL"
    assert "/id" in res4.details[0]

    # 5. 人物: {"op": "add", "path": "", "value": {...}}
    p5 = {"operations": [{"op": "add", "path": "", "value": {"id": "C001", "name": "新カイ"}}]}
    res5 = check_setting_patch_policy(p5, c_doc, target="characters/C001.yaml", new_foreshadow_ids=[])
    assert res5.status == "FAIL"
    assert "whole document" in res5.details[0]


def test_r2_follow_main_corrupt_job_record_fails_closed(tmp_path: Path) -> None:
    from test_stateupdate import validated
    settings, work, draft_rec = validated(tmp_path)
    repo = work.path

    # Advance main so rebase is needed
    c2 = load_yaml(repo / "characters" / "C002.yaml")
    c2["personality"] = ["変更"]
    (repo / "characters" / "C002.yaml").write_text(dumps_yaml(c2), encoding="utf-8")
    commit_all(repo, "main advance", [("NovUI-Edit", "human-content")], name="Tester", email="tester@test")

    # Put a corrupt job record in jobs directory
    jdir = jobs_dir(settings, work.work_key)
    (jdir / "job-999.yaml").write_text("invalid: [broken yaml", encoding="utf-8")

    # follow_main should raise ChapterError without rebasing
    with pytest.raises(ChapterError, match="could not be read"):
        follow_main(settings, work, CH)


def test_r3_state_update_retry_after_conflict_revert(tmp_path: Path) -> None:
    from test_stateupdate import validated, good_su
    settings, work, draft_rec = validated(tmp_path)
    repo = work.path
    wt = Path(draft_rec["worktree"])

    # Branch modifies draft.md (normal draft edit)
    (wt / "chapters" / CH / "draft.md").write_text("ブランチの新しい本文\n", encoding="utf-8")
    commit_all(wt, "branch change draft.md", [("NovUI-Edit", "human-content")], name="Tester", email="tester@test")

    # Main erroneously modifies draft.md (creating overlap conflict)
    draft_main_orig = (repo / "chapters" / CH / "draft.md").read_text(encoding="utf-8") if (repo / "chapters" / CH / "draft.md").is_file() else None
    (repo / "chapters" / CH / "draft.md").write_text("mainの誤った本文\n", encoding="utf-8")
    commit_all(repo, "main change draft.md", [("NovUI-Edit", "human-content")], name="Tester", email="tester@test")

    # 1. run_state_update -> FollowConflict -> WAITING_HUMAN
    rec1 = run_state_update(settings, work, CH, claude_runner=good_su())
    assert rec1["state"] == "WAITING_HUMAN"
    assert rec1["checks"][-1]["name"] == "follow_main"
    assert rec1["checks"][-1]["status"] == "FAIL"
    old_job_id = rec1["job_id"]

    # 2. Main reverts change on draft.md and advances with setting change on C002
    if draft_main_orig is not None:
        (repo / "chapters" / CH / "draft.md").write_text(draft_main_orig, encoding="utf-8")
    else:
        (repo / "chapters" / CH / "draft.md").unlink()
    c2 = load_yaml(repo / "characters" / "C002.yaml")
    c2["personality"] = ["ミサキ変更"]
    (repo / "characters" / "C002.yaml").write_text(dumps_yaml(c2), encoding="utf-8")
    commit_all(repo, "main revert draft and change C002", [("NovUI-Edit", "human-content")], name="Tester", email="tester@test")

    # 3. Re-run state_update
    rec2 = run_state_update(settings, work, CH, claude_runner=good_su())
    assert rec2["state"] == "WAITING_HUMAN"
    assert rec2["job_id"] != old_job_id

    # Old job record is now CANCELLED
    rec1_after = load_job_record(jobs_dir(settings, work.work_key) / f"{old_job_id}.yaml")
    assert rec1_after["state"] == "CANCELLED"
    assert rec1_after["history"][-1]["reason"] == "retried by state-update"

    # New job reached proposal stage
    prop = pending_proposal(settings, work.work_key, CH)
    assert prop is not None
    assert prop["job_id"] == rec2["job_id"]
