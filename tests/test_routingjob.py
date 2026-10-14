"""Tests for novui.routingjob (instruction_routing; fake Claude and AGY)."""

import json
import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_requestflow import CaptureAgy  # noqa: E402
from test_validatejob import CH, GOOD_TEXT, drafted, envelope, setup_work  # noqa: E402

from novui.chapters import ChapterError, read_chapter_meta
from novui.jobqueue import load_queue, run_queue
from novui.procrun import ProcResult
from novui.routingjob import (
    approve_routing,
    check_proposed_jobs,
    load_routing,
    next_instruction_id,
    reject_routing,
    routing_items,
    routings_dir,
    run_routing_job,
)
from novui.workrepo import chapter_branches, commit_all


@pytest.fixture(autouse=True)
def mock_container_meta(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("novui.jobrunner.image_label", lambda img, key: "1.2.14")
    monkeypatch.setattr(
        "novui.jobrunner.image_id",
        lambda img: "sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
    )


ANCHOR = {"text": "門番が紋章に目を留めた。", "before": "", "after": ""}
NEW = "門番は紋章を見て、黙って道をあけた。"


def job(job_type: str, *, anchor: Any = None, depends_on: Any = None, text: str = "具体的な指示") -> dict[str, Any]:
    return {"job_type": job_type, "summary": f"{job_type} の案", "instruction": text, "anchor": anchor,
            "depends_on": depends_on}


def routing(*jobs: dict[str, Any], scope: str = "chapter", questions: Any = None,
            mismatch: bool = False) -> dict[str, Any]:
    return {
        "type": "instruction_routing", "instruction_id": "INS-9999", "scope": scope, "chapter_id": CH,
        "instruction": "写し間違い", "scope_mismatch": mismatch,
        "scope_mismatch_reason": "広い" if mismatch else None,
        "proposed_jobs": list(jobs), "impact": [], "questions": questions or [],
    }


class FakeClaude:
    def __init__(self, out: dict[str, Any]) -> None:
        self.out = out
        self.prompts: list[str] = []

    def __call__(self, **kwargs: Any) -> ProcResult:
        self.prompts.append(kwargs["prompt"])
        self.schema = json.loads(kwargs["json_schema"])
        return ProcResult(0, 1.0, False, None, False, envelope(self.out), b"")


# ---------- 純粋関数 ----------

def test_check_proposed_jobs() -> None:
    assert check_proposed_jobs([job("range_edit", anchor=ANCHOR), job("chapter_rewrite", depends_on=0)]) == []
    problems = check_proposed_jobs([job("range_edit"), job("chapter_rewrite", anchor=ANCHOR), job("validate")])
    assert problems == [
        "proposed_jobs[0]: range_edit needs an anchor",
        "proposed_jobs[1]: only range_edit takes an anchor",
        "proposed_jobs[2]: validate cannot be proposed (use range_edit, chapter_rewrite, plan_revision)",
    ]


def test_routing_items_turns_rewrite_after_revision_into_draft() -> None:
    doc = {"instruction_id": "INS-0001", "chapter_id": CH, "instruction": "テンポを上げる",
           "routing": routing(job("plan_revision"), job("chapter_rewrite", depends_on=0))}
    items = routing_items(doc)
    assert [i["job_type"] for i in items] == ["plan_revision", "draft"]
    assert items[0]["instruction"] == "具体的な指示\n\nHuman の元の指示：テンポを上げる"
    assert all(i["instruction_id"] == "INS-0001" and i["source"] == {"kind": "instruction", "instruction_id": "INS-0001"}
               for i in items)
    doc["routing"] = routing(job("plan_revision"), job("range_edit", anchor=ANCHOR))
    with pytest.raises(ChapterError, match="after plan_revision"):
        routing_items(doc)


def test_next_instruction_id_is_not_reused(tmp_path: Path) -> None:
    settings, work = setup_work(tmp_path)
    assert next_instruction_id(settings, work.work_key) == "INS-0001"
    assert next_instruction_id(settings, work.work_key) == "INS-0002"


# ---------- run_routing_job / approve / reject ----------

def test_routing_range_edit_approve_and_run(tmp_path: Path) -> None:
    settings, work, rec = drafted(tmp_path)
    claude = FakeClaude(routing(job("range_edit", anchor=ANCHOR, text="門番の反応を描く"), scope="range"))
    record, doc = run_routing_job(settings, work, CH, "range", "ここを直して", anchor={"text": "門番が紋章に目を留めた。"},
                                  claude_runner=claude)
    assert record["state"] == "COMPLETED" and record["job_type"] == "routing"
    assert record["instruction_id"] == "INS-0001"
    assert doc is not None and doc["status"] == "pending" and doc["base"]["ref"] == rec["branch"]
    assert doc["routing"]["instruction"] == "ここを直して" and doc["routing"]["instruction_id"] == "INS-0001"
    assert f"chapters/{CH}/draft.md" in [c["path"] for c in record["context"]]
    prompt = claude.prompts[-1]
    assert "ここを直して" in prompt and "instruction_id：`INS-0001`" in prompt
    assert "```text\n門番が紋章に目を留めた。\n```" in prompt and "DRAFTED（本文あり）" in prompt
    assert "{{" not in prompt
    # the CLI schema requires the added fields
    item_schema = claude.schema["properties"]["proposed_jobs"]["items"]
    assert {"anchor", "instruction"} <= set(item_schema["required"])

    queued = approve_routing(settings, work, "INS-0001")
    assert [i["job_type"] for i in queued] == ["range_edit"]
    assert load_routing(settings, work.work_key, "INS-0001")["status"] == "approved"
    with pytest.raises(ChapterError, match="only a pending routing"):
        approve_routing(settings, work, "INS-0001")
    with pytest.raises(ChapterError, match="already approved"):
        reject_routing(settings, work, "INS-0001")

    agy = CaptureAgy(NEW)
    records = run_queue(settings, work, container_runner=agy)
    assert records[0]["state"] == "COMPLETED" and records[0]["instruction_id"] == "INS-0001"
    assert records[0]["edit"]["source"] == {"kind": "instruction", "instruction_id": "INS-0001"}
    assert "門番の反応を描く" in agy.prompt() and "Human の元の指示：ここを直して" in agy.prompt()


def test_routing_questions_wait_and_reject(tmp_path: Path) -> None:
    settings, work, _ = drafted(tmp_path)
    record, doc = run_routing_job(settings, work, CH, "chapter", "いい感じに",
                                  claude_runner=FakeClaude(routing(questions=["どこをどう変えますか"])))
    assert record["state"] == "WAITING_HUMAN" and record["history"][-1]["reason"] == "questions: 1"
    assert doc is not None and doc["status"] == "questions"
    with pytest.raises(ChapterError, match="only a pending routing"):
        approve_routing(settings, work, doc["instruction_id"])
    reject_routing(settings, work, doc["instruction_id"])
    assert not (routings_dir(settings, work.work_key) / f"{doc['instruction_id']}.yaml").exists()
    from novui.jobrecord import load_job_record
    from novui.jobrunner import jobs_dir
    assert load_job_record(jobs_dir(settings, work.work_key) / f"{record['job_id']}.yaml")["state"] == "CANCELLED"
    # the ID is not reused
    _, doc2 = run_routing_job(settings, work, CH, "chapter", "短く",
                              claude_runner=FakeClaude(routing(job("chapter_rewrite"))))
    assert doc2 is not None and doc2["instruction_id"] == "INS-0002"


@pytest.mark.parametrize("out, detail", [
    (routing(job("validate")), "validate cannot be proposed"),
    (routing(job("chapter_rewrite"), scope="range"), "scope range allows only range_edit"),
    (routing(), "neither proposed_jobs nor questions"),
])
def test_routing_invalid_proposal_fails(tmp_path: Path, out: dict[str, Any], detail: str) -> None:
    settings, work, _ = drafted(tmp_path)
    anchor = ANCHOR if out["scope"] == "range" else None
    record, doc = run_routing_job(settings, work, CH, out["scope"], "x", anchor=anchor, claude_runner=FakeClaude(out))
    assert record["state"] == "FAILED" and doc is None
    check = next(c for c in record["checks"] if c["name"] == "routing_jobs")
    assert any(detail in d for d in check["details"])


def test_routing_semantic_error_fails_in_claude_output(tmp_path: Path) -> None:
    settings, work, _ = drafted(tmp_path)
    out = routing(job("chapter_rewrite"), questions=["?"])
    record, doc = run_routing_job(settings, work, CH, "chapter", "x", claude_runner=FakeClaude(out))
    assert record["state"] == "FAILED" and doc is None
    assert not (routings_dir(settings, work.work_key) / f"{record['instruction_id']}.yaml").exists()


def test_routing_scope_mismatch_allows_other_jobs(tmp_path: Path) -> None:
    settings, work, _ = drafted(tmp_path)
    out = routing(job("chapter_rewrite"), scope="range", mismatch=True)
    record, doc = run_routing_job(settings, work, CH, "range", "x", anchor=ANCHOR, claude_runner=FakeClaude(out))
    assert record["state"] == "COMPLETED" and doc is not None and doc["routing"]["scope_mismatch"] is True


def test_routing_argument_errors(tmp_path: Path) -> None:
    settings, work = setup_work(tmp_path)
    claude = FakeClaude(routing(job("plan_revision")))
    with pytest.raises(ValueError, match="scope"):
        run_routing_job(settings, work, CH, "setting", "x", claude_runner=claude)
    with pytest.raises(ValueError, match="needs an anchor"):
        run_routing_job(settings, work, CH, "range", "x", claude_runner=claude)
    with pytest.raises(ValueError, match="only scope range"):
        run_routing_job(settings, work, CH, "chapter", "x", anchor=ANCHOR, claude_runner=claude)
    with pytest.raises(ChapterError, match="no draft"):
        run_routing_job(settings, work, CH, "range", "x", anchor=ANCHOR, claude_runner=claude)


def test_routing_stale_after_change(tmp_path: Path) -> None:
    settings, work, rec = drafted(tmp_path)
    _, doc = run_routing_job(settings, work, CH, "chapter", "短く",
                             claude_runner=FakeClaude(routing(job("chapter_rewrite"))))
    assert doc is not None
    wt = Path(rec["worktree"])
    (wt / "notes.txt").write_text("x", encoding="utf-8")
    commit_all(wt, "change", [("NovUI-Job", rec["job_id"])], name="Tester", email="tester@test")
    with pytest.raises(ChapterError, match="run instruct again"):
        approve_routing(settings, work, doc["instruction_id"])


def test_routing_plan_revision_discards_branch_then_drafts(tmp_path: Path) -> None:
    from test_planjob import make_envelope
    from test_validatejob import make_plan
    settings, work, rec = drafted(tmp_path)
    out = routing(job("plan_revision", text="S1 を短く"), job("chapter_rewrite", depends_on=0))
    _, doc = run_routing_job(settings, work, CH, "plan", "テンポを上げる", claude_runner=FakeClaude(out))
    assert doc is not None and doc["base"]["ref"] == rec["branch"]
    with pytest.raises(ChapterError, match="--discard-branch"):
        approve_routing(settings, work, doc["instruction_id"])
    assert chapter_branches(work.path, CH)
    queued = approve_routing(settings, work, doc["instruction_id"], discard_branch=True)
    assert [i["job_type"] for i in queued] == ["plan_revision", "draft"]
    assert chapter_branches(work.path, CH) == []

    def claude(**kwargs: Any) -> ProcResult:
        return ProcResult(0, 1.0, False, None, False, make_envelope(make_plan()), b"")

    msgs: list[str] = []
    records = run_queue(settings, work, claude_runner=claude, container_runner=CaptureAgy(GOOD_TEXT), report=msgs.append)
    assert [r["job_type"] for r in records] == ["plan_revision"]
    assert read_chapter_meta(work.path, CH)["state"] == "PLANNED"
    assert "waits for approve-plan" in msgs[-1]
    from novui.planjob import approve_plan
    approve_plan(settings, work, CH)
    records = run_queue(settings, work, container_runner=CaptureAgy(GOOD_TEXT))
    assert [r["job_type"] for r in records] == ["draft"] and records[0]["state"] == "COMPLETED"
    assert records[0]["instruction_id"] == doc["instruction_id"]
    assert [i["status"] for i in load_queue(settings, work.work_key)] == ["done", "done"]


def test_routing_cli(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from novui.cli import main
    settings, work, _ = drafted(tmp_path)
    capsys.readouterr()
    base = ["--work", "test-novel"]
    out_doc = routing(job("range_edit", anchor=ANCHOR, text="門番の反応"), scope="range")
    rc = main(["instruct", *base, "--chapter", CH, "--scope", "range", "--anchor", "門番が紋章に目を留めた。",
               "ここを直して"], settings=settings, claude_runner=FakeClaude(out_doc))
    out = capsys.readouterr().out
    assert rc == 0
    assert "Routing INS-0001 [pending] for ch-001 (scope range)" in out
    assert "  [0] range_edit: range_edit の案" in out and "range: 門番が紋章に目を留めた。" in out
    assert "approve-routing --id INS-0001" in out

    assert main(["instruct", *base, "--chapter", CH, "--scope", "setting", "x"], settings=settings) == 1
    capsys.readouterr()
    rc = main(["instruct", *base, "--chapter", CH, "--scope", "chapter", "いい感じに"], settings=settings,
              claude_runner=FakeClaude(routing(questions=["どこを？"])))
    out = capsys.readouterr().out
    assert rc == 3 and "Question: どこを？" in out and "INS-0002 [questions]" in out
    assert main(["approve-routing", *base, "--id", "INS-0002"], settings=settings) == 1
    capsys.readouterr()
    assert main(["reject-routing", *base, "--id", "INS-0002"], settings=settings) == 0
    assert "INS-0002 rejected and removed" in capsys.readouterr().out

    assert main(["approve-routing", *base, "--id", "INS-0001"], settings=settings) == 0
    assert "Queued Q-0001: range_edit ch-001" in capsys.readouterr().out
    assert main(["run-queue", *base], settings=settings, container_runner=CaptureAgy(NEW)) == 0
    assert "finished with state: COMPLETED" in capsys.readouterr().out
