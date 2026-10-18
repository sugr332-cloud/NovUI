"""Mechanical checks of the novel game script format (docs/script-format.md, phase2e-design §4).

All checks return PASS or WARNING. In the draft Job, script_syntax and script_flow are turned into FAIL
(the engine cannot read such a script). §11.3: in validate every mechanical result stays WARNING.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from novui.checks import CheckResult, check_char_range, count_chars
from novui.flagstate import branch_scenes as branch_only_scenes  # noqa: F401  2-E4 からの名前
from novui.schema import SchemaError, validate_or_raise
from novui.script import ParsedScript, ScriptLine, body_text, parse_script
from novui.semantics import SEMANTIC_CHECKS, check_plan
from novui.yamlio import YamlError, load_yaml

DRAFT_FAIL_CHECKS: frozenset[str] = frozenset({"script_syntax", "script_flow", "script_flags"})


@dataclass(frozen=True)
class ScriptInputs:
    characters: Mapping[str, Mapping[str, Any]]  # mechanical.MechanicalInputs.characters
    backgrounds: frozenset[str] | None  # assets/backgrounds.yaml（無ければ None）
    flags: frozenset[str] | None  # flags/registry.yaml（無ければ None）
    max_line_chars: int | None  # rules/script.yaml
    load_errors: tuple[str, ...]


def _unique(items: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(items))


def _result(name: str, details: Sequence[str]) -> CheckResult:
    if details:
        return CheckResult(name=name, status="WARNING", details=tuple(_unique(details)))
    return CheckResult(name=name, status="PASS", details=())


def _load(root: Path, rel: str, schema_name: str, errors: list[str]) -> Any | None:
    path = root / rel
    if not path.is_file():
        return None
    try:
        doc = load_yaml(path)
        validate_or_raise(doc, schema_name)
        sem = SEMANTIC_CHECKS[schema_name](doc)
        if sem:
            raise SchemaError(f"semantic errors: {sem}", errors=sem)
    except (YamlError, SchemaError, OSError) as exc:
        errors.append(f"{rel}: {exc}")
        return None
    return doc


def load_script_inputs(root: Path, characters: Mapping[str, Mapping[str, Any]]) -> ScriptInputs:
    """Read assets/backgrounds.yaml, flags/registry.yaml and rules/script.yaml under root.
    Files that cannot be read are reported in load_errors and treated as absent."""
    errors: list[str] = []
    backgrounds = _load(root, "assets/backgrounds.yaml", "backgrounds", errors)
    flags = _load(root, "flags/registry.yaml", "flags", errors)
    rules = _load(root, "rules/script.yaml", "script_rules", errors)
    return ScriptInputs(
        characters=characters,
        backgrounds=frozenset(backgrounds) if backgrounds is not None else None,
        flags=frozenset(f["name"] for f in flags) if flags is not None else None,
        max_line_chars=(rules or {}).get("max_line_chars"),
        load_errors=tuple(errors),
    )


def display_names(characters: Mapping[str, Mapping[str, Any]]) -> tuple[dict[str, str], list[str]]:
    """(display name -> character ID, warnings about display names used by two or more characters)."""
    by_name: dict[str, str] = {}
    warnings: list[str] = []
    for cid in sorted(characters):
        name = characters[cid].get("display_name")
        if name is None:
            continue
        if name in by_name:
            warnings.append(f"display_name {name!r} is used by {by_name[name]} and {cid}")
            continue
        by_name[name] = cid
    return by_name, warnings


def _plan_scenes(plan: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    return {s["id"]: s for s in plan.get("scenes", [])}


def _lines(parsed: ParsedScript) -> Iterable[tuple[str, ScriptLine]]:
    for scene in parsed.scenes:
        for ln in scene.lines:
            yield scene.scene_id, ln


def check_script_syntax(parsed: ParsedScript) -> CheckResult:
    """'script_syntax': lines that cannot be read and structural errors of the parser."""
    return _result("script_syntax", [f"line {e.lineno}: {e.message}" for e in parsed.errors])


def check_script_flow(parsed: ParsedScript, plan: Mapping[str, Any]) -> CheckResult:
    """'script_flow': scenes match the plan (same IDs, same order, each once) and each scene ends as the plan's
    choice/next says."""
    details: list[str] = []
    plan_ids = [s["id"] for s in plan.get("scenes", [])]
    planned = set(plan_ids)
    script_ids = [s.scene_id for s in parsed.scenes]
    for sid in _unique(script_ids):
        if sid not in planned:
            details.append(f"scene {sid} is not in plan")
    for sid in plan_ids:
        if sid not in script_ids:
            details.append(f"missing scene {sid}")
    seen = [sid for sid in _unique(script_ids) if sid in planned]
    expected = [sid for sid in plan_ids if sid in seen]
    if seen != expected:
        details.append(f"scene order mismatch: expected {', '.join(expected)}; got {', '.join(seen)}")

    scenes = _plan_scenes(plan)
    done: set[str] = set()
    for block in parsed.scenes:
        if block.scene_id not in scenes or block.scene_id in done:
            continue
        done.add(block.scene_id)
        ps = scenes[block.scene_id]
        sid = block.scene_id
        has_choice = any(ln.kind == "choice" for ln in block.lines)
        options = [
            (ln.args["text"], ln.args["goto"], ln.args["flag"]) for ln in block.lines if ln.kind == "option"
        ]
        gotos = [ln.args["scene_id"] for ln in block.lines if ln.kind == "goto"]
        if ps.get("choice"):
            want = [(c["text"], c["goto"], c.get("flag")) for c in ps["choice"]]
            if not has_choice:
                details.append(f"scene {sid}: @choice is missing")
            elif options != want:
                details.append(f"scene {sid}: choice options {options} do not match plan {want}")
        elif has_choice:
            details.append(f"scene {sid}: @choice is not in plan")
        if ps.get("next"):
            if gotos != [ps["next"]]:
                details.append(f"scene {sid}: expected @goto {ps['next']}, got {gotos or 'none'}")
        elif gotos:
            details.append(f"scene {sid}: @goto {gotos[0]} is not in plan")
    return _result("script_flow", details)


def load_earlier_plans(root: Path, chapter_id: str) -> dict[str, Mapping[str, Any]]:
    """plan.yaml of the chapters before chapter_id in chapters-order.yaml (unreadable plans are skipped)."""
    try:
        order = list(load_yaml(root / "chapters-order.yaml"))
    except (YamlError, OSError, TypeError):
        return {}
    if chapter_id not in order:
        return {}
    plans: dict[str, Mapping[str, Any]] = {}
    for ch in order[: order.index(chapter_id)]:
        path = root / "chapters" / ch / "plan.yaml"
        if not path.is_file():
            continue
        try:
            doc = load_yaml(path)
            validate_or_raise(doc, "plan")
        except (YamlError, SchemaError, OSError):
            continue
        if check_plan(doc):
            continue
        plans[ch] = doc
    return plans


def flags_set_before(
    earlier_plans: Mapping[str, Mapping[str, Any]], plan: Mapping[str, Any], scene_id: str
) -> frozenset[str]:
    """Flags that may be set before scene_id: every choice flag of the earlier chapters, and the choice flags
    of the scenes before scene_id in this chapter's plan."""
    flags: set[str] = set()
    for p in earlier_plans.values():
        for s in p.get("scenes", []):
            flags |= {c["flag"] for c in s.get("choice") or [] if c.get("flag")}
    for s in plan.get("scenes", []):
        if s["id"] == scene_id:
            break
        flags |= {c["flag"] for c in s.get("choice") or [] if c.get("flag")}
    return frozenset(flags)


