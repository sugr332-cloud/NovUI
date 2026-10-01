"""Tests for novui.jobrecord module."""

import copy
from pathlib import Path
import pytest

from novui.jobrecord import (
    apply_transition,
    load_job_record,
    new_job_record,
    now_iso,
    save_job_record,
)
from novui.schema import SchemaError
from novui.states import InvalidTransition, JobEvent, JobState


def test_now_iso_format() -> None:
    iso = now_iso()
    # pattern: ^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\.[0-9]+)?(Z|[+-][0-9]{2}:[0-9]{2})$
    assert "T" in iso
    assert "+" in iso or "-" in iso or iso.endswith("Z")


def test_job_record_lifecycle_and_immutability() -> None:
    t0 = "2026-10-01T23:00:00+09:00"
    rec0 = new_job_record("job-001", "draft", chapter_id="ch-001", created_at=t0)
    assert rec0["state"] == "QUEUED"
    assert rec0["started_at"] is None
    assert rec0["finished_at"] is None
    assert len(rec0["history"]) == 1
    assert rec0["history"][0] == {"state": "QUEUED", "event": None, "at": t0, "reason": None}

    # QUEUED -> RUNNING (LOCK_ACQUIRED)
    t1 = "2026-10-01T23:01:00+09:00"
    rec1 = apply_transition(rec0, JobEvent.LOCK_ACQUIRED, at=t1)
    # 元の rec0 は不変
    assert rec0["state"] == "QUEUED"
    assert rec0["started_at"] is None
    assert len(rec0["history"]) == 1

    assert rec1["state"] == "RUNNING"
    assert rec1["started_at"] == t1
    assert rec1["finished_at"] is None
    assert len(rec1["history"]) == 2
    assert rec1["history"][1] == {
        "state": "RUNNING",
        "event": "LOCK_ACQUIRED",
        "at": t1,
        "reason": None,
    }

    # RUNNING -> CHECKING (CLI_EXITED)
    t2 = "2026-10-01T23:02:00+09:00"
    rec2 = apply_transition(rec1, JobEvent.CLI_EXITED, at=t2)
    assert rec2["state"] == "CHECKING"
    assert rec2["started_at"] == t1
    assert rec2["finished_at"] is None
    assert len(rec2["history"]) == 3

    # CHECKING -> COMPLETED (CHECK_PASSED, needs_validation=False)
    t3 = "2026-10-01T23:03:00+09:00"
    rec3 = apply_transition(rec2, JobEvent.CHECK_PASSED, needs_validation=False, at=t3)
    assert rec3["state"] == "COMPLETED"
    assert rec3["started_at"] == t1
    assert rec3["finished_at"] == t3
    assert len(rec3["history"]) == 4
    assert rec3["history"][3] == {
        "state": "COMPLETED",
        "event": "CHECK_PASSED",
        "at": t3,
        "reason": None,
    }


def test_invalid_transition_propagates() -> None:
    rec = new_job_record("job-002", "plan", chapter_id="ch-001")
    # QUEUED 状態から CLI_EXITED は不正
    with pytest.raises(InvalidTransition):
        apply_transition(rec, JobEvent.CLI_EXITED)


def test_save_and_load_job_record(tmp_path: Path) -> None:
    rec = new_job_record("job-003", "plan", chapter_id="ch-001")
    saved_path = save_job_record(tmp_path, rec)
    assert saved_path == tmp_path / "job-003.yaml"
    assert saved_path.is_file()

    loaded = load_job_record(saved_path)
    assert loaded == rec


def test_save_job_record_validation_failure_creates_no_file(tmp_path: Path) -> None:
    rec = new_job_record("job-004", "plan", chapter_id="ch-001")
    # state を書き換えて history と矛盾させる (semantics 違反)
    corrupted = copy.deepcopy(rec)
    corrupted["state"] = "RUNNING"
    # history は QUEUED のまま

    with pytest.raises(SchemaError):
        save_job_record(tmp_path, corrupted)

    # ファイルが作成されていないことを確認
    assert not (tmp_path / "job-004.yaml").exists()
