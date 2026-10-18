"""Foreshadowing checks (spec §11.8, phase2d-design §5, §9).

Mechanical checks on foreshadowing/registry.yaml against chapters-order.yaml and the plan, and the
foreshadowing status table inserted in the integrity_review prompt. Results are PASS or WARNING only
(§11.3: mechanical results are never STOP). All functions except load_registry are pure.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from novui.checks import CheckResult
from novui.positions import PositionError, chapter_index, format_position, position_key
from novui.schema import SchemaError, validate_or_raise
from novui.semantics import check_registry
from novui.yamlio import YamlError, dumps_yaml, load_yaml

REGISTRY_REL = "foreshadowing/registry.yaml"
_POSITION_LISTS = ("introduced", "hints", "developments")
_LATER_LISTS = ("hints", "developments")
_SINGLE_POSITIONS = ("planned_resolution", "resolved")


def _unique(items: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(items))


def _result(name: str, details: Sequence[str]) -> CheckResult:
    if details:
        return CheckResult(name=name, status="WARNING", details=tuple(_unique(details)))
    return CheckResult(name=name, status="PASS", details=())


def _key(order: Sequence[str], pos: Any) -> tuple[int, int] | None:
    try:
        return position_key(order, pos)
    except PositionError:
        return None


def load_registry(root: Path) -> list[dict[str, Any]] | None:
    """registry.yaml validated by schema and semantics; None if missing or invalid
    (load errors are reported by the existing mechanical_inputs check)."""
    path = root / REGISTRY_REL
    if not path.is_file():
        return None
    try:
        doc = load_yaml(path)
        validate_or_raise(doc, "registry")
    except (YamlError, SchemaError, OSError):
        return None
    if check_registry(doc):
        return None
    return doc


def check_foreshadow_positions(order: Sequence[str], registry: Sequence[Mapping[str, Any]]) -> CheckResult:
    """'foreshadow_positions': every position in the registry can be compared."""
    details: list[str] = []
    for item in registry:
        fid = item["id"]
        for field in _POSITION_LISTS:
            for i, pos in enumerate(item.get(field, [])):
                try:
                    position_key(order, pos)
                except PositionError as exc:
                    details.append(f"{fid} {field}[{i}] {format_position(pos)}: {exc}")
        for field in _SINGLE_POSITIONS:
            pos = item.get(field)
            if pos is None:
                continue
            try:
                position_key(order, pos)
            except PositionError as exc:
                details.append(f"{fid} {field} {format_position(pos)}: {exc}")
    return _result("foreshadow_positions", details)


def foreshadow_order_issues(order: Sequence[str], registry: Sequence[Mapping[str, Any]]) -> list[str]:
    """Order problems of each foreshadowing (positions that cannot be compared are ignored).

    Earliest introduced is the reference. hints/developments must not be before it, resolved must not be
    before it, and hints/developments must not be after resolved. Equal positions are not problems.
    """
    issues: list[str] = []
    for item in registry:
        fid = item["id"]
        introduced = [(k, p) for p in item.get("introduced", []) if (k := _key(order, p)) is not None]
        first = min(introduced, key=lambda kp: kp[0]) if introduced else None
        resolved = item.get("resolved")
        resolved_key = _key(order, resolved) if resolved is not None else None

        if first is None:
            if item.get("hints") or item.get("developments") or resolved is not None:
                issues.append(f"{fid} has hints/developments/resolved but no introduced")
            continue

        first_key, first_pos = first
        for field in _LATER_LISTS:
            for i, pos in enumerate(item.get(field, [])):
                k = _key(order, pos)
                if k is not None and k < first_key:
                    issues.append(
                        f"{fid} {field}[{i}] {format_position(pos)} is before introduced {format_position(first_pos)}"
                    )
        if resolved_key is not None and resolved_key < first_key:
            issues.append(
                f"{fid} resolved {format_position(resolved)} is before introduced {format_position(first_pos)}"
            )
        if resolved_key is not None:
            for field in _LATER_LISTS:
                for i, pos in enumerate(item.get(field, [])):
                    k = _key(order, pos)
                    if k is not None and k > resolved_key:
                        issues.append(
                            f"{fid} {field}[{i}] {format_position(pos)} is after resolved {format_position(resolved)}"
                        )
    return issues


def check_foreshadow_order(order: Sequence[str], registry: Sequence[Mapping[str, Any]]) -> CheckResult:
    """'foreshadow_order': WARNING when foreshadow_order_issues finds problems."""
    return _result("foreshadow_order", foreshadow_order_issues(order, registry))


def check_foreshadow_overdue(
    order: Sequence[str], registry: Sequence[Mapping[str, Any]], chapter_id: str
) -> CheckResult:
    """'foreshadow_overdue': major, planned/active foreshadowing whose planned_resolution chapter is
    before the chapter being validated (a resolution planned in this chapter has not passed yet)."""
    details: list[str] = []
    try:
        current = chapter_index(order, chapter_id)
    except PositionError:
        return _result("foreshadow_overdue", details)
    for item in registry:
        planned = item.get("planned_resolution")
        if item.get("importance") != "major" or item.get("status") not in ("planned", "active") or planned is None:
            continue
        k = _key(order, planned)
        if k is not None and k[0] < current:
            details.append(f"{item['id']} planned_resolution {format_position(planned)} has passed but not resolved")
    return _result("foreshadow_overdue", details)


def check_foreshadow_plan_refs(
    registry: Sequence[Mapping[str, Any]], plan: Mapping[str, Any], chapter_id: str
) -> CheckResult:
    """'foreshadow_plan_refs': plan scenes referring to cancelled foreshadowing, or to foreshadowing
    resolved in another chapter (resolved in this chapter is a rewrite and is allowed)."""
    by_id = {item["id"]: item for item in registry}
    details: list[str] = []
    for scene in plan.get("scenes", []):
        for fid in scene.get("foreshadowing", []):
            item = by_id.get(fid)
            if item is None:
                continue
            status = item.get("status")
            resolved = item.get("resolved")
            if status == "cancelled" or (
                status == "resolved" and resolved is not None and resolved.get("chapter") != chapter_id
            ):
                details.append(f"{scene['id']} refers to {fid} which is {status}")
    return _result("foreshadow_plan_refs", details)


def run_foreshadow_checks(
    order: Sequence[str],
    registry: Sequence[Mapping[str, Any]] | None,
    plan: Mapping[str, Any],
    chapter_id: str,
) -> list[CheckResult]:
    """The four foreshadowing checks in order; [] when the registry is missing or invalid."""
    if registry is None:
        return []
    return [
        check_foreshadow_positions(order, registry),
        check_foreshadow_order(order, registry),
        check_foreshadow_overdue(order, registry, chapter_id),
        check_foreshadow_plan_refs(registry, plan, chapter_id),
    ]


def _last(order: Sequence[str], positions: Sequence[Mapping[str, Any]]) -> Mapping[str, Any] | None:
    keyed = [(k, p) for p in positions if (k := _key(order, p)) is not None]
    return max(keyed, key=lambda kp: kp[0])[1] if keyed else None


def foreshadow_status_rows(
    order: Sequence[str],
    registry: Sequence[Mapping[str, Any]] | None,
    plan: Mapping[str, Any],
    chapter_id: str,
) -> list[dict[str, Any]]:
    """Rows of the foreshadowing status table (phase2d-design §5.2): the foreshadowing the plan refers to
    (in order of first appearance), then other major ones that are planned or active (registry order)."""
    if registry is None:
        return []
    by_id = {item["id"]: item for item in registry}
    scenes_of: dict[str, list[str]] = {}
    for scene in plan.get("scenes", []):
        for fid in scene.get("foreshadowing", []):
            scenes_of.setdefault(fid, [])
            if scene["id"] not in scenes_of[fid]:
                scenes_of[fid].append(scene["id"])

    targets = [fid for fid in scenes_of if fid in by_id]
    targets += [
        item["id"]
        for item in registry
        if item["id"] not in scenes_of and item.get("importance") == "major" and item.get("status") in ("planned", "active")
    ]

    rows: list[dict[str, Any]] = []
    for fid in targets:
        item = by_id[fid]
        planned = item.get("planned_resolution")
        last_hint = _last(order, item.get("hints", []))
        last_dev = _last(order, item.get("developments", []))
        rows.append({
            "id": fid,
            "name": item["name"],
            "status": item["status"],
            "importance": item["importance"],
            "introduced": [dict(p) for p in item.get("introduced", [])],
            "last_hint": dict(last_hint) if last_hint is not None else None,
            "last_development": dict(last_dev) if last_dev is not None else None,
            "planned_resolution": dict(planned) if planned is not None else None,
            "resolved": dict(item["resolved"]) if item.get("resolved") is not None else None,
            "planned_resolution_in_this_chapter": planned is not None and planned.get("chapter") == chapter_id,
            "scenes_in_this_chapter": list(scenes_of.get(fid, [])),
        })
    return rows


def render_foreshadow_status(rows: Sequence[Mapping[str, Any]]) -> str:
    """Text inserted in the integrity_review prompt as {{foreshadow_status}}. 'なし' when there is no row."""
    if not rows:
        return "なし"
    return dumps_yaml([dict(r) for r in rows]).rstrip("\n")


def check_new_order_issues(
    order: Sequence[str],
    before: Sequence[Mapping[str, Any]],
    after: Sequence[Mapping[str, Any]],
) -> CheckResult:
    """'foreshadow_order' for state_update: only problems that the patch introduced (decision 4)."""
    old = set(foreshadow_order_issues(order, before))
    return _result("foreshadow_order", [i for i in foreshadow_order_issues(order, after) if i not in old])
