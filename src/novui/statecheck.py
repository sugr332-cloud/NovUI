"""Checks for state_update: summary references, patch policy, dry-run and consistency (phase2c-design §2.4, §3.4).

All functions are pure. Each returns a checks.CheckResult.
"""

from __future__ import annotations

import re
from typing import Any, Collection, Iterable, Mapping, Sequence

from novui.checks import CheckResult
from novui.schema import SchemaError, validate
from novui.semantics import SEMANTIC_CHECKS
from novui.statepatch import PatchError, apply_patch, parse_pointer

REGISTRY_TARGET = "foreshadowing/registry.yaml"

_CHAR_TARGET_RE = re.compile(r"^characters/(C[0-9]{3,})\.yaml$")
_CHAR_ID_RE = re.compile(r"^C[0-9]{3,}$")
_K_ID_RE = re.compile(r"^K[0-9]{3,}$")
_K_NUM_RE = re.compile(r"^K([0-9]+)$")
_INDEX_RE = re.compile(r"^(0|[1-9][0-9]*)$")
_ALLOWED_OPS = ("add", "replace", "test")
_POSITION_LISTS = ("introduced", "hints", "developments")
_REGISTRY_STATUSES = ("planned", "active", "resolved")


def _result(name: str, status_if_problem: str, details: Sequence[str]) -> CheckResult:
    if details:
        return CheckResult(name=name, status=status_if_problem, details=tuple(details))
    return CheckResult(name=name, status="PASS", details=())


def derive_patch_targets(summary: Mapping[str, Any]) -> list[str]:
    """Patch targets derived from a summary: characters/<id>.yaml (sorted), then the foreshadowing registry."""
    ids = sorted({
        c["id"]
        for c in summary.get("characters", [])
        if c.get("knowledge_added") or c.get("relationship_changes")
    })
    targets = [f"characters/{cid}.yaml" for cid in ids]
    if summary.get("foreshadowing"):
        targets.append(REGISTRY_TARGET)
    return targets


def _brace_paths(obj: Any, path: str = "") -> list[str]:
    found: list[str] = []
    if isinstance(obj, str):
        if "{{" in obj or "}}" in obj:
            found.append(path or "/")
    elif isinstance(obj, Mapping):
        for k, v in obj.items():
            found += _brace_paths(v, f"{path}/{k}")
    elif isinstance(obj, (list, tuple)):
        for i, v in enumerate(obj):
            found += _brace_paths(v, f"{path}/{i}")
    return found


def check_summary_refs(
    summary: Mapping[str, Any],
    *,
    chapter_id: str,
    character_ids: Collection[str],
    foreshadow_ids: Collection[str],
) -> CheckResult:
    """chapter_id matches; character and foreshadowing IDs are unique and known; no '{{' or '}}' in any string."""
    details: list[str] = []
    if summary.get("chapter_id") != chapter_id:
        details.append(f"chapter_id {summary.get('chapter_id')!r} does not match {chapter_id!r}")

    seen: set[str] = set()
    for c in summary.get("characters", []):
        cid = c.get("id")
        if cid in seen:
            details.append(f"duplicate character id: {cid}")
        seen.add(cid)
        if cid not in character_ids:
            details.append(f"character {cid} has no characters/{cid}.yaml in the context")

    seen = set()
    for f in summary.get("foreshadowing", []):
        fid = f.get("id")
        if fid in seen:
            details.append(f"duplicate foreshadowing id: {fid}")
        seen.add(fid)
        if fid not in foreshadow_ids:
            details.append(f"foreshadowing {fid} is not in foreshadowing/registry.yaml")

    for p in _brace_paths(summary):
        details.append(f"string contains '{{{{' or '}}}}' at {p}")
    return _result("summary_refs", "FAIL", details)


def check_patch_target(patch: Mapping[str, Any], expected_target: str) -> CheckResult:
    """The patch target is the one the Controller asked for."""
    if patch.get("target") != expected_target:
        return CheckResult(
            name="patch_target",
            status="FAIL",
            details=(f"target {patch.get('target')!r} does not match expected {expected_target!r}",),
        )
    return CheckResult(name="patch_target", status="PASS", details=())


