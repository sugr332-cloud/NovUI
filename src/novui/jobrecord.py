"""Job record management and state tracking."""

import copy
from datetime import datetime
from pathlib import Path
from typing import Any

from novui.schema import SchemaError, validate_or_raise
from novui.semantics import check_job_record
from novui.states import JobEvent, JobState, TERMINAL_JOB_STATES, next_job_state
from novui.yamlio import load_yaml, write_yaml_atomic


def now_iso() -> str:
    """Return current local timestamp in ISO 8601 format with second precision."""
    return datetime.now().astimezone().replace(microsecond=0).isoformat()


def new_job_record(
    job_id: str,
    job_type: str,
    *,
    chapter_id: str | None,
    created_at: str | None = None,
) -> dict[str, Any]:
    """Create a new job record dictionary in QUEUED state."""
    at = created_at if created_at is not None else now_iso()
    return {
        "job_id": job_id,
        "job_type": job_type,
        "chapter_id": chapter_id,
        "state": JobState.QUEUED.name,
        "created_at": at,
        "started_at": None,
        "finished_at": None,
        "base_commit": None,
        "branch": None,
        "worktree": None,
        "instruction_id": None,
        "approval_id": None,
        "context": [],
        "cli": None,
        "container": None,
        "run": None,
        "checks": [],
        "attempt": 1,
        "history": [
            {
                "state": JobState.QUEUED.name,
                "event": None,
                "at": at,
                "reason": None,
            }
        ],
    }


def apply_transition(
    record: dict[str, Any],
    event: JobEvent,
    *,
    needs_validation: bool | None = None,
    reason: str | None = None,
    at: str | None = None,
) -> dict[str, Any]:
    """Apply a state transition to a job record, returning a new record dictionary.

    Original record is not modified.
    Updates started_at on entering RUNNING (if currently null).
    Updates finished_at on entering any terminal state.
    """
    current_state = JobState[record["state"]]
    next_st = next_job_state(
        current_state,
        event,
        needs_validation=needs_validation,
        reason=reason,
    )

    at_str = at if at is not None else now_iso()
    new_rec = copy.deepcopy(record)
    new_rec["state"] = next_st.name
    new_rec["history"].append({
        "state": next_st.name,
        "event": event.name,
        "at": at_str,
        "reason": reason,
    })

    if next_st == JobState.RUNNING and new_rec.get("started_at") is None:
        new_rec["started_at"] = at_str

    if next_st in TERMINAL_JOB_STATES:
        new_rec["finished_at"] = at_str

    return new_rec


def _validate_record(record: dict[str, Any]) -> None:
    validate_or_raise(record, "job_record")
    sem_errors = check_job_record(record)
    if sem_errors:
        raise SchemaError(
            f"Semantic validation failed for job_record: {sem_errors}",
            errors=sem_errors,
        )


def save_job_record(directory: Path, record: dict[str, Any]) -> Path:
    """Validate and atomically save job record to <directory>/<job_id>.yaml.

    Raises SchemaError if validation fails, without writing any file.
    """
    _validate_record(record)
    job_id = record["job_id"]
    target_path = directory / f"{job_id}.yaml"
    write_yaml_atomic(target_path, record)
    return target_path


def load_job_record(path: Path) -> dict[str, Any]:
    """Read and validate a job record file.

    Raises SchemaError if validation fails.
    """
    data = load_yaml(path)
    if not isinstance(data, dict):
        raise SchemaError("Job record must be a YAML mapping", errors=["root is not a mapping"])
    _validate_record(data)
    return data
