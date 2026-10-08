"""Character rules in effect at a scene position (spec §6.4.1, §11.7; phase2d-design §3).

The Controller computes, for each scene of a plan, the rules of each character in that scene with
changes and exceptions applied (the "rule table"). Claude and AGY use the table as it is and do not
recompute changes. All functions are pure; problems in the settings are returned as warnings.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Iterable, Mapping, Sequence

from novui.checks import CheckResult
from novui.positions import PositionError, chapter_index, compare_positions, format_position, position_key
from novui.yamlio import dumps_yaml

FIXED_RULE_NAMES: tuple[str, ...] = (
    "speech.first_person", "speech.formality", "speech.endings", "speech.habits", "speech.forbidden",
    "address.default", "personality", "behavior",
)

_PER_CHARACTER_RULE_RE = re.compile(r"^(address|relationships)\.C[0-9]{3,}$")


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
) -> tuple[str, list[str]]:
    """Value in effect at pos: the change with the latest `from` at or before pos, else default.

    Changes whose `from` cannot be compared are ignored with a warning. When two or more changes have the
    same `from`, the later one in the list wins and a warning is returned.
    """
    pos_key = position_key(order, pos)
    warnings: list[str] = []
    best: tuple[tuple[int, int], str] | None = None
    seen: dict[tuple[int, int], int] = {}
    for change in changes:
        start = change.get("from")
        try:
            key = position_key(order, start)
        except PositionError as exc:
            warnings.append(f"{label} change from {_fmt(start)}: {exc}")
            continue
        seen[key] = seen.get(key, 0) + 1
        if seen[key] == 2:
            warnings.append(f"{label} has multiple changes from {_fmt(start)}")
        if key <= pos_key and (best is None or key >= best[0]):
            best = (key, change["value"])
    return (best[1] if best is not None else default), warnings


def character_table(
    order: Sequence[str],
    doc: Mapping[str, Any],
    pos: Mapping[str, Any],
) -> tuple[dict[str, Any], list[str]]:
    """Rule table of one character at one position. Only fields present in doc are output
    (suspended_rules is always output)."""
    cid = doc["id"]
    warnings: list[str] = []
    table: dict[str, Any] = {"character": cid, "name": doc["name"]}

    if "speech" in doc:
        table["speech"] = dict(doc["speech"])

    if "address" in doc:
        address: dict[str, str] = {}
        for target, value in doc["address"].items():
            if isinstance(value, str):
                address[target] = value
            else:
                address[target], w = effective_value(
                    order, value["default"], value.get("changes", []), pos, label=f"{cid} address.{target}"
                )
                warnings += w
        if address:
            table["address"] = address

    if "relationships" in doc:
        rels: list[dict[str, str]] = []
        for rel in doc["relationships"]:
            state, w = effective_value(
                order, rel["state"], rel.get("changes", []), pos, label=f"{cid} relationships.{rel['with']}"
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
            if idx < current:
                known.append({"id": k["id"], "fact": k["fact"], "source_chapter": k["source_chapter"]})
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
) -> RuleTables:
    """Rule tables for every scene of the plan, for the scene's characters that exist in characters/."""
    if chapter_id not in order:
        return RuleTables(scenes=(), warnings=(f"chapter {chapter_id} not in chapters-order.yaml",))
    scenes: list[dict[str, Any]] = []
    warnings: list[str] = []
    for scene in plan.get("scenes", []):
        pos = {"chapter": chapter_id, "scene": scene["id"]}
        tables: list[dict[str, Any]] = []
        for cid in scene.get("characters", []):
            doc = characters.get(cid)
            if doc is None:
                continue
            table, w = character_table(order, doc, pos)
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
