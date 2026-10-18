"""Mechanical check 'flag_conditions' on the `when` of settings (phase2f-design §5).

Settings with a position (changes of address and relationships, foreshadowing positions) and knowledge may
have a condition `when`. The check finds conditions on unregistered flags, conditions that never hold, and
settings changed in a branch scene without a condition that holds on every path into it. Results are PASS or
WARNING only. All functions except load_flag_names are pure.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Collection, Iterable, Iterator, Mapping, Sequence

from novui.checks import CheckResult
from novui.flagstate import FlagStates, branch_scenes, holds_count, load_plans, parse_condition, work_flag_states
from novui.positions import PositionError, chapter_index, format_position
from novui.schema import SchemaError, validate_or_raise
from novui.yamlio import YamlError, load_yaml

FLAGS_REL = "flags/registry.yaml"
_POSITION_LISTS = ("introduced", "hints", "developments")


@dataclass(frozen=True)
class Conditioned:
    label: str
    chapter: str
    scene: str | None  # knowledge は場面を持たない
    when: str | None


def load_flag_names(root: Path) -> frozenset[str] | None:
    """Names in flags/registry.yaml; None if missing or invalid."""
    path = root / FLAGS_REL
    if not path.is_file():
        return None
    try:
        doc = load_yaml(path)
        validate_or_raise(doc, "flags")
    except (YamlError, SchemaError, OSError):
        return None
    return frozenset(f["name"] for f in doc)


def flag_check_args(root: Path, order: Sequence[str], chapter_id: str, plan: Mapping[str, Any]) -> dict[str, Any]:
    """Keyword arguments of check_flag_conditions for the chapter, from the plans under root (plan is used
    for chapter_id instead of its file). flag_states is one of them."""
    plans, warnings = load_plans(root, order)
    plans[chapter_id] = plan
    fs = work_flag_states(order, plans)
    fs = FlagStates(fs.at_start, fs.at_scene, fs.at_end, tuple(warnings) + fs.warnings)
    return dict(order=order, flag_states=fs, plans=plans, flag_names=load_flag_names(root), upto_chapter=chapter_id)


def character_items(doc: Mapping[str, Any]) -> Iterator[Conditioned]:
    cid = doc.get("id")
    address = doc.get("address")
    if isinstance(address, Mapping):
        for target, value in address.items():
            if isinstance(value, Mapping):
                for i, ch in enumerate(value.get("changes", [])):
                    yield Conditioned(f"{cid} address.{target} changes[{i}]", ch["from"]["chapter"],
                                      ch["from"]["scene"], ch.get("when"))
    for rel in doc.get("relationships", []):
        for i, ch in enumerate(rel.get("changes", [])):
            yield Conditioned(f"{cid} relationships.{rel['with']} changes[{i}]", ch["from"]["chapter"],
                              ch["from"]["scene"], ch.get("when"))
    for k in doc.get("knowledge", []):
        yield Conditioned(f"{cid} knowledge {k['id']}", k["source_chapter"], None, k.get("when"))


def registry_items(registry: Sequence[Mapping[str, Any]]) -> Iterator[Conditioned]:
    for item in registry:
        fid = item["id"]
        for field in _POSITION_LISTS:
            for i, pos in enumerate(item.get(field, [])):
                yield Conditioned(f"{fid} {field}[{i}]", pos["chapter"], pos["scene"], pos.get("when"))
        resolved = item.get("resolved")
        if resolved is not None:
            yield Conditioned(f"{fid} resolved", resolved["chapter"], resolved["scene"], resolved.get("when"))


def condition_issues(
    items: Iterable[Conditioned],
    *,
    order: Sequence[str],
    flag_states: FlagStates,
    plans: Mapping[str, Mapping[str, Any]],
    flag_names: Collection[str] | None,
    upto_chapter: str,
) -> list[str]:
    """Problems of the items whose chapter is at or before upto_chapter (later chapters are not checked)."""
    try:
        upto = chapter_index(order, upto_chapter)
    except PositionError:
        return []
    branch_of = {ch: branch_scenes(plan) for ch, plan in plans.items()}
    issues: list[str] = []
    for it in items:
        try:
            if chapter_index(order, it.chapter) > upto:
                continue
        except PositionError:
            continue  # 位置の誤りは character_rules・foreshadow_positions が示す
        where = format_position({"chapter": it.chapter, "scene": it.scene}) if it.scene else f"end of {it.chapter}"
        if it.when is not None:
            flag = parse_condition(it.when)[0]
            if flag_names is None:
                issues.append(f"{it.label} when {it.when!r}: flags/registry.yaml is missing")
            elif flag not in flag_names:
                issues.append(f"{it.label} when {it.when!r}: {flag!r} is not in flags/registry.yaml")
        states = flag_states.states_at(it.chapter, it.scene) if it.scene else flag_states.end_of(it.chapter)
        if states is None:
            continue
        held, total = holds_count(it.when, states)
        if it.when is not None and held == 0:
            issues.append(f"{it.label} when {it.when!r} never holds at {where}")
            continue
        if it.scene is not None and it.scene in branch_of.get(it.chapter, frozenset()):
            if it.when is None:
                issues.append(f"{it.label} at {where} (branch scene) has no when")
            elif held != total:
                issues.append(f"{it.label} when {it.when!r} does not hold on every path into {where} (branch scene)")
    return issues


def work_condition_issues(
    characters: Mapping[str, Mapping[str, Any]],
    registry: Sequence[Mapping[str, Any]] | None,
    **kw: Any,
) -> list[str]:
    items: list[Conditioned] = []
    for doc in characters.values():
        items += character_items(doc)
    if registry is not None:
        items += registry_items(registry)
    return condition_issues(items, **kw)


def _result(details: Sequence[str]) -> CheckResult:
    if details:
        return CheckResult(name="flag_conditions", status="WARNING", details=tuple(dict.fromkeys(details)))
    return CheckResult(name="flag_conditions", status="PASS", details=())


def check_flag_conditions(
    characters: Mapping[str, Mapping[str, Any]],
    registry: Sequence[Mapping[str, Any]] | None,
    **kw: Any,
) -> CheckResult:
    """'flag_conditions' for validate: every character and the registry, up to the chapter."""
    return _result(work_condition_issues(characters, registry, **kw))


def check_new_flag_conditions(
    before: tuple[Mapping[str, Mapping[str, Any]], Sequence[Mapping[str, Any]] | None],
    after: tuple[Mapping[str, Mapping[str, Any]], Sequence[Mapping[str, Any]] | None],
    **kw: Any,
) -> CheckResult:
    """'flag_conditions' for state_update: only problems that the patch introduced (phase2d-design decision 4)."""
    old = set(work_condition_issues(*before, **kw))
    return _result([i for i in work_condition_issues(*after, **kw) if i not in old])