def check_script_flags(
    parsed: ParsedScript,
    plan: Mapping[str, Any],
    inputs: ScriptInputs,
    *,
    earlier_plans: Mapping[str, Mapping[str, Any]],
) -> CheckResult:
    """'script_flags': flags are registered in flags/registry.yaml, and an @if flag may be set before its scene.
    (That option flags match the plan is checked by script_flow.)"""
    details: list[str] = []
    used = [(sid, ln) for sid, ln in _lines(parsed) if ln.kind in ("option", "if") and ln.args.get("flag")]
    if used and inputs.flags is None:
        return _result("script_flags", ["flags are used but flags/registry.yaml is missing"])
    for sid, ln in used:
        flag = ln.args["flag"]
        if flag not in (inputs.flags or ()):
            details.append(f"line {ln.lineno}: flag {flag!r} is not in flags/registry.yaml")
        if ln.kind == "if" and flag not in flags_set_before(earlier_plans, plan, sid):
            details.append(f"line {ln.lineno}: flag {flag!r} cannot be set before scene {sid}")
    return _result("script_flags", details)


def check_script_speakers(parsed: ParsedScript, plan: Mapping[str, Any], inputs: ScriptInputs) -> CheckResult:
    """'script_speakers': speakers are the display names of the scene's characters or its extras."""
    details: list[str] = []
    names, dup = display_names(inputs.characters)
    details += dup
    all_names = set(names)
    scenes = _plan_scenes(plan)
    for block in parsed.scenes:
        ps = scenes.get(block.scene_id)
        if ps is None:
            continue
        allowed = set(ps.get("extras", []))
        for cid in ps.get("characters", []):
            doc = inputs.characters.get(cid)
            if doc is None:
                continue
            if doc.get("display_name") is None:
                details.append(f"scene {block.scene_id}: {cid} has no display_name")
            else:
                allowed.add(doc["display_name"])
        for extra in ps.get("extras", []):
            if extra in all_names:
                details.append(f"scene {block.scene_id}: extra {extra!r} is the display_name of {names[extra]}")
        for ln in block.lines:
            if ln.kind in ("say", "think") and ln.args["speaker"] not in allowed:
                details.append(
                    f"line {ln.lineno}: speaker {ln.args['speaker']!r} is not a character or extra of scene {block.scene_id}"
                )
    return _result("script_speakers", details)


