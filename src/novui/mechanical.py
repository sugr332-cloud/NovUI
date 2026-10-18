"""Mechanical checks on draft text (§11.3, §6.5.1).

All checks here return PASS or WARNING. §11.3: mechanical results are WARNING, never STOP.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Any, Collection, Iterable, Mapping, Sequence

from novui.checks import CheckResult
from novui.schema import SchemaError, validate_or_raise
from novui.semantics import check_character, check_registry
from novui.yamlio import YamlError, load_yaml

SCENE_MARKER_RE: re.Pattern[str] = re.compile(r"^<!-- scene: (S[0-9]+) -->$")
# 区切りに見えるが SCENE_MARKER_RE に一致しない行の検出用
_MARKER_LIKE_RE: re.Pattern[str] = re.compile(r"<!--\s*scene\b", re.IGNORECASE)


@dataclass(frozen=True)
class SceneMarker:
    scene_id: str
    line: int  # 1 始まりの行番号


@dataclass(frozen=True)
class MechanicalInputs:
    characters: Mapping[str, dict[str, Any]]  # 人物 ID → characters/<ID>.yaml の内容
    foreshadow_ids: frozenset[str]
    prohibited_phrases: tuple[str, ...]
    load_errors: tuple[str, ...]  # 読み込めなかった資料（WARNING として報告する）


def find_scene_markers(text: str) -> tuple[list[SceneMarker], list[int]]:
    """Return (markers, malformed_lines). Lines are 1-based."""
    markers: list[SceneMarker] = []
    malformed: list[int] = []
    for idx, line in enumerate(text.splitlines(), start=1):
        m = SCENE_MARKER_RE.match(line)
        if m:
            markers.append(SceneMarker(scene_id=m.group(1), line=idx))
        elif _MARKER_LIKE_RE.search(line):
            malformed.append(idx)
    return markers, malformed


def strip_scene_markers(text: str) -> str:
    """Return text without scene marker lines."""
    return "\n".join(line for line in text.splitlines() if not SCENE_MARKER_RE.match(line))


def check_scene_markers(text: str, plan_scene_ids: Sequence[str]) -> CheckResult:
    """§6.5.1: markers must match plan scenes, same IDs, same order, exactly once each."""
    markers, malformed = find_scene_markers(text)
    details: list[str] = []

    for line in malformed:
        details.append(f"malformed scene marker at line {line}")

    planned = set(plan_scene_ids)
    lines_by_id: dict[str, list[int]] = {}
    for mk in markers:
        lines_by_id.setdefault(mk.scene_id, []).append(mk.line)

    for mk in markers:
        if mk.scene_id not in planned:
            details.append(f"scene {mk.scene_id} at line {mk.line} is not in plan")

    for sid, lines in lines_by_id.items():
        if sid in planned and len(lines) > 1:
            details.append(f"duplicate scene {sid} at lines {', '.join(str(n) for n in lines)}")

    for sid in plan_scene_ids:
        if sid not in lines_by_id:
            details.append(f"missing scene {sid}")

    # 順序：plan にある ID の初出の並びが、plan の順と一致すること
    seen: list[str] = []
    for mk in markers:
        if mk.scene_id in planned and mk.scene_id not in seen:
            seen.append(mk.scene_id)
    expected = [sid for sid in plan_scene_ids if sid in seen]
    if seen != expected:
        details.append(f"scene order mismatch: expected {', '.join(expected)}; got {', '.join(seen)}")

    return CheckResult(
        name="scene_markers",
        status="WARNING" if details else "PASS",
        details=tuple(details),
    )


def check_ref_ids(
    character_ids: Iterable[str],
    foreshadow_ids: Iterable[str],
    inputs: MechanicalInputs,
) -> CheckResult:
    """§11.3: referenced character / foreshadowing IDs must exist in characters/ and registry.yaml."""
    details: list[str] = []
    for cid in sorted(set(character_ids)):
        if cid not in inputs.characters:
            details.append(f"character {cid} not found in characters/")
    for fid in sorted(set(foreshadow_ids)):
        if fid not in inputs.foreshadow_ids:
            details.append(f"foreshadowing {fid} not found in foreshadowing/registry.yaml")
    return CheckResult(
        name="ref_ids",
        status="WARNING" if details else "PASS",
        details=tuple(details),
    )


def check_prohibited_phrases(text: str, phrases: Sequence[str]) -> CheckResult:
    """§11.3: occurrences of phrases registered in rules/prohibited.yaml (scene markers excluded)."""
    body = strip_scene_markers(text)
    details: list[str] = []
    for phrase in phrases:
        n = body.count(phrase)
        if n > 0:
            details.append(f"{phrase!r}: {n}")
    return CheckResult(
        name="prohibited_phrases",
        status="WARNING" if details else "PASS",
        details=tuple(details),
    )


def check_forbidden_words(
    text: str,
    characters: Mapping[str, dict[str, Any]],
    phrases: Collection[str],
) -> CheckResult:
    """§11.3: words in characters' speech.forbidden that are also registered in rules/prohibited.yaml."""
    body = strip_scene_markers(text)
    registered = set(phrases)
    details: list[str] = []
    for cid in sorted(characters):
        speech = characters[cid].get("speech") or {}
        for word in speech.get("forbidden", []):
            if word not in registered:
                continue
            n = body.count(word)
            if n > 0:
                details.append(f"{cid} speech.forbidden {word!r}: {n}")
    return CheckResult(
        name="forbidden_words",
        status="WARNING" if details else "PASS",
        details=tuple(details),
    )


