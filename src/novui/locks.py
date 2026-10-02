"""Locking mechanisms for novel projects and chapters."""

from __future__ import annotations

from pathlib import Path
import threading

from novui.workrepo import chapter_branches


class LockError(Exception):
    """Raised when an invalid lock operation is attempted."""


class RunLock:
    """Per-work execution lock (§18.1). Only one AGY Job can run per novel work concurrently."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._holders: dict[str, str] = {}

    def acquire(self, work_key: str, job_id: str) -> bool:
        """Attempt to acquire execution lock for work_key. Returns True if acquired, False if held by another job."""
        with self._lock:
            current = self._holders.get(work_key)
            if current is None or current == job_id:
                self._holders[work_key] = job_id
                return True
            return False

    def release(self, work_key: str, job_id: str) -> None:
        """Release execution lock for work_key. Raises LockError if not held by job_id."""
        with self._lock:
            current = self._holders.get(work_key)
            if current != job_id:
                raise LockError(f"Job {job_id!r} is not the current holder for work {work_key!r} (held by: {current!r})")
            del self._holders[work_key]

    def holder(self, work_key: str) -> str | None:
        """Return the current lock holder job_id for work_key, or None if unlocked."""
        with self._lock:
            return self._holders.get(work_key)


def can_accept_write_job(repo: Path, chapter_id: str) -> bool:
    """Check if chapter can accept a new write job (§18.1 chapter lock). True if no unmerged chapter branches exist."""
    return len(chapter_branches(repo, chapter_id)) == 0
