"""Approval records (§15.4): numbering, construction, applicability and use checks."""

from __future__ import annotations

import fcntl
import os
from pathlib import Path
import re
import threading
from typing import Any, Mapping, Sequence

from novui.config import Settings
from novui.gitinspect import GitError, run_git
from novui.jobrecord import now_iso
from novui.schema import SchemaError, validate_or_raise
from novui.semantics import check_approval
from novui.statepatch import patch_set_sha256
from novui.works import WorkInfo


class ApprovalError(Exception):
    """Raised when an approval record cannot be used for the given patches."""


APPROVALS_DIR: str = ".novui/approvals"

_ID_RE = re.compile(r"^A-[0-9]{4,}$")
_FILE_RE = re.compile(r"^A-([0-9]{4,})\.yaml$")
_THREAD_LOCK = threading.Lock()


def approval_rel(approval_id: str) -> str:
    """Return the repository-relative path of the approval record: .novui/approvals/<approval_id>.yaml."""
    if not isinstance(approval_id, str) or not _ID_RE.match(approval_id):
        raise ValueError(f"Invalid approval_id: {approval_id!r}")
    return f"{APPROVALS_DIR}/{approval_id}.yaml"


def _max_number(names: Sequence[str]) -> int:
    best = 0
    for name in names:
        m = _FILE_RE.match(name)
        if m:
            best = max(best, int(m.group(1)))
    return best


def _max_on_main(repo: Path) -> int:
    """Highest approval number recorded on main (read from git, independent of the checkout)."""
    try:
        out = run_git(repo, "ls-tree", "-r", "--name-only", "main", "--", APPROVALS_DIR)
    except GitError:
        return 0
    names = [line.rsplit("/", 1)[-1] for line in out.decode("utf-8").splitlines() if line.strip()]
    return _max_number(names)


def _max_in_dir(directory: Path) -> int:
    if not directory.is_dir():
        return 0
    return _max_number([p.name for p in directory.iterdir() if p.is_file()])


def reserve_approval_id(settings: Settings, work: WorkInfo, *, extra_dirs: Sequence[Path] = ()) -> str:
    """Reserve the next approval ID of the work: 'A-' + zero-padded number (at least 4 digits).

    The number is max(counter, highest on main + 1, highest in extra_dirs + 1). The counter
    (<data_dir>/works/<work_key>/approvals.next) is advanced before returning and numbers are never reused,
    even if the caller fails afterwards. Exclusive across threads and processes (flock).
    """
    base = settings.data_dir / "works" / work.work_key
    base.mkdir(parents=True, exist_ok=True)
    counter_path = base / "approvals.next"
    lock_path = base / "approvals.lock"

    with _THREAD_LOCK:
        fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            counter = 1
            if counter_path.is_file():
                text = counter_path.read_text(encoding="utf-8").strip()
                if not re.fullmatch(r"[0-9]+", text) or int(text) < 1:
                    raise ApprovalError(f"Invalid approval counter in {counter_path}: {text!r}")
                counter = int(text)
            n = max(counter, _max_on_main(work.path) + 1)
            for d in extra_dirs:
                n = max(n, _max_in_dir(Path(d)) + 1)

            tmp = base / f".tmp_approvals.next_{os.getpid()}"
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(f"{n + 1}\n")
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, counter_path)
        finally:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)
    return f"A-{n:04d}"


def build_approval(
    approval_id: str,
    *,
    job_id: str,
    patches: Sequence[Mapping[str, Any]],
    approved_at: str | None = None,
) -> dict[str, Any]:
    """Build and validate the approval record of a proposal (source: proposal)."""
    if not patches:
        raise ValueError("patches must not be empty: a proposal without patches has no approval record")
    approval_rel(approval_id)
    doc: dict[str, Any] = {
        "approval_id": approval_id,
        "source": "proposal",
        "instruction_id": None,
        "job_id": job_id,
        "patch_sha256": patch_set_sha256(patches),
        "targets": sorted(p["target"] for p in patches),
        "approved_by": "human",
        "approved_at": approved_at if approved_at is not None else now_iso(),
    }
    validate_or_raise(doc, "approval")
    sem_errors = check_approval(doc)
    if sem_errors:
        raise SchemaError(f"Semantic validation failed for approval: {sem_errors}", errors=sem_errors)
    return doc


def check_approval_applicable(approval: Mapping[str, Any], patches: Sequence[Mapping[str, Any]]) -> None:
    """Raise ApprovalError unless the patch set hash matches the record and every target is in targets."""
    try:
        actual = patch_set_sha256(patches)
    except ValueError as exc:
        raise ApprovalError(f"Invalid patches: {exc}") from exc
    if actual != approval.get("patch_sha256"):
        raise ApprovalError(
            f"patch_sha256 mismatch: approval has {approval.get('patch_sha256')!r}, patches hash to {actual!r}"
        )
    targets = set(approval.get("targets", []))
    outside = sorted(p["target"] for p in patches if p["target"] not in targets)
    if outside:
        raise ApprovalError(f"patch targets not covered by the approval: {outside}")


def _exists_at(repo: Path, ref: str, rel: str) -> bool:
    try:
        run_git(repo, "cat-file", "-e", f"{ref}:{rel}")
    except GitError:
        return False
    return True


def is_approval_used(repo: Path, approval_id: str, *, branch: str | None = None) -> bool:
    """True if the approval record already exists on main (or on branch, when given)."""
    rel = approval_rel(approval_id)
    if _exists_at(repo, "main", rel):
        return True
    return branch is not None and _exists_at(repo, branch, rel)
