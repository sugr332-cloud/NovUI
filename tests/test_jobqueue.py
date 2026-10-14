"""Tests for novui.jobqueue (queue, fix from findings, run_queue; fake AGY / Claude)."""

import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_requestflow import CaptureAgy  # noqa: E402
from test_validatejob import CH, GOOD_TEXT, drafted, integrity, ok_claude, writing  # noqa: E402

from novui.chapters import ChapterError, read_chapter_meta
from novui.jobqueue import (
    QueueBusy,
    cancel_item,
    enqueue,
    fix_findings,
    load_queue,
    new_item,
    numbered_findings,
    queue_lock,
    run_queue,
)
from novui.jobrecord import load_job_record
from novui.jobrunner import jobs_dir
from novui.requestflow import resolve_request
from novui.validatejob import decide_validation, run_validate_job


@pytest.fixture(autouse=True)
def mock_container_meta(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("novui.jobrunner.image_label", lambda img, key: "1.2.14")
    monkeypatch.setattr(
        "novui.jobrunner.image_id",
        lambda img: "sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
    )


DRAFT = f"chapters/{CH}/draft.md"
NEW1 = "門番は紋章を見て、黙って道をあけた。"
NEW2 = "カイは夕暮れの港町ミナトに着いた。"


class SeqAgy(CaptureAgy):
    """Returns the given texts in order."""

    def __init__(self, *texts: str) -> None:
        super().__init__("")
        self.texts = list(texts)

    def __call__(self, **kwargs: Any) -> Any:
        self.text = self.texts.pop(0)
        return super().__call__(**kwargs)


def _request_fix(tmp_path: Path) -> tuple[Any, Any, dict[str, Any]]:
    """A draft whose review has I1 (WARNING, anchored) and W1, decided REQUEST_FIX."""
    settings, work, rec = drafted(tmp_path)
    v = run_validate_job(settings, work, CH, claude_runner=ok_claude(integrity("WARNING"), writing(1)))
    assert v["state"] == "WAITING_HUMAN"
    decide_validation(settings, work, CH, "REQUEST_FIX", "一人称を直す")
    return settings, work, rec


# ---------- 指摘の番号 ----------

def test_numbered_findings() -> None:
    integ = integrity("WARNING", "plot")
    integ["checks"]["character"] = {"result": "WARNING", "findings": [
        {"severity": "WARNING", "anchor": None, "message": "人物", "character": None, "evidence": []}]}
    found = numbered_findings({"integrity": integ, "writing": writing(2)})
    assert list(found) == ["I1", "I2", "W1", "W2"]
    assert found["I1"]["check"] == "character" and found["I1"]["anchor"] is None  # character comes before plot
    assert found["I2"]["check"] == "plot" and found["I2"]["severity"] == "WARNING"
    assert found["W1"]["kind"] == "writing" and found["W1"]["message"] == "冗長"
    assert numbered_findings({"integrity": None, "writing": None}) == {}


# ---------- fix ----------

def test_fix_and_run_queue(tmp_path: Path) -> None:
    settings, work, rec = _request_fix(tmp_path)
    wt = Path(rec["worktree"])
    items = fix_findings(settings, work, CH, ["I1", "W1"], note="短めに")
    assert [i["item_id"] for i in items] == ["Q-0001", "Q-0002"]
    assert items[0]["anchor"]["text"] == "門番が紋章に目を留めた。"
    assert items[0]["source"]["finding"] == "I1" and items[0]["source"]["kind"] == "finding"
    assert "検証の指摘（I1）：C001 speech.first_person" in items[0]["instruction"]
    assert "Human の指示：一人称を直す" in items[0]["instruction"]
    assert "Human の補足：短めに" in items[0]["instruction"]

    agy = SeqAgy(NEW1, NEW2)
    msgs: list[str] = []
    records = run_queue(settings, work, container_runner=agy, report=msgs.append)
    assert [r["state"] for r in records] == ["COMPLETED", "COMPLETED"]
    assert [r["edit"]["source"]["finding"] for r in records] == ["I1", "W1"]
    assert (wt / DRAFT).read_text(encoding="utf-8") == (
        GOOD_TEXT.replace("門番が紋章に目を留めた。", NEW1).replace("カイは港町ミナトに着いた。", NEW2)
    )
    assert [i["status"] for i in load_queue(settings, work.work_key)] == ["done", "done"]
    assert msgs[-1] == "The queue is empty."
    assert read_chapter_meta(wt, CH)["state"] == "DRAFTED"


def test_fix_conditions(tmp_path: Path) -> None:
    settings, work, _ = drafted(tmp_path)
    with pytest.raises(ChapterError, match="no review.yaml"):
        fix_findings(settings, work, CH, ["I1"])
    v = run_validate_job(settings, work, CH, claude_runner=ok_claude(integrity("WARNING"), writing(1)))
    assert v["state"] == "WAITING_HUMAN"
    with pytest.raises(ChapterError, match="REQUEST_FIX"):
        fix_findings(settings, work, CH, ["I1"])
    decide_validation(settings, work, CH, "REQUEST_FIX", "直す")
    with pytest.raises(ChapterError, match="not found in the review"):
        fix_findings(settings, work, CH, ["I9"])
    with pytest.raises(ValueError):
        fix_findings(settings, work, CH, [])


def test_fix_writing_findings_on_ai_validated(tmp_path: Path) -> None:
    settings, work, rec = drafted(tmp_path)
    v = run_validate_job(settings, work, CH, claude_runner=ok_claude(integrity(), writing(1)))
    assert v["state"] == "COMPLETED"
    items = fix_findings(settings, work, CH, ["W1"])
    assert "Human の指示" not in items[0]["instruction"]
    records = run_queue(settings, work, container_runner=CaptureAgy(NEW2))
    assert records[0]["state"] == "COMPLETED"
    assert read_chapter_meta(Path(rec["worktree"]), CH)["state"] == "DRAFTED"


def test_fix_finding_without_anchor(tmp_path: Path) -> None:
    settings, work, _ = drafted(tmp_path)
    integ = integrity("WARNING")
    integ["checks"]["character"]["findings"][0]["anchor"] = None
    run_validate_job(settings, work, CH, claude_runner=ok_claude(integ))
    decide_validation(settings, work, CH, "REQUEST_FIX", "直す")
    with pytest.raises(ChapterError, match="has no anchor"):
        fix_findings(settings, work, CH, ["I1"])


def test_fix_round_limit(tmp_path: Path) -> None:
    settings, work, _ = drafted(tmp_path)
    texts = ["門番が紋章に目を留めた。"] * 3  # each edit keeps the text so the anchor matches again

    def round_() -> None:
        v = run_validate_job(settings, work, CH, claude_runner=ok_claude(integrity("WARNING")))
        assert v["state"] == "WAITING_HUMAN"
        decide_validation(settings, work, CH, "REQUEST_FIX", "直す")

    for _ in range(2):
        round_()
        fix_findings(settings, work, CH, ["I1"])
        fix_findings(settings, work, CH, ["I1"])  # the same review again is the same round
        assert run_queue(settings, work, container_runner=CaptureAgy(texts.pop()))[-1]["state"] == "COMPLETED"
    round_()
    with pytest.raises(ChapterError, match="2 fix rounds"):
        fix_findings(settings, work, CH, ["I1"])
    assert len(fix_findings(settings, work, CH, ["I1"], force=True)) == 1


# ---------- run_queue ----------

def test_run_queue_stops_at_waiting_and_retries(tmp_path: Path) -> None:
    settings, work, rec = _request_fix(tmp_path)
    fix_findings(settings, work, CH, ["I1", "W1"])
    msgs: list[str] = []
    records = run_queue(settings, work, container_runner=CaptureAgy("門番は【要確認：門番の名前】と名乗った。"),
                        report=msgs.append)
    assert [r["state"] for r in records] == ["WAITING_HUMAN"]
    q = load_queue(settings, work.work_key)
    assert [i["status"] for i in q] == ["waiting", "pending"]
    assert q[0]["note"] == "undefined_settings: 1" and q[0]["job_id"] == records[0]["job_id"]

    # without --retry it stops at the waiting item
    assert run_queue(settings, work, container_runner=CaptureAgy(NEW1), report=msgs.append) == []
    assert "is waiting" in msgs[-1] and "run-queue --retry" in msgs[-1]
    # with --retry but the request still open: not run, the item stays waiting
    assert run_queue(settings, work, retry=True, container_runner=CaptureAgy(NEW1), report=msgs.append) == []
    assert "not resolved" in msgs[-1]
    assert load_queue(settings, work.work_key)[0]["status"] == "waiting"

    resolve_request(settings, work, CH, 0, "名前は付けない")
    agy = SeqAgy(NEW1, NEW2)
    records = run_queue(settings, work, retry=True, container_runner=agy)
    assert [r["state"] for r in records] == ["COMPLETED", "COMPLETED"]
    assert "名前は付けない" in agy.prompt(0)
    old = load_job_record(jobs_dir(settings, work.work_key) / f"{q[0]['job_id']}.yaml")
    assert old["state"] == "CANCELLED"
    assert [i["status"] for i in load_queue(settings, work.work_key)] == ["done", "done"]


def test_run_queue_failed_and_cancel(tmp_path: Path) -> None:
    settings, work, _ = _request_fix(tmp_path)
    fix_findings(settings, work, CH, ["I1", "W1"])
    records = run_queue(settings, work, container_runner=CaptureAgy(""))
    assert records[0]["state"] == "FAILED"
    assert [i["status"] for i in load_queue(settings, work.work_key)] == ["failed", "pending"]
    cancel_item(settings, work.work_key, "Q-0001")
    with pytest.raises(ChapterError, match="cannot be cancelled"):
        cancel_item(settings, work.work_key, "Q-0001")
    with pytest.raises(ChapterError, match="not found"):
        cancel_item(settings, work.work_key, "Q-0099")
    records = run_queue(settings, work, container_runner=CaptureAgy(NEW2))
    assert [r["edit"]["source"]["finding"] for r in records] == ["W1"]


def test_run_queue_draft_waits_for_plan_approval(tmp_path: Path) -> None:
    from test_validatejob import setup_work
    from novui.chapters import transition_on_main
    from novui.states import ChapterEvent
    settings, work = setup_work(tmp_path)
    transition_on_main(settings, work, CH, ChapterEvent.PLAN_REVISED, subject="revise")  # -> PLANNED
    enqueue(settings, work.work_key, [new_item("draft", CH, instruction="", source={"kind": "human"},
                                               instruction_id="INS-0001")])
    msgs: list[str] = []
    assert run_queue(settings, work, container_runner=CaptureAgy(GOOD_TEXT), report=msgs.append) == []
    assert "waits for approve-plan" in msgs[-1]
    from novui.planjob import approve_plan
    approve_plan(settings, work, CH)
    records = run_queue(settings, work, container_runner=CaptureAgy(GOOD_TEXT))
    assert records[0]["state"] == "COMPLETED" and records[0]["instruction_id"] == "INS-0001"


def test_queue_lock_is_exclusive(tmp_path: Path) -> None:
    settings, work, _ = drafted(tmp_path)
    with queue_lock(settings, work.work_key):
        with pytest.raises(QueueBusy):
            run_queue(settings, work)
    assert run_queue(settings, work) == []
