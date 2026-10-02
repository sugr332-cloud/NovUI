"""Startup recovery procedures."""

from pathlib import Path

from novui.config import Settings
from novui.jobhome import cleanup_stale_job_homes
from novui.jobrecord import apply_transition, load_job_record, save_job_record
from novui.states import JobEvent, JobState

_RECOVERABLE_STATES = frozenset({
    JobState.QUEUED.name,
    JobState.RUNNING.name,
    JobState.CHECKING.name,
    JobState.VALIDATING.name,
})


def recover_on_startup(settings: Settings) -> list[str]:
    """Recover unfinished jobs on controller startup.

    Finds all job records under <data_dir>/works/*/jobs/*.yaml.
    Applies CONTROLLER_RESTARTED to any job in QUEUED, RUNNING, CHECKING, or VALIDATING.
    Calls cleanup_stale_job_homes(settings.jobhome_root).
    Returns sorted list of job_ids transitioned to STOPPED.
    """
    works_dir = settings.data_dir / "works"
    stopped_job_ids: list[str] = []

    if works_dir.is_dir():
        for work_dir in sorted(works_dir.iterdir()):
            if not work_dir.is_dir():
                continue
            jdir = work_dir / "jobs"
            if not jdir.is_dir():
                continue
            for record_file in sorted(jdir.glob("*.yaml")):
                if not record_file.is_file():
                    continue
                record = load_job_record(record_file)
                if record.get("state") in _RECOVERABLE_STATES:
                    updated = apply_transition(record, JobEvent.CONTROLLER_RESTARTED)
                    save_job_record(jdir, updated)
                    stopped_job_ids.append(record["job_id"])

    cleanup_stale_job_homes(settings.jobhome_root)
    return sorted(stopped_job_ids)
