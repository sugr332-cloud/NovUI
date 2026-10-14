"""Tests for plan_revision (novui.planjob.run_plan_revision_job; fake Claude)."""

import copy
import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_planjob import _make_valid_plan, make_envelope, novel_setup  # noqa: E402,F401

from novui.chapters import ChapterError, add_chapter, read_chapter_meta
from novui.config import Settings
from novui.gitinspect import run_git
from novui.planjob import approve_plan, run_plan_job, run_plan_revision_job
from novui.procrun import ProcResult
from novui.workrepo import create_job_worktree, head_commit
from novui.works import WorkInfo
from novui.yamlio import load_yaml

CH = "ch-001"


class FakeClaude:
    def __init__(self, *outputs: bytes) -> None:
        self.outputs = list(outputs)
        self.prompts: list[str] = []

    def __call__(self, *args: Any, **kwargs: Any) -> ProcResult:
        self.prompts.append(str(kwargs.get("prompt", "")) + " ".join(map(str, kwargs.get("command", []) or [])))
        return ProcResult(0, 1.0, False, None, False, self.outputs.pop(0), b"")


def _approved(novel_setup: tuple[Settings, WorkInfo]) -> tuple[Settings, WorkInfo]:
    settings, work = novel_setup
    add_chapter(settings, work, CH, "第一章", "アウトライン本文")
    assert run_plan_job(settings, work, CH, claude_runner=FakeClaude(make_envelope(_make_valid_plan())))["state"] == "COMPLETED"
    approve_plan(settings, work, CH)
    return settings, work


def _revised_plan() -> dict[str, Any]:
    plan = _make_valid_plan()
    plan["scenes"][0]["summary"] = "改訂した場面"
    return plan


def test_plan_revision_success(novel_setup: tuple[Settings, WorkInfo]) -> None:
    settings, work = _approved(novel_setup)
    repo = work.path
    claude = FakeClaude(make_envelope(_revised_plan()))
    record = run_plan_revision_job(settings, work, CH, "S1 を短くする", instruction_id="INS-0001",
                                   claude_runner=claude)
    assert record["state"] == "COMPLETED", record["checks"]
    assert record["job_type"] == "plan_revision" and record["instruction_id"] == "INS-0001"
    assert record["edit"]["reason"] == "S1 を短くする" and record["edit"]["source"] == {"kind": "human"}
    assert f"chapters/{CH}/plan.yaml" in [c["path"] for c in record["context"]]
    meta = read_chapter_meta(repo, CH)
    assert meta["state"] == "PLANNED" and meta["last_job"] == record["job_id"]
    assert load_yaml(repo / "chapters" / CH / "plan.yaml")["scenes"][0]["summary"] == "改訂した場面"
    msg = run_git(repo, "log", "-1", "--format=%B").decode()
    assert f"revise plan {CH} by {record['job_id']}" in msg and f"NovUI-Job: {record['job_id']}" in msg
    prompt = claude.prompts[-1]
    assert "【plan の改訂】" in prompt and "S1 を短くする" in prompt
    # the revised plan is approved again before drafting
    approve_plan(settings, work, CH)
    assert read_chapter_meta(repo, CH)["state"] == "PLAN_APPROVED"


def test_plan_revision_refs_failure_keeps_main(novel_setup: tuple[Settings, WorkInfo]) -> None:
    settings, work = _approved(novel_setup)
    bad = _revised_plan()
    bad["scenes"][0]["characters"] = ["C999"]
    head0 = head_commit(work.path, "main")
    record = run_plan_revision_job(settings, work, CH, "x", claude_runner=FakeClaude(make_envelope(bad)))
    assert record["state"] == "FAILED"
    assert head_commit(work.path, "main") == head0
    assert read_chapter_meta(work.path, CH)["state"] == "PLAN_APPROVED"


def test_plan_revision_conditions(novel_setup: tuple[Settings, WorkInfo]) -> None:
    settings, work = novel_setup
    add_chapter(settings, work, CH, "第一章", "アウトライン本文")
    with pytest.raises(ChapterError, match="must be PLAN_APPROVED"):
        run_plan_revision_job(settings, work, CH, "x", claude_runner=FakeClaude())
    run_plan_job(settings, work, CH, claude_runner=FakeClaude(make_envelope(_make_valid_plan())))
    with pytest.raises(ChapterError, match="must be PLAN_APPROVED"):
        run_plan_revision_job(settings, work, CH, "x", claude_runner=FakeClaude())
    approve_plan(settings, work, CH)
    with pytest.raises(ValueError):
        run_plan_revision_job(settings, work, CH, "  ", claude_runner=FakeClaude())
    create_job_worktree(work.path, settings.worktree_root, work.work_key, CH, "job-9")
    with pytest.raises(ChapterError, match="discard it first"):
        run_plan_revision_job(settings, work, CH, "x", claude_runner=FakeClaude())


def test_revise_plan_cli(novel_setup: tuple[Settings, WorkInfo], capsys: pytest.CaptureFixture[str]) -> None:
    from novui.cli import main
    settings, work = _approved(novel_setup)
    capsys.readouterr()
    rc = main(["revise-plan", "--work", "test-novel", "--chapter", CH, "--instruction", "S1 を短く"],
              settings=settings, claude_runner=FakeClaude(make_envelope(_revised_plan())))
    out = capsys.readouterr().out
    assert rc == 0 and "finished with state: COMPLETED" in out and "run approve-plan" in out
    assert main(["revise-plan", "--work", "test-novel", "--chapter", CH, "--instruction", "x"],
                settings=settings, claude_runner=FakeClaude()) == 1
    assert "must be PLAN_APPROVED" in capsys.readouterr().err