def _is_nonempty_str(v: Any) -> bool:
    return isinstance(v, str) and v != ""


def _position_errors(v: Any, chapter_id: str, scene_ids: Sequence[str], label: str) -> list[str]:
    if not isinstance(v, dict) or set(v.keys()) != {"chapter", "scene"}:
        return [f"{label}: must be an object with exactly chapter and scene"]
    errs: list[str] = []
    if v["chapter"] != chapter_id:
        errs.append(f"{label}: chapter {v['chapter']!r} must be {chapter_id!r}")
    if v["scene"] not in scene_ids:
        errs.append(f"{label}: scene {v['scene']!r} is not a scene of the plan {list(scene_ids)}")
    return errs


def _change_errors(v: Any, chapter_id: str, scene_ids: Sequence[str], label: str) -> list[str]:
    if not isinstance(v, dict) or set(v.keys()) != {"value", "from", "reason"}:
        return [f"{label}: must be an object with exactly value, from and reason"]
    errs: list[str] = []
    if not _is_nonempty_str(v["value"]):
        errs.append(f"{label}: value must be a non-empty string")
    if not _is_nonempty_str(v["reason"]):
        errs.append(f"{label}: reason must be a non-empty string")
    errs += _position_errors(v["from"], chapter_id, scene_ids, f"{label}.from")
    return errs


def _policy_character(
    ops: Sequence[Mapping[str, Any]],
    chapter_id: str,
    scene_ids: Sequence[str],
    doc_before: Any,
) -> list[str]:
    errs: list[str] = []
    before = doc_before if isinstance(doc_before, dict) else {}
    address_before = before.get("address") if isinstance(before.get("address"), dict) else {}
    existing_k = {k.get("id") for k in before.get("knowledge", []) if isinstance(k, dict)}
    new_k: set[str] = set()
    tested: set[str] = set()
    converted: set[str] = set()

    for i, op in enumerate(ops):
        name = op["op"]
        path = op["path"]
        label = f"operations[{i}]"
        try:
            toks = parse_pointer(path)
        except PatchError as exc:
            errs.append(f"{label}: {exc}")
            continue

        if name == "test":
            tested.add(path)
            continue

        if name == "add":
            value = op.get("value")
            if toks == ["knowledge", "-"]:
                if not isinstance(value, dict) or set(value.keys()) != {"id", "fact", "source_chapter"}:
                    errs.append(f"{label}: knowledge value must be an object with exactly id, fact and source_chapter")
                    continue
                kid = value["id"]
                if not isinstance(kid, str) or not _K_ID_RE.match(kid):
                    errs.append(f"{label}: knowledge id {kid!r} must be K followed by 3 or more digits")
                elif kid in new_k:
                    errs.append(f"{label}: knowledge id {kid} is duplicated in the patch")
                elif kid in existing_k:
                    errs.append(f"{label}: knowledge id {kid} already exists")
                else:
                    new_k.add(kid)
                if not _is_nonempty_str(value["fact"]):
                    errs.append(f"{label}: knowledge fact must be a non-empty string")
                if value["source_chapter"] != chapter_id:
                    errs.append(f"{label}: source_chapter must be {chapter_id!r}")
            elif len(toks) == 4 and toks[0] == "relationships" and _INDEX_RE.match(toks[1]) and toks[2:] == ["changes", "-"]:
                if f"/relationships/{toks[1]}/with" not in tested:
                    errs.append(f"{label}: a test of /relationships/{toks[1]}/with must precede this operation")
                errs += _change_errors(value, chapter_id, scene_ids, f"{label}.value")
            elif toks == ["relationships", "-"]:
                if (
                    not isinstance(value, dict)
                    or set(value.keys()) != {"with", "state"}
                    or not isinstance(value["with"], str)
                    or not _CHAR_ID_RE.match(value["with"])
                    or not _is_nonempty_str(value["state"])
                ):
                    errs.append(f"{label}: relationship value must be an object with a character id 'with' and a non-empty 'state'")
            elif len(toks) == 4 and toks[0] == "address" and _CHAR_ID_RE.match(toks[1]) and toks[2:] == ["changes", "-"]:
                partner = toks[1]
                if not isinstance(address_before.get(partner), dict) and partner not in converted:
                    errs.append(f"{label}: address.{partner} must be in object form to add changes")
                errs += _change_errors(value, chapter_id, scene_ids, f"{label}.value")
            elif len(toks) == 2 and toks[0] == "address" and _CHAR_ID_RE.match(toks[1]):
                if toks[1] in address_before:
                    errs.append(f"{label}: address.{toks[1]} already exists; use a change instead")
                if not _is_nonempty_str(value):
                    errs.append(f"{label}: address value must be a non-empty string")
            else:
                errs.append(f"{label}: add to {path!r} is not allowed")
            continue

        # replace
        if len(toks) == 2 and toks[0] == "address" and _CHAR_ID_RE.match(toks[1]):
            partner = toks[1]
            current = address_before.get(partner)
            value = op.get("value")
            if not isinstance(current, str):
                errs.append(f"{label}: address.{partner} must currently be a string to be replaced")
            if path not in tested:
                errs.append(f"{label}: a test of {path} must precede this operation")
            if not isinstance(value, dict) or set(value.keys()) != {"default", "changes"}:
                errs.append(f"{label}: value must be an object with exactly default and changes")
            else:
                if isinstance(current, str) and value["default"] != current:
                    errs.append(f"{label}: default must be the current value {current!r}")
                if not isinstance(value["changes"], list) or not value["changes"]:
                    errs.append(f"{label}: changes must be a non-empty list")
                else:
                    for j, ch in enumerate(value["changes"]):
                        errs += _change_errors(ch, chapter_id, scene_ids, f"{label}.value.changes[{j}]")
            converted.add(partner)
        else:
            errs.append(f"{label}: replace of {path!r} is not allowed")
    return errs


