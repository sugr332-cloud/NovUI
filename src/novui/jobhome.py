"""Job home directory management and token isolation."""

from contextlib import contextmanager
import os
from pathlib import Path
import re
import secrets
import shutil
from typing import Iterator

TOKEN_RELPATH: str = ".gemini/antigravity-cli/antigravity-oauth-token"

_JOB_ID_RE = re.compile(r"^(?:job-[0-9]+|itest-[0-9a-f]{8,})$")


class JobHomeError(Exception):
    """Raised when job home creation, token copying, or deletion fails."""


@contextmanager
def job_home(jobhome_root: Path, job_id: str, token_path: Path) -> Iterator[Path]:
    """Create an isolated, temporary home directory with copy of oauth token.

    Guarantees cleanup on with block exit. Never exposes token content in errors or logs.
    """
    if not _JOB_ID_RE.match(job_id):
        raise ValueError(f"Invalid job_id {job_id!r}; must match job-* or itest-* format")

    try:
        jobhome_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        # Ensure root itself has 700 permissions
        os.chmod(jobhome_root, 0o700)
    except OSError as exc:
        raise JobHomeError(f"Failed to create jobhome_root {jobhome_root}: {exc}") from exc

    random_hex = secrets.token_hex(4)
    home_dir = jobhome_root / f"{job_id}-{random_hex}"
    if home_dir.exists():
        raise JobHomeError(f"Job home directory already exists: {home_dir}")

    try:
        home_dir.mkdir(mode=0o700)
    except OSError as exc:
        raise JobHomeError(f"Failed to create directory {home_dir}: {exc}") from exc

    # Validate token_path
    if token_path.is_symlink() or not token_path.is_file():
        # Clean up created directory before raising
        shutil.rmtree(home_dir, ignore_errors=True)
        raise JobHomeError(f"Token file must be a regular non-symlink file: {token_path}")

    target_token = home_dir / TOKEN_RELPATH
    target_token.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    # Ensure parents have mode 700
    p = target_token.parent
    while p != home_dir:
        os.chmod(p, 0o700)
        p = p.parent

    try:
        # Copy token bytes
        token_bytes = token_path.read_bytes()
        target_token.write_bytes(token_bytes)
        target_token.chmod(0o600)
    except OSError as exc:
        shutil.rmtree(home_dir, ignore_errors=True)
        raise JobHomeError(f"Failed to copy token to {target_token}: {exc}") from exc

    exc_raised = False
    try:
        yield home_dir
    except BaseException:
        exc_raised = True
        raise
    finally:
        try:
            if home_dir.exists():
                shutil.rmtree(home_dir)
        except OSError as cleanup_exc:
            if not exc_raised:
                raise JobHomeError(f"Failed to clean up job home {home_dir}: {cleanup_exc}") from cleanup_exc


def cleanup_stale_job_homes(jobhome_root: Path) -> list[Path]:
    """Remove any orphaned directories starting with job- or itest- in jobhome_root."""
    if not jobhome_root.is_dir():
        return []

    removed: list[Path] = []
    for entry in sorted(jobhome_root.iterdir()):
        if entry.is_dir() and (entry.name.startswith("job-") or entry.name.startswith("itest-")):
            try:
                shutil.rmtree(entry)
                removed.append(entry)
            except OSError:
                pass
    return removed
