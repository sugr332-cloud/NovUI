"""State update proposals: Controller data kept outside the work repository (phase2c-design §2.5).

An unapproved proposal is never written to the work repository (§15.3). Proposal validation is separate from
the Claude output validation (semantics.SEMANTIC_CHECKS): check_proposal is applied here on save and load.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, Mapping, Sequence

from novui.config import Settings
from novui.jobrecord import now_iso
from novui.schema import SchemaError, validate_or_raise
from novui.semantics import check_state_patch
from novui.statepatch import patch_set_sha256, patch_sha256
from novui.yamlio import load_yaml, write_yaml_atomic


class ProposalError(Exception):
    """Raised when a proposal is inconsistent or a status change is not allowed."""


PROPOSAL_STATUSES: tuple[str, ...] = ("pending", "approved", "rejected", "superseded", "invalid")


def proposals_dir(settings: Settings, work_key: str) -> Path:
    """Return <data_dir>/works/<work_key>/proposals."""
    return settings.data_dir / "works" / work_key / "proposals"


def _proposal_path(settings: Settings, work_key: str, chapter_id: str, job_id: str) -> Path:
    return proposals_dir(settings, work_key) / chapter_id / f"{job_id}.yaml"


def _job_number(job_id: str) -> int:
    return int(job_id.rsplit("-", 1)[-1])


def check_proposal(doc: Any) -> list[str]:
    """Semantic checks of a proposal document (after schema validation)."""
    errors: list[str] = []
    patches = doc.get("patches", [])

    targets = [p["patch"]["target"] for p in patches]
    if targets != sorted(targets):
        errors.append("patches must be sorted by target in ascending order")
    if len(set(targets)) != len(targets):
        errors.append("duplicate target in patches")

    for i, p in enumerate(patches):
        sem = check_state_patch(p["patch"])
        errors.extend(f"patches[{i}].patch: {e}" for e in sem)
        if p["patch_sha256"] != patch_sha256(p["patch"]):
            errors.append(f"patches[{i}].patch_sha256 does not match the patch")

    expected_set = None
    if patches and len(set(targets)) == len(targets):
        expected_set = patch_set_sha256([p["patch"] for p in patches])
    if doc.get("patch_set_sha256") != expected_set:
        errors.append("patch_set_sha256 does not match the patches (null when there are no patches)")

    if doc.get("branch") is not None and doc.get("chapter_id") is not None:
        if not str(doc["branch"]).startswith(f"ai/{doc['chapter_id']}/"):
            errors.append("branch must be ai/<chapter_id>/<job_id>")

    status = doc.get("status")
    if status == "pending":
        if doc.get("decided_at") is not None:
            errors.append("decided_at must be null when status is pending")
    elif doc.get("decided_at") is None:
        errors.append(f"decided_at is required when status is {status}")

    if status == "approved" and patches and doc.get("approval_id") is None:
        errors.append("approval_id is required when an approved proposal has patches")
    if status != "approved" and doc.get("approval_id") is not None:
        errors.append("approval_id must be null unless status is approved")
    return errors


def _validate(doc: Mapping[str, Any]) -> None:
    validate_or_raise(dict(doc), "proposal")
    errors = check_proposal(doc)
    if errors:
        raise SchemaError(f"Semantic validation failed for proposal: {errors}", errors=errors)


def new_proposal(
    *,
    job_id: str,
    chapter_id: str,
    branch: str,
    branch_head: str,
    summary: Mapping[str, Any],
    patches: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Build a pending proposal. Patches are sorted by target and each gets its patch_sha256."""
    ordered = sorted((copy.deepcopy(dict(p)) for p in patches), key=lambda p: p["target"])
    doc: dict[str, Any] = {
        "type": "state_proposal",
        "job_id": job_id,
        "chapter_id": chapter_id,
        "branch": branch,
        "branch_head": branch_head,
        "status": "pending",
        "status_reason": None,
        "created_at": now_iso(),
        "decided_at": None,
        "approval_id": None,
        "summary": copy.deepcopy(dict(summary)),
        "patches": [{"patch": p, "patch_sha256": patch_sha256(p)} for p in ordered],
        "patch_set_sha256": patch_set_sha256(ordered) if ordered else None,
    }
    return doc


def save_proposal(settings: Settings, work_key: str, proposal: Mapping[str, Any]) -> Path:
    """Validate and atomically save a proposal. Raises SchemaError without writing if it is invalid."""
    _validate(proposal)
    path = _proposal_path(settings, work_key, proposal["chapter_id"], proposal["job_id"])
    write_yaml_atomic(path, dict(proposal))
    return path


def load_proposal(path: Path) -> dict[str, Any]:
    """Read and validate a proposal file."""
    data = load_yaml(path)
    if not isinstance(data, dict):
        raise SchemaError("Proposal must be a YAML mapping", errors=["root is not a mapping"])
    _validate(data)
    return data


def find_proposals(settings: Settings, work_key: str, chapter_id: str) -> list[dict[str, Any]]:
    """All proposals of the chapter, ordered by created_at then by job number."""
    chapter_dir = proposals_dir(settings, work_key) / chapter_id
    if not chapter_dir.is_dir():
        return []
    docs = [load_proposal(p) for p in sorted(chapter_dir.glob("job-*.yaml"))]
    docs.sort(key=lambda d: (d["created_at"], _job_number(d["job_id"])))
    return docs


def pending_proposal(settings: Settings, work_key: str, chapter_id: str) -> dict[str, Any] | None:
    """The pending proposal of the chapter, or None. More than one is a ProposalError."""
    pending = [d for d in find_proposals(settings, work_key, chapter_id) if d["status"] == "pending"]
    if len(pending) > 1:
        raise ProposalError(
            f"Chapter {chapter_id!r} has {len(pending)} pending proposals: {[d['job_id'] for d in pending]}"
        )
    return pending[0] if pending else None


def with_status(
    proposal: Mapping[str, Any],
    status: str,
    *,
    reason: str | None = None,
    approval_id: str | None = None,
    decided_at: str | None = None,
) -> dict[str, Any]:
    """Return a copy of proposal in a new status. Only pending proposals can change, and only to another status."""
    if status not in PROPOSAL_STATUSES:
        raise ProposalError(f"Unknown proposal status: {status!r}")
    current = proposal["status"]
    if current != "pending":
        raise ProposalError(f"Proposal {proposal['job_id']} is already {current}; a finished proposal cannot change")
    if status == "pending":
        raise ProposalError("A proposal cannot change from pending to pending")
    new = copy.deepcopy(dict(proposal))
    new["status"] = status
    new["status_reason"] = reason
    new["decided_at"] = decided_at if decided_at is not None else now_iso()
    new["approval_id"] = approval_id
    return new