def _policy_registry(
    ops: Sequence[Mapping[str, Any]],
    chapter_id: str,
    scene_ids: Sequence[str],
) -> list[str]:
    errs: list[str] = []
    tested_idx: set[int] = set()
    resolved_status_idx: set[int] = set()
    resolved_set_idx: set[int] = set()

    for i, op in enumerate(ops):
        name = op["op"]
        path = op["path"]
        label = f"operations[{i}]"
        try:
            toks = parse_pointer(path)
        except PatchError as exc:
            errs.append(f"{label}: {exc}")
            continue
        if not toks or not _INDEX_RE.match(toks[0]):
            errs.append(f"{label}: path {path!r} must start with an array index")
            continue
        idx = int(toks[0])

        if idx not in tested_idx:
            if name == "test" and toks == [toks[0], "id"]:
                tested_idx.add(idx)
                continue
            errs.append(f"{label}: the first operation on /{idx}/ must be test /{idx}/id")
            continue

        if name == "test":
            continue
        value = op.get("value")
        if name == "add":
            if len(toks) == 3 and toks[1] in _POSITION_LISTS and toks[2] == "-":
                errs += _position_errors(value, chapter_id, scene_ids, f"{label}.value")
            else:
                errs.append(f"{label}: add to {path!r} is not allowed")
        else:  # replace
            if toks[1:] == ["status"]:
                if value not in _REGISTRY_STATUSES:
                    errs.append(f"{label}: status must be one of {list(_REGISTRY_STATUSES)} (cancelled is not allowed)")
                elif value == "resolved":
                    resolved_status_idx.add(idx)
            elif toks[1:] == ["resolved"]:
                errs += _position_errors(value, chapter_id, scene_ids, f"{label}.value")
                resolved_set_idx.add(idx)
            else:
                errs.append(f"{label}: replace of {path!r} is not allowed")

    for idx in sorted(resolved_status_idx - resolved_set_idx):
        errs.append(f"/{idx}/status is set to resolved without a replace of /{idx}/resolved")
    return errs


