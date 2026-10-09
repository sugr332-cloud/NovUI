"""Semantic validations that go beyond JSON Schema."""

from pathlib import Path
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

    errors += _check_plan_flow(doc.get("scenes", []))
    return errors


def plan_scene_successors(scenes: list[Any]) -> dict[str, list[str]]:
    """Next scenes of each scene (script-format §3): the choice targets, else next, else the following scene.

    The last scene without choice/next has no successor (end of the chapter).
    """
    ids = [s.get("id") for s in scenes]
    result: dict[str, list[str]] = {}
    for i, s in enumerate(scenes):
        if s.get("choice"):
            result[s["id"]] = [c.get("goto") for c in s["choice"]]
        elif s.get("next"):
            result[s["id"]] = [s["next"]]
        elif i + 1 < len(ids):
            result[s["id"]] = [ids[i + 1]]
        else:
            result[s["id"]] = []
    return result


def _check_plan_flow(scenes: list[Any]) -> list[str]:
    """choice/next of the scenes: targets exist and are later scenes, every scene is reachable (phase2e-design §5.1)."""
    errors: list[str] = []
    if not scenes or not all(isinstance(s, dict) and "id" in s for s in scenes):
        return errors
    index = {s["id"]: i for i, s in enumerate(scenes)}
    for i, s in enumerate(scenes):
        sid = s["id"]
        choice = s.get("choice")
        if choice and s.get("next"):
            errors.append(f"Scene {sid} has both choice and next")
        if s.get("next") and i == len(scenes) - 1:
            errors.append(f"Scene {sid} is the last scene and must not have next")
        targets = [c.get("goto") for c in choice] if choice else ([s["next"]] if s.get("next") else [])
        if choice and len(set(targets)) != len(targets):
            errors.append(f"Scene {sid} has duplicate choice targets")
        for t in targets:
            if t not in index:
                errors.append(f"Scene {sid} goes to {t} which is not in the plan")
            elif index[t] <= i:
                errors.append(f"Scene {sid} goes to {t} which is not a later scene")
        extras = s.get("extras", [])
        if len(set(extras)) != len(extras):
            errors.append(f"Scene {sid} has duplicate extras")
    if errors:
        return errors
    succ = plan_scene_successors(scenes)
    reached = {scenes[0]["id"]}
    stack = [scenes[0]["id"]]
    while stack:
        for t in succ[stack.pop()]:
            if t not in reached:
                reached.add(t)
                stack.append(t)
    for s in scenes:
        if s["id"] not in reached:
            errors.append(f"Scene {s['id']} is not reachable from the first scene")
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
        if name != "character":
            for i, f in enumerate(c_data.get("findings", [])):
                if f.get("character") is not None:
                    errors.append(f"Check {name!r} finding {i} has character but only 'character' findings may")

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


def check_character(doc: Any) -> list[str]:
    """Validate character document semantics."""
    errors: list[str] = []
    my_id = doc.get("id")

    address = doc.get("address", {})
    if isinstance(address, dict):
        for k in address.keys():
            if k != "default" and k == my_id:
                errors.append(f"address key {k!r} cannot be the character's own id")

    relationships = doc.get("relationships", [])
    if isinstance(relationships, list):
        for r in relationships:
            if isinstance(r, dict) and r.get("with") == my_id:
                errors.append(f"relationships with {my_id!r} cannot be the character's own id")

    expressions = doc.get("expressions", [])
    if isinstance(expressions, list) and len(set(expressions)) != len(expressions):
        errors.append("Duplicate expressions")

    knowledge = doc.get("knowledge", [])
    if isinstance(knowledge, list):
        seen_k_ids: set[str] = set()
        for k in knowledge:
            if isinstance(k, dict):
                kid = k.get("id")
                if kid in seen_k_ids:
                    errors.append(f"Duplicate knowledge id: {kid!r}")
                seen_k_ids.add(kid)

    return errors


def check_registry(doc: Any) -> list[str]:
    """Validate foreshadowing registry document semantics."""
    errors: list[str] = []
    if not isinstance(doc, list):
        return ["registry document must be an array"]

    seen_ids: set[str] = set()
    for item in doc:
        if isinstance(item, dict):
            fid = item.get("id")
            if fid in seen_ids:
                errors.append(f"Duplicate foreshadowing id: {fid!r}")
            seen_ids.add(fid)

            status = item.get("status")
            resolved = item.get("resolved")
            if status == "resolved":
                if resolved is None:
                    errors.append(f"Foreshadowing {fid!r} has status 'resolved' but resolved position is null")
            else:
                if resolved is not None:
                    errors.append(f"Foreshadowing {fid!r} has status {status!r} but resolved position is not null")

    return errors


def check_flags(doc: Any) -> list[str]:
    """Validate flags/registry.yaml semantics."""
    errors: list[str] = []
    if not isinstance(doc, list):
        return ["flags document must be an array"]
    seen: set[str] = set()
    for item in doc:
        if isinstance(item, dict):
            name = item.get("name")
            if name in seen:
                errors.append(f"Duplicate flag name: {name!r}")
            seen.add(name)
    return errors


def check_model_catalog(doc: Any) -> list[str]:
    """Validate model catalog semantics."""
    errors: list[str] = []
    if not isinstance(doc, dict):
        return errors
    models = doc.get("models", [])
    if isinstance(models, list):
        seen_ids: set[str] = set()
        for m in models:
            if isinstance(m, dict):
                mid = m.get("id")
                if mid in seen_ids:
                    errors.append(f"Duplicate model id: {mid!r}")
                if mid is not None:
                    seen_ids.add(mid)
    return errors


def check_works_registry(doc: Any) -> list[str]:
    """Validate works registry semantics."""
    errors: list[str] = []
    if not isinstance(doc, dict):
        return errors
    works = doc.get("works", [])
    if isinstance(works, list):
        seen_keys: set[str] = set()
        for w in works:
            if isinstance(w, dict):
                k = w.get("work_key")
                if k in seen_keys:
                    errors.append(f"Duplicate work_key: {k!r}")
                if k is not None:
                    seen_keys.add(k)
                p = w.get("path")
                if p is not None:
                    path_obj = Path(p)
                    if not path_obj.is_absolute():
                        errors.append(f"path must be an absolute path, got: {p!r}")
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
    "character": check_character,
    "registry": check_registry,
    "review": _no_op_check,
    "summary": _no_op_check,
    "model_catalog": check_model_catalog,
    "controller_settings": _no_op_check,
    "project": _no_op_check,
    "chapters_order": _no_op_check,
    "prohibited": _no_op_check,
    "works_registry": check_works_registry,
    "flags": check_flags,
    "backgrounds": _no_op_check,
    "script_rules": _no_op_check,
})