def _character_of(ln: ScriptLine, names: Mapping[str, str]) -> str | None:
    if ln.kind in ("enter", "exit", "face"):
        return ln.args["character_id"]
    if ln.kind in ("say", "think"):
        return names.get(ln.args["speaker"])
    return None


def check_script_expressions(parsed: ParsedScript, inputs: ScriptInputs) -> CheckResult:
    """'script_expressions': expressions are in the character's expressions (characters without expressions
    are not checked; extras have no expressions)."""
    details: list[str] = []
    names, _ = display_names(inputs.characters)
    for _sid, ln in _lines(parsed):
        expr = ln.args.get("expression") if ln.kind in ("enter", "face", "say", "think") else None
        if expr is None:
            continue
        cid = _character_of(ln, names)
        if cid is None:
            details.append(f"line {ln.lineno}: {ln.args.get('speaker')!r} has no expressions (extra)")
            continue
        exprs = (inputs.characters.get(cid) or {}).get("expressions")
        if exprs is not None and expr not in exprs:
            details.append(f"line {ln.lineno}: {cid} has no expression {expr!r}")
    return _result("script_expressions", details)


def check_script_stage(parsed: ParsedScript, plan: Mapping[str, Any], inputs: ScriptInputs) -> CheckResult:
    """'script_stage': follow the sprites on the stage in each scene (empty at the start of a scene)."""
    details: list[str] = []
    names, _ = display_names(inputs.characters)
    scenes = _plan_scenes(plan)
    for block in parsed.scenes:
        sid = block.scene_id
        in_scene = set((scenes.get(sid) or {}).get("characters", []))
        if not any(ln.kind == "bg" for ln in block.lines):
            details.append(f"scene {sid} has no @bg")
        stage: dict[str, str] = {}  # 人物 ID → 立ち位置
        for ln in block.lines:
            if ln.kind in ("enter", "exit", "face"):
                cid = ln.args["character_id"]
                if cid not in in_scene:
                    details.append(f"line {ln.lineno}: {cid} is not a character of scene {sid} in plan")
            if ln.kind == "enter":
                cid, pos = ln.args["character_id"], ln.args["position"]
                if cid in stage:
                    details.append(f"line {ln.lineno}: {cid} enters but is already on stage")
                elif pos in stage.values():
                    other = next(c for c, p in stage.items() if p == pos)
                    details.append(f"line {ln.lineno}: {cid} enters at {pos} where {other} already is")
                stage[cid] = pos
            elif ln.kind == "exit":
                if ln.args["character_id"] not in stage:
                    details.append(f"line {ln.lineno}: {ln.args['character_id']} exits but is not on stage")
                stage.pop(ln.args["character_id"], None)
            elif ln.kind == "face":
                if ln.args["character_id"] not in stage:
                    details.append(f"line {ln.lineno}: @face {ln.args['character_id']} who is not on stage")
            elif ln.kind in ("say", "think"):
                cid = names.get(ln.args["speaker"])
                if cid is not None and cid not in stage:
                    details.append(f"line {ln.lineno}: {cid} speaks off-screen (not on stage)")
    return _result("script_stage", details)


