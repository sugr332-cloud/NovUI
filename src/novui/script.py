"""Parser of the novel game script format (docs/script-format.md, phase2e-design §1).

The Controller and the game engine read draft.md by the same definition. Parsing never raises: lines that
cannot be read and structural problems are returned as errors, and every line that could be read is kept.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any, Mapping

_FLAG = r"(?!not\b)[a-z][a-z0-9_]{0,63}"
_NAME = r"[^\s@<\-［「（][^\s［「（]*"

# script-format §2 の表の順。台詞は心の声より先に当てる
_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("scene", re.compile(r"^<!-- scene: (?P<scene_id>S[0-9]+) -->$")),
    ("bg", re.compile(r"^@bg (?P<name>\S+)$")),
    ("enter", re.compile(r"^@enter (?P<character_id>C[0-9]{3,}) (?P<position>left|center|right)(?: (?P<expression>\S+))?$")),
    ("exit", re.compile(r"^@exit (?P<character_id>C[0-9]{3,})$")),
    ("face", re.compile(r"^@face (?P<character_id>C[0-9]{3,}) (?P<expression>\S+)$")),
    ("choice", re.compile(r"^@choice$")),
    ("option", re.compile(rf"^- (?P<text>.+?) -> (?P<goto>S[0-9]+)(?: \+(?P<flag>{_FLAG}))?$")),
    ("goto", re.compile(r"^@goto (?P<scene_id>S[0-9]+)$")),
    ("if", re.compile(rf"^@if (?P<negated>not )?(?P<flag>{_FLAG})$")),
    ("else", re.compile(r"^@else$")),
    ("endif", re.compile(r"^@endif$")),
    ("say", re.compile(rf"^(?P<speaker>{_NAME})(?:［(?P<expression>[^］\s]+)］)?「(?P<text>.*)」$")),
    ("think", re.compile(rf"^(?P<speaker>{_NAME})(?:［(?P<expression>[^］\s]+)］)?（(?P<text>.*)）$")),
)

TEXT_KINDS: frozenset[str] = frozenset({"say", "think", "narration"})
IF_ALLOWED_KINDS: frozenset[str] = frozenset({"say", "think", "narration", "face"})


@dataclass(frozen=True)
class ScriptLine:
    lineno: int
    kind: str  # scene, bg, enter, exit, face, choice, option, goto, if, else, endif, say, think, narration
    args: Mapping[str, Any]
    raw: str


@dataclass(frozen=True)
class ScriptError:
    lineno: int
    message: str


@dataclass(frozen=True)
class SceneBlock:
    scene_id: str
    lineno: int
    lines: tuple[ScriptLine, ...]  # 区切りの行を含まない。空行を含まない


@dataclass(frozen=True)
class ParsedScript:
    preamble: tuple[ScriptLine, ...]
    scenes: tuple[SceneBlock, ...]
    errors: tuple[ScriptError, ...]


def parse_line(raw: str, lineno: int) -> ScriptLine | ScriptError:
    """Parse one non-empty line (surrounding whitespace is stripped first)."""
    line = raw.strip()
    for kind, pattern in _PATTERNS:
        m = pattern.match(line)
        if m is None:
            continue
        args: dict[str, Any] = dict(m.groupdict())
        if kind == "if":
            args["negated"] = args["negated"] is not None
        return ScriptLine(lineno=lineno, kind=kind, args=args, raw=raw)
    if line.startswith("@"):
        return ScriptError(lineno, f"unknown or malformed command {line.split()[0]!r}")
    if line.startswith("<!--"):
        return ScriptError(lineno, "malformed scene marker or comment")
    if line.startswith("- "):
        return ScriptError(lineno, "malformed choice option")
    return ScriptLine(lineno=lineno, kind="narration", args={"text": line}, raw=raw)


@dataclass
class _SceneState:
    scene_id: str
    lineno: int
    lines: list[ScriptLine] = field(default_factory=list)
    choice_line: int | None = None
    options: int = 0
    closed_by: str | None = None  # "choice" or "goto"
    if_line: int | None = None
    seen_else: bool = False


def _close_scene(state: _SceneState, errors: list[ScriptError]) -> SceneBlock:
    if state.choice_line is not None and state.options < 2:
        errors.append(ScriptError(state.choice_line, "@choice needs at least two options"))
    if state.if_line is not None:
        errors.append(ScriptError(state.if_line, "@if is not closed by @endif in the scene"))
    return SceneBlock(scene_id=state.scene_id, lineno=state.lineno, lines=tuple(state.lines))


def _structure_error(state: _SceneState, ln: ScriptLine) -> str | None:
    """Structural error of ln in the scene, or None. Updates state."""
    kind = ln.kind
    if state.closed_by == "goto":
        return "no line may follow @goto in the scene"
    if state.closed_by == "choice":
        if kind == "option":
            state.options += 1
            return None
        state.closed_by = "after_choice"
        return "no line may follow the options of @choice in the scene"
    if state.closed_by == "after_choice":
        return "no line may follow the options of @choice in the scene"
    if kind == "option":
        return "choice option without @choice"

    if state.if_line is not None:
        if kind == "if":
            return "@if must not be nested"
        if kind == "else":
            if state.seen_else:
                return "second @else in the same @if"
            state.seen_else = True
            return None
        if kind == "endif":
            state.if_line = None
            state.seen_else = False
            return None
        if kind not in IF_ALLOWED_KINDS:
            return f"@{kind} is not allowed inside @if"
        return None
    if kind in ("else", "endif"):
        return f"@{kind} without @if"
    if kind == "if":
        state.if_line = ln.lineno
        return None
    if kind == "choice":
        state.choice_line = ln.lineno
        state.closed_by = "choice"
    elif kind == "goto":
        state.closed_by = "goto"
    return None


def parse_script(text: str) -> ParsedScript:
    """Parse a whole draft.md. Errors do not stop parsing; every readable line is kept."""
    errors: list[ScriptError] = []
    preamble: list[ScriptLine] = []
    scenes: list[SceneBlock] = []
    seen_ids: dict[str, int] = {}
    state: _SceneState | None = None

    for lineno, raw in enumerate(text.splitlines(), start=1):
        if not raw.strip():
            continue
        parsed = parse_line(raw, lineno)
        if isinstance(parsed, ScriptError):
            errors.append(parsed)
            continue
        if parsed.kind == "scene":
            if state is not None:
                scenes.append(_close_scene(state, errors))
            sid = parsed.args["scene_id"]
            if sid in seen_ids:
                errors.append(ScriptError(lineno, f"scene {sid} appears again (first at line {seen_ids[sid]})"))
            else:
                seen_ids[sid] = lineno
            state = _SceneState(scene_id=sid, lineno=lineno)
            continue
        if state is None:
            preamble.append(parsed)
            errors.append(ScriptError(lineno, "line before the first scene marker"))
            continue
        problem = _structure_error(state, parsed)
        if problem is not None:
            errors.append(ScriptError(lineno, problem))
        state.lines.append(parsed)

    if state is not None:
        scenes.append(_close_scene(state, errors))
    errors.sort(key=lambda e: e.lineno)
    return ParsedScript(preamble=tuple(preamble), scenes=tuple(scenes), errors=tuple(errors))


def body_text(parsed: ParsedScript) -> str:
    """Text of dialogue, inner voice and narration joined by newlines (for character counts)."""
    return "\n".join(
        ln.args["text"] for scene in parsed.scenes for ln in scene.lines if ln.kind in TEXT_KINDS
    )