def load_mechanical_inputs(root: Path) -> MechanicalInputs:
    """Load characters/, foreshadowing/registry.yaml and rules/prohibited.yaml under root.

    Files that cannot be read or validated are reported in load_errors instead of raising.
    """
    errors: list[str] = []

    characters: dict[str, dict[str, Any]] = {}
    chars_dir = root / "characters"
    if chars_dir.is_dir():
        for p in sorted(chars_dir.glob("*.yaml")):
            rel = f"characters/{p.name}"
            try:
                doc = load_yaml(p)
                validate_or_raise(doc, "character")
                sem = check_character(doc)
                if sem:
                    raise SchemaError(f"semantic errors: {sem}", errors=sem)
            except (YamlError, SchemaError, OSError) as exc:
                errors.append(f"{rel}: {exc}")
                continue
            if doc["id"] != p.stem:
                errors.append(f"{rel}: id {doc['id']!r} does not match file name")
                continue
            characters[doc["id"]] = doc

    foreshadow_ids: set[str] = set()
    reg_path = root / "foreshadowing" / "registry.yaml"
    if reg_path.is_file():
        try:
            reg = load_yaml(reg_path)
            validate_or_raise(reg, "registry")
            sem = check_registry(reg)
            if sem:
                raise SchemaError(f"semantic errors: {sem}", errors=sem)
            foreshadow_ids = {item["id"] for item in reg}
        except (YamlError, SchemaError, OSError) as exc:
            errors.append(f"foreshadowing/registry.yaml: {exc}")

    phrases: tuple[str, ...] = ()
    pro_path = root / "rules" / "prohibited.yaml"
    if pro_path.is_file():
        try:
            pro = load_yaml(pro_path)
            validate_or_raise(pro, "prohibited")
            phrases = tuple(pro["phrases"])
        except (YamlError, SchemaError, OSError) as exc:
            errors.append(f"rules/prohibited.yaml: {exc}")

    return MechanicalInputs(
        characters=characters,
        foreshadow_ids=frozenset(foreshadow_ids),
        prohibited_phrases=phrases,
        load_errors=tuple(errors),
    )


def plan_ref_ids(plan: Mapping[str, Any]) -> tuple[list[str], list[str]]:
    """Return (character_ids, foreshadow_ids) referenced by plan scenes, in order of first appearance."""
    chars: list[str] = []
    fores: list[str] = []
    for scene in plan.get("scenes", []):
        for cid in scene.get("characters", []):
            if cid not in chars:
                chars.append(cid)
        for fid in scene.get("foreshadowing", []):
            if fid not in fores:
                fores.append(fid)
    return chars, fores


def run_draft_mechanical_checks(
    text: str,
    plan: Mapping[str, Any],
    inputs: MechanicalInputs,
) -> list[CheckResult]:
    """Mechanical checks for a draft against its plan (§11.3)."""
    results: list[CheckResult] = []
    if inputs.load_errors:
        results.append(CheckResult(name="mechanical_inputs", status="WARNING", details=inputs.load_errors))
    scene_ids = [s["id"] for s in plan.get("scenes", [])]
    results.append(check_scene_markers(text, scene_ids))
    char_ids, fore_ids = plan_ref_ids(plan)
    results.append(check_ref_ids(char_ids, fore_ids, inputs))
    results.append(check_prohibited_phrases(text, inputs.prohibited_phrases))
    results.append(check_forbidden_words(text, inputs.characters, inputs.prohibited_phrases))
    return results