def check_script_backgrounds(parsed: ParsedScript, plan: Mapping[str, Any], inputs: ScriptInputs) -> CheckResult:
    """'script_backgrounds': backgrounds are registered in assets/backgrounds.yaml and match plan's background.
    PASS when there is no list."""
    if inputs.backgrounds is None:
        return _result("script_backgrounds", [])
    details: list[str] = []
    scenes = _plan_scenes(plan)
    for block in parsed.scenes:
        bgs = [ln for ln in block.lines if ln.kind == "bg"]
        for ln in bgs:
            if ln.args["name"] not in inputs.backgrounds:
                details.append(f"line {ln.lineno}: background {ln.args['name']!r} is not in assets/backgrounds.yaml")
        want = (scenes.get(block.scene_id) or {}).get("background")
        if want is not None and (not bgs or bgs[0].args["name"] != want):
            got = bgs[0].args["name"] if bgs else "none"
            details.append(f"scene {block.scene_id}: background {got!r} does not match plan {want!r}")
    return _result("script_backgrounds", details)


def check_script_speaker_words(parsed: ParsedScript, inputs: ScriptInputs) -> CheckResult:
    """'script_speaker_words': the speaker's lines contain no word of the speaker's speech.forbidden."""
    details: list[str] = []
    names, _ = display_names(inputs.characters)
    for _sid, ln in _lines(parsed):
        if ln.kind != "say":
            continue
        cid = names.get(ln.args["speaker"])
        if cid is None:
            continue
        for word in ((inputs.characters[cid].get("speech") or {}).get("forbidden") or []):
            if word in ln.args["text"]:
                details.append(f"line {ln.lineno}: {cid} says forbidden word {word!r}")
    return _result("script_speaker_words", details)


def check_script_line_length(parsed: ParsedScript, inputs: ScriptInputs) -> CheckResult:
    """'script_line_length': text of a line is not longer than rules/script.yaml max_line_chars."""
    if inputs.max_line_chars is None:
        return _result("script_line_length", [])
    details = [
        f"line {ln.lineno}: {count_chars(ln.args['text'])} chars > {inputs.max_line_chars}"
        for _sid, ln in _lines(parsed)
        if ln.kind in ("say", "think", "narration") and count_chars(ln.args["text"]) > inputs.max_line_chars
    ]
    return _result("script_line_length", details)


def check_script_suspicious_lines(parsed: ParsedScript) -> CheckResult:
    """'script_suspicious_lines': narration that looks like a broken dialogue line."""
    details: list[str] = []
    for _sid, ln in _lines(parsed):
        if ln.kind != "narration":
            continue
        text = ln.args["text"]
        if "「" not in text or not text.endswith("」"):
            continue
        prefix = text.split("「", 1)[0]
        if len(prefix) <= 10 and "。" not in prefix and "、" not in prefix:
            details.append(f"line {ln.lineno}: narration looks like a dialogue line: {text[:30]!r}")
    return _result("script_suspicious_lines", details)


def run_script_checks(
    text: str,
    plan: Mapping[str, Any],
    inputs: ScriptInputs,
    *,
    for_draft: bool,
    earlier_plans: Mapping[str, Mapping[str, Any]] | None = None,
) -> list[CheckResult]:
    """All script checks in order. for_draft turns WARNING of script_syntax, script_flow and script_flags
    into FAIL."""
    parsed = parse_script(text)
    results: list[CheckResult] = []
    if inputs.load_errors:
        results.append(CheckResult(name="script_inputs", status="WARNING", details=inputs.load_errors))
    results += [
        check_script_syntax(parsed),
        check_script_flow(parsed, plan),
        check_script_flags(parsed, plan, inputs, earlier_plans=earlier_plans or {}),
        check_script_speakers(parsed, plan, inputs),
        check_script_expressions(parsed, inputs),
        check_script_stage(parsed, plan, inputs),
        check_script_backgrounds(parsed, plan, inputs),
        check_script_speaker_words(parsed, inputs),
        check_script_line_length(parsed, inputs),
        check_script_suspicious_lines(parsed),
    ]
    if for_draft:
        results = [
            replace(c, status="FAIL") if c.name in DRAFT_FAIL_CHECKS and c.status == "WARNING" else c
            for c in results
        ]
    return results


def script_char_count(text: str, min_chars: int, max_chars: int) -> CheckResult:
    """'char_count' of the script: only the text of dialogue, inner voice and narration is counted."""
    return check_char_range(body_text(parse_script(text)), min_chars, max_chars)
