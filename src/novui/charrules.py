"""Character rules in effect at a scene position (spec §6.4.1, §11.7; phase2d-design §3).

The Controller computes, for each scene of a plan, the rules of each character in that scene with
changes and exceptions applied (the "rule table"). Claude and AGY use the table as it is and do not
recompute changes. All functions are pure; problems in the settings are returned as warnings.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Collection, Iterable, Mapping, Sequence

from novui.checks import CheckResult
from novui.flagstate import START_STATES, FlagStates, State, condition_holds, holds_count, parse_condition
from novui.positions import PositionError, chapter_index, compare_positions, format_position, position_key
from novui.yamlio import dumps_yaml

FIXED_RULE_NAMES: tuple[str, ...] = (
    "speech.first_person", "speech.formality", "speech.endings", "speech.habits", "speech.forbidden",
    "address.default", "personality", "behavior",
)

_PER_CHARACTER_RULE_RE = re.compile(r"^(address|relationships)\.C[0-9]{3,}$")

# 1つの値が依存してよいフラグの数（phase2f-design §3.2、決定 4）
MAX_FLAGS_PER_VALUE = 2


@dataclass(frozen=True)
class RuleTables:
    scenes: tuple[dict[str, Any], ...]  # [{"scene": "S1", "characters": [表, ...]}, ...]（plan の場面の順）
    warnings: tuple[str, ...]


def _unique(items: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(items))


def _fmt(pos: Any) -> str:
    try:
        return format_position(pos)
    except (KeyError, TypeError):
        return repr(pos)


def is_known_rule(rule: str) -> bool:
    """True for the rule names exceptions may name (phase2d-design §9)."""
    return rule in FIXED_RULE_NAMES or bool(_PER_CHARACTER_RULE_RE.match(rule))


def effective_value(
    order: Sequence[str],
    default: str,
    changes: Sequence[Mapping[str, Any]],
    pos: Mapping[str, Any],
    *,
    label: str,
    state: State = frozenset(),
) -> tuple[str, list[str]]:
    """Value in effect at pos: the change with the latest `from` at or before pos whose `when` holds in state,
    else default.

    Changes whose `from` cannot be compared are ignored with a warning. When two or more changes have the
    same `from` and the same `when`, the later one in the list wins and a warning is returned.
    """
    pos_key = position_key(order, pos)
    warnings: list[str] = []
    best: tuple[tuple[int, int], str] | None = None
    seen: dict[tuple[tuple[int, int], str | None], int] = {}
    for change in changes:
        start = change.get("from")
        try:
            key = position_key(order, start)
        except PositionError as exc:
            warnings.append(f"{label} change from {_fmt(start)}: {exc}")
            continue
        when = change.get("when")
        seen[(key, when)] = seen.get((key, when), 0) + 1
        if seen[(key, when)] == 2:
            warnings.append(f"{label} has multiple changes from {_fmt(start)}" + (f" when {when}" if when else ""))
        if key <= pos_key and condition_holds(when, state) and (best is None or key >= best[0]):
            best = (key, change["value"])
    return (best[1] if best is not None else default), warnings


def _listed(default: str, changes: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """The default and the changes as they are, for a value that cannot be shown per flag."""
    return {"default": default, "changes": [dict(c) for c in changes]}


def flag_value(
    order: Sequence[str],
    default: str,
    changes: Sequence[Mapping[str, Any]],
    pos: Mapping[str, Any],
    states: Collection[State] | None,
    *,
    label: str,
) -> tuple[Any, list[str]]:
    """Value in effect at pos over the flag states of pos (phase2f-design §3.1).

    A single value when every state gives the same value. Otherwise rows [{value, flags: {flag: bool}}] for
    the flags the value depends on, at most MAX_FLAGS_PER_VALUE flags. With more flags, or when the states are
    unknown (None), the default and the changes are listed as they are, with a warning.
    """
    if not any("when" in c for c in changes):
        return effective_value(order, default, changes, pos, label=label)
    if states is None:
        return _listed(default, changes), [f"{label}: flag states at {_fmt(pos)} are unknown; changes are listed"]
    warnings: list[str] = []
    values: dict[State, str] = {}
    for st in states:
        values[st], w = effective_value(order, default, changes, pos, label=label, state=st)
        warnings += w
    if len(set(values.values())) <= 1:
        return (next(iter(values.values())) if values else default), warnings
    flags = sorted({parse_condition(c["when"])[0] for c in changes if "when" in c})
    dep = [f for f in flags if any(f in st for st in states) and any(f not in st for st in states)]
    if len(dep) > MAX_FLAGS_PER_VALUE:
        warnings.append(
            f"{label} depends on {len(dep)} flags at {_fmt(pos)} (limit {MAX_FLAGS_PER_VALUE}); changes are listed"
        )
        return _listed(default, changes), warnings
    rows: dict[tuple[bool, ...], str] = {}
    for st, value in values.items():
        rows.setdefault(tuple(f in st for f in dep), value)
    return [
        {"value": rows[key], "flags": dict(zip(dep, key))} for key in sorted(rows)
    ], warnings


def character_table(
    order: Sequence[str],
    doc: Mapping[str, Any],
    pos: Mapping[str, Any],
    states: Collection[State] | None = START_STATES,
) -> tuple[dict[str, Any], list[str]]:
    """Rule table of one character at one position over the flag states of the position (None: unknown).
    Only fields present in doc are output (suspended_rules is always output)."""
    cid = doc["id"]
    warnings: list[str] = []
    table: dict[str, Any] = {"character": cid, "name": doc["name"]}

    # 台本の形式の作品の表示名と表情（phase2e-design §2）
    for key in ("display_name", "expressions"):
        if key in doc:
            table[key] = list(doc[key]) if key == "expressions" else doc[key]

    if "speech" in doc:
        table["speech"] = dict(doc["speech"])

    if "address" in doc:
        address: dict[str, str] = {}
        for target, value in doc["address"].items():
            if isinstance(value, str):
                address[target] = value
            else:
                address[target], w = flag_value(
                    order, value["default"], value.get("changes", []), pos, states, label=f"{cid} address.{target}"
                )
                warnings += w
        if address:
            table["address"] = address

    if "relationships" in doc:
        rels: list[dict[str, Any]] = []
        for rel in doc["relationships"]:
            state, w = flag_value(
                order, rel["state"], rel.get("changes", []), pos, states,
                label=f"{cid} relationships.{rel['with']}",
            )
            warnings += w
            rels.append({"with": rel["with"], "state": state})
        table["relationships"] = rels

    suspended: list[dict[str, str]] = []
    for i, exc in enumerate(doc.get("exceptions", [])):
        if not is_known_rule(exc["rule"]):
            warnings.append(f"{cid} exceptions[{i}] unknown rule {exc['rule']!r}")
        try:
            if compare_positions(order, exc["from"], exc["to"]) > 0:
                warnings.append(f"{cid} exceptions[{i}] from {_fmt(exc['from'])} is after to {_fmt(exc['to'])}")
                continue
        except PositionError as err:
            warnings.append(f"{cid} exceptions[{i}] {_fmt(exc['from'])} - {_fmt(exc['to'])}: {err}")
            continue
        if compare_positions(order, exc["from"], pos) <= 0 <= compare_positions(order, exc["to"], pos):
            suspended.append({"rule": exc["rule"], "reason": exc["reason"]})
    table["suspended_rules"] = suspended

    if "knowledge" in doc:
        current = chapter_index(order, pos["chapter"])
        known: list[dict[str, str]] = []
        for k in doc["knowledge"]:
            try:
                idx = chapter_index(order, k["source_chapter"])
            except PositionError:
                warnings.append(
                    f"{cid} knowledge {k['id']} source_chapter {k['source_chapter']} not in chapters-order.yaml"
                )
                continue
            if idx >= current:
                continue
            entry = {"id": k["id"], "fact": k["fact"], "source_chapter": k["source_chapter"]}
            when = k.get("when")
            if when is not None:
                # どの状態でも成り立つ条件は解き、どの状態でも成り立たない知識は載せない（phase2f-design §3.1）
                held, total = holds_count(when, states) if states is not None else (0, -1)
                if held == 0 and total > 0:
                    continue
                if held != total:
                    entry["when"] = when
            known.append(entry)
        table["knowledge"] = known

    for key in ("personality", "behavior"):
        if key in doc:
            table[key] = list(doc[key])

    return table, warnings


def scene_rule_tables(
    order: Sequence[str],
    chapter_id: str,
    plan: Mapping[str, Any],
    characters: Mapping[str, Mapping[str, Any]],
    flag_states: FlagStates | None = None,
) -> RuleTables:
    """Rule tables for every scene of the plan, for the scene's characters that exist in characters/.
    Without flag_states, no flag is set anywhere (a work without choices)."""
    if chapter_id not in order:
        return RuleTables(scenes=(), warnings=(f"chapter {chapter_id} not in chapters-order.yaml",))
    scenes: list[dict[str, Any]] = []
    warnings: list[str] = list(flag_states.warnings) if flag_states is not None else []
    for scene in plan.get("scenes", []):
        pos = {"chapter": chapter_id, "scene": scene["id"]}
        states = flag_states.states_at(chapter_id, scene["id"]) if flag_states is not None else START_STATES
        tables: list[dict[str, Any]] = []
        for cid in scene.get("characters", []):
            doc = characters.get(cid)
            if doc is None:
                continue
            table, w = character_table(order, doc, pos, states)
            tables.append(table)
            warnings += w
        if tables:
            scenes.append({"scene": scene["id"], "characters": tables})
    return RuleTables(scenes=tuple(scenes), warnings=tuple(_unique(warnings)))


def render_rule_tables(tables: RuleTables) -> str:
    """Text inserted in prompts as {{character_rules}}. 'なし' when there is no table."""
    if not tables.scenes:
        return "なし"
    return dumps_yaml(list(tables.scenes)).rstrip("\n")


def check_character_rules(tables: RuleTables) -> CheckResult:
    """Mechanical check 'character_rules': WARNING when computing the tables found problems in the settings."""
    if tables.warnings:
        return CheckResult(name="character_rules", status="WARNING", details=tables.warnings)
    return CheckResult(name="character_rules", status="PASS", details=())
