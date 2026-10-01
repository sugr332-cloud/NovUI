"""Semantic validations that go beyond JSON Schema."""

from typing import Any, Callable
from novui.paths import is_safe_relpath


def check_chapter(doc: Any) -> list[str]:
    """Validate chapter document semantics."""
    errors: list[str] = []
    review_required = doc.get("review_required")
    reasons = doc.get("review_reasons", [])
    if not review_required and reasons:
        errors.append("review_reasons must be empty when review_required is false")
    if review_required and not reasons:
        errors.append("review_reasons must contain at least one entry when review_required is true")
    return errors


def check_plan(doc: Any) -> list[str]:
    """Validate plan document semantics."""
    errors: list[str] = []
    tc = doc.get("target_chars", {})
    min_c = tc.get("min", 0)
    max_c = tc.get("max", 0)
    if min_c > max_c:
        errors.append(f"target_chars.min ({min_c}) must be <= target_chars.max ({max_c})")

    seen_scenes: set[str] = set()
    for s in doc.get("scenes", []):
        sid = s.get("id")
        if sid in seen_scenes:
            errors.append(f"Duplicate scene id: {sid}")
        seen_scenes.add(sid)

    for p in doc.get("context", {}).get("settings", []):
        if not is_safe_relpath(p):
            errors.append(f"context.settings path is not a safe relative path: {p!r}")

    return errors


def check_integrity_review(doc: Any) -> list[str]:
    """Validate integrity review semantics."""
    errors: list[str] = []
    severity_order = {"PASS": 0, "WARNING": 1, "STOP": 2}
    rank_to_res = {0: "PASS", 1: "WARNING", 2: "STOP"}

    checks = doc.get("checks", {})
    max_rank = 0
    for name, c_data in checks.items():
        res = c_data.get("result")
        rank = severity_order.get(res, 0)
        if rank > max_rank:
            max_rank = rank
        if res == "PASS":
            for f in c_data.get("findings", []):
                if f.get("severity") == "STOP":
                    errors.append(f"Check {name!r} has result PASS but contains a STOP finding")

    expected_overall = rank_to_res[max_rank]
    actual_overall = doc.get("result")
    if actual_overall != expected_overall:
        errors.append(
            f"Integrity review result {actual_overall!r} does not match heaviest check result {expected_overall!r}"
        )
    return errors


def check_writing_review(doc: Any) -> list[str]:
    """Validate writing review semantics."""
    errors: list[str] = []
    seen_ids: set[str] = set()
    for f in doc.get("findings", []):
        fid = f.get("id")
        if fid in seen_ids:
            errors.append(f"Duplicate finding id: {fid}")
        seen_ids.add(fid)
    return errors


def check_state_patch(doc: Any) -> list[str]:
    """Validate state patch semantics."""
    errors: list[str] = []
    target = doc.get("target", "")
    if not is_safe_relpath(target):
        errors.append(f"target is not a safe relative path: {target!r}")
    return errors


def check_instruction_routing(doc: Any) -> list[str]:
    """Validate instruction routing semantics."""
    errors: list[str] = []
    questions = doc.get("questions", [])
    proposed_jobs = doc.get("proposed_jobs", [])
    if questions and proposed_jobs:
        errors.append("proposed_jobs must be empty when questions has 1 or more entries")

    mismatch = doc.get("scope_mismatch")
    reason = doc.get("scope_mismatch_reason")
    if mismatch is True:
        if not reason or not isinstance(reason, str) or not reason.strip():
            errors.append("scope_mismatch_reason must be a non-empty string when scope_mismatch is true")
    elif mismatch is False:
        if reason is not None:
            errors.append("scope_mismatch_reason must be null when scope_mismatch is false")

    for i, job in enumerate(proposed_jobs):
        dep = job.get("depends_on")
        if dep is not None:
            if not isinstance(dep, int) or dep < 0 or dep >= i:
                errors.append(f"proposed_jobs[{i}].depends_on ({dep}) must be null or an integer < {i}")
    return errors


def check_approval(doc: Any) -> list[str]:
    """Validate approval document semantics."""
    errors: list[str] = []
    source = doc.get("source")
    if source == "instruction" and doc.get("instruction_id") is None:
        errors.append("instruction_id must not be null when source is 'instruction'")
    if source == "proposal" and doc.get("job_id") is None:
        errors.append("job_id must not be null when source is 'proposal'")

    targets = doc.get("targets", [])
    seen: set[str] = set()
    for t in targets:
        if not is_safe_relpath(t):
            errors.append(f"targets path is not a safe relative path: {t!r}")
        if t in seen:
            errors.append(f"Duplicate target path in targets: {t!r}")
        seen.add(t)
    return errors


def check_requests(doc: Any) -> list[str]:
    """Validate requests document semantics."""
    errors: list[str] = []
    if not isinstance(doc, list):
        return ["requests document must be a list"]
    for idx, item in enumerate(doc):
        if isinstance(item, dict) and item.get("type") == "resolution":
            req_idx = item.get("request_index")
            if (
                req_idx is None
                or not isinstance(req_idx, int)
                or req_idx < 0
                or req_idx >= idx
                or not isinstance(doc[req_idx], dict)
                or doc[req_idx].get("type") != "request"
            ):
                errors.append(
                    f"resolution at index {idx} has invalid request_index {req_idx} (must point to preceding request)"
                )
    return errors


def check_job_record(doc: Any) -> list[str]:
    """Validate job record semantics."""
    errors: list[str] = []
    history = doc.get("history", [])
    state = doc.get("state")
    if not history:
        errors.append("history must contain at least one entry")
    elif history[-1].get("state") != state:
        errors.append(
            f"history last state ({history[-1].get('state')!r}) must match record state ({state!r})"
        )

    for ctx in doc.get("context", []):
        p = ctx.get("path")
        if not is_safe_relpath(p):
            errors.append(f"context path is not a safe relative path: {p!r}")
    return errors


def _no_op_check(_doc: Any) -> list[str]:
    return []


class _SemanticChecksDict(dict):
    """Dictionary supporting lookups with or without '.schema.json' extension."""

    def __getitem__(self, key: str) -> Callable[[Any], list[str]]:
        norm = key.removesuffix(".schema.json").removesuffix(".json")
        return super().__getitem__(norm)

    def get(self, key: str, default: Any = None) -> Any:
        norm = key.removesuffix(".schema.json").removesuffix(".json")
        return super().get(norm, default)

    def __contains__(self, key: object) -> bool:
        if isinstance(key, str):
            norm = key.removesuffix(".schema.json").removesuffix(".json")
            return super().__contains__(norm)
        return super().__contains__(key)


SEMANTIC_CHECKS: dict[str, Callable[[Any], list[str]]] = _SemanticChecksDict({
    "chapter": check_chapter,
    "plan": check_plan,
    "integrity_review": check_integrity_review,
    "writing_review": check_writing_review,
    "state_patch": check_state_patch,
    "instruction_routing": check_instruction_routing,
    "approval": check_approval,
    "requests": check_requests,
    "job_record": check_job_record,
    "review": _no_op_check,
    "summary": _no_op_check,
})
