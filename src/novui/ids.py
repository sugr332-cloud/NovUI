"""ID generator utilities for NovUI."""

from pathlib import Path
import re

from novui.config import Settings
from novui.jobrunner import jobs_dir

_JOB_FILE_RE = re.compile(r"^job-([0-9]+)\.yaml$")


def next_job_id(settings: Settings, work_key: str) -> str:
    """Return next sequential job ID for work_key: 'job-<max+1>' (or 'job-1' if none exist)."""
    jdir = jobs_dir(settings, work_key)
    max_num = 0
    if jdir.is_dir():
        for p in jdir.iterdir():
            if p.is_file():
                m = _JOB_FILE_RE.match(p.name)
                if m:
                    num = int(m.group(1))
                    if num > max_num:
                        max_num = num

    return f"job-{max_num + 1}"