def check_patch_policy(
    patch: Mapping[str, Any],
    *,
    chapter_id: str,
    scene_ids: Sequence[str],
    doc_before: Any,
) -> CheckResult:
    """Operations of a state_update patch are within the allowed forms (phase2c-design §3.4)."""
    target = patch.get("target")
    ops = patch.get("operations", [])
    errs: list[str] = []

    is_char = isinstance(target, str) and _CHAR_TARGET_RE.match(target) is not None
    if not is_char and target != REGISTRY_TARGET:
        return CheckResult(name="patch_policy", status="FAIL", details=(f"target {target!r} is not allowed",))

    for i, op in enumerate(ops):
        if op.get("op") not in _ALLOWED_OPS:
            errs.append(f"operations[{i}]: op {op.get('op')!r} is not allowed (add, replace and test only)")
    if errs:
        return CheckResult(name="patch_policy", status="FAIL", details=tuple(errs))

    if is_char:
        errs = _policy_character(ops, chapter_id, scene_ids, doc_before)
    else:
        errs = _policy_registry(ops, chapter_id, scene_ids)
    return _result("patch_policy", "FAIL", errs)


def dry_run_patch(doc_before: Any, patch: Mapping[str, Any]) -> CheckResult:
    """Apply the patch to doc_before and validate the result with the target's schema and semantics."""
    target = patch.get("target")
    if isinstance(target, str) and _CHAR_TARGET_RE.match(target):
        schema_name = "character"
    elif target == REGISTRY_TARGET:
        schema_name = "registry"
    else:
        return CheckResult(name="patch_apply", status="FAIL", details=(f"unsupported target {target!r}",))

    try:
        after = apply_patch(doc_before, patch)
    except (PatchError, SchemaError) as exc:
        detail = f"{exc}" + (f" {exc.errors}" if isinstance(exc, SchemaError) else "")
        return CheckResult(name="patch_apply", status="FAIL", details=(detail,))

    errs = [f"schema: {e}" for e in validate(after, schema_name)]
    errs += [f"semantics: {e}" for e in SEMANTIC_CHECKS[schema_name](after)]
    return _result("patch_apply", "FAIL", errs)


def check_summary_patch_consistency(
    summary: Mapping[str, Any],
    patches: Sequence[Mapping[str, Any]],
) -> CheckResult:
    """WARNING if a summary knowledge_added sentence has no matching knowledge fact in the character's patch."""
    facts: dict[str, set[str]] = {}
    for p in patches:
        m = _CHAR_TARGET_RE.match(p.get("target", ""))
        if not m:
            continue
        bucket = facts.setdefault(m.group(1), set())
        for op in p.get("operations", []):
            if op.get("op") == "add" and op.get("path") == "/knowledge/-" and isinstance(op.get("value"), dict):
                bucket.add(op["value"].get("fact"))

    missing: list[str] = []
    for c in summary.get("characters", []):
        for sentence in c.get("knowledge_added", []):
            if sentence not in facts.get(c["id"], set()):
                missing.append(f"{c['id']}: {sentence}")
    return _result("summary_patch_consistency", "WARNING", missing)


def next_knowledge_number(character_docs: Iterable[Mapping[str, Any]]) -> int:
    """Highest knowledge K number across the given character documents + 1 (1 if none)."""
    best = 0
    for doc in character_docs:
        for k in doc.get("knowledge", []) if isinstance(doc, Mapping) else []:
            kid = k.get("id") if isinstance(k, Mapping) else None
            m = _K_NUM_RE.match(kid) if isinstance(kid, str) else None
            if m:
                best = max(best, int(m.group(1)))
    return best + 1


def pointer_hints(target: str, doc_before: Any) -> str:
    """Lines for the {{pointer_hints}} placeholder: where each foreshadowing, relationship and address lives."""
    lines: list[str] = []
    if target == REGISTRY_TARGET:
        if isinstance(doc_before, list):
            for i, item in enumerate(doc_before):
                if isinstance(item, Mapping) and "id" in item:
                    lines.append(f"{item['id']} → /{i}")
    elif isinstance(doc_before, Mapping):
        rels = doc_before.get("relationships")
        if isinstance(rels, list):
            for i, r in enumerate(rels):
                if isinstance(r, Mapping) and "with" in r:
                    lines.append(f"関係：{r['with']} → /relationships/{i}")
        addr = doc_before.get("address")
        if isinstance(addr, Mapping):
            for partner, val in addr.items():
                if partner == "default":
                    continue
                form = "文字列" if isinstance(val, str) else "object"
                lines.append(f"呼び方：{partner} → /address/{partner}（{form}）")
    return "\n".join(lines) if lines else "なし"
