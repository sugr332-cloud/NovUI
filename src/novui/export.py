"""Export a FINAL script chapter to the game engine's YAML (docs/script-format.md §6, phase2e-design §10).

The engine (cruway/novel_game) reads one YAML file per scene in the `dialogues` form and a `_meta.yaml` per
chapter. Keys used here are the ones `script_parser.dart` `_parseDialogues` / `parseChapterMeta` read, plus
the dialogue `condition` and `characters.update` that the engine still has to add (phase2e-design §10.4).
"""

from __future__ import annotations

from pathlib import Path
import re
from typing import Any, Mapping

from novui.chapters import ChapterError, read_chapter_meta
from novui.draftjob import read_plan
from novui.mechanical import load_mechanical_inputs
from novui.script import TEXT_KINDS, ScriptLine, parse_script
from novui.scriptcheck import check_script_flow, check_script_syntax, display_names
from novui.semantics import plan_scene_successors
from novui.states import ChapterState
from novui.textformat import read_text_format
from novui.yamlio import dumps_yaml

# エンジンの _parseDialogues が読むキー（condition は §10.4 でエンジンに足すもの）
DIALOGUE_KEYS: tuple[str, ...] = ("type", "speaker", "text", "emotion", "condition", "characters", "choices")
CHARACTER_CHANGE_KEYS: tuple[str, ...] = ("add", "remove", "update")
CHOICE_KEYS: tuple[str, ...] = ("text", "next_scene", "set_flag")


class ExportError(Exception):
    """The chapter cannot be exported (not a script work, not FINAL, broken script, missing engine ids...)."""


def _chapter_number(chapter_id: str) -> int:
    m = re.match(r"^ch-([0-9]+)$", chapter_id)
    if m is None:
        raise ExportError(f"invalid chapter id {chapter_id!r}")
    return int(m.group(1))


def default_engine_chapter(chapter_id: str) -> str:
    """ch-001 -> chapter_01 (at least two digits)."""
    return f"chapter_{_chapter_number(chapter_id):02d}"


def scene_engine_ids(plan: Mapping[str, Any]) -> dict[str, str]:
    """NovUI scene ID -> engine scene ID (plan's engine_id, else scene_00, scene_01... in plan order)."""
    return {
        s["id"]: s.get("engine_id") or f"scene_{i:02d}" for i, s in enumerate(plan["scenes"])
    }


class _SceneWriter:
    """Builds one engine scene from the lines of a NovUI scene."""

    def __init__(self, sid: str, characters: Mapping[str, Mapping[str, Any]], names: Mapping[str, str],
                 ids: Mapping[str, str]) -> None:
        self.sid = sid
        self.characters = characters
        self.names = names
        self.ids = ids
        self.background: str | None = None
        self.initial: list[dict[str, Any]] = []
        self.dialogues: list[dict[str, Any]] = []
        self.pending: dict[str, list[Any]] = {k: [] for k in CHARACTER_CHANGE_KEYS}
        self.pending_condition: str | None = None
        self.condition: str | None = None
        self.choices: list[dict[str, Any]] | None = None
        self.goto: str | None = None

    def _engine_id(self, cid: str, lineno: int) -> str:
        doc = self.characters.get(cid)
        if doc is None or not doc.get("engine_id"):
            raise ExportError(f"line {lineno}: {cid} has no engine_id")
        return doc["engine_id"]

    def _default_emotion(self, cid: str) -> str | None:
        exprs = (self.characters.get(cid) or {}).get("expressions")
        return exprs[0] if exprs else None

    def _queue(self, key: str, value: Any, lineno: int) -> None:
        if any(self.pending.values()) and self.pending_condition != self.condition:
            raise ExportError(f"line {lineno}: stage changes in and out of @if before the same line")
        self.pending_condition = self.condition
        self.pending[key].append(value)

    def add(self, ln: ScriptLine) -> None:
        kind, a = ln.kind, ln.args
        if kind == "bg":
            self.background = a["name"]
        elif kind == "enter":
            entry: dict[str, Any] = {"id": self._engine_id(a["character_id"], ln.lineno), "position": a["position"]}
            emotion = a["expression"] or self._default_emotion(a["character_id"])
            if emotion is not None:
                entry["emotion"] = emotion
            if not self.dialogues and self.condition is None and not any(self.pending.values()):
                self.initial.append(entry)
            else:
                self._queue("add", entry, ln.lineno)
        elif kind == "exit":
            self._queue("remove", self._engine_id(a["character_id"], ln.lineno), ln.lineno)
        elif kind == "face":
            self._queue("update", {"id": self._engine_id(a["character_id"], ln.lineno), "emotion": a["expression"]},
                        ln.lineno)
        elif kind == "if":
            self.condition = ("!" if a["negated"] else "") + a["flag"]
        elif kind == "else":
            assert self.condition is not None
            self.condition = self.condition[1:] if self.condition.startswith("!") else "!" + self.condition
        elif kind == "endif":
            self.condition = None
        elif kind == "choice":
            self.choices = []
        elif kind == "option":
            assert self.choices is not None
            choice: dict[str, Any] = {"text": a["text"], "next_scene": self.ids[a["goto"]]}
            if a["flag"]:
                choice["set_flag"] = a["flag"]
            self.choices.append(choice)
        elif kind == "goto":
            self.goto = a["scene_id"]
        elif kind in TEXT_KINDS:
            self._text(ln)

    def _text(self, ln: ScriptLine) -> None:
        a = ln.args
        entry: dict[str, Any] = {}
        if ln.kind == "narration":
            entry["type"] = "narration"
        else:
            if ln.kind == "think":
                entry["type"] = "thought"
            cid = self.names.get(a["speaker"])
            entry["speaker"] = self._engine_id(cid, ln.lineno) if cid is not None else a["speaker"]
        entry["text"] = a["text"]
        if ln.kind != "narration" and a.get("expression"):
            entry["emotion"] = a["expression"]
        if self.condition is not None:
            entry["condition"] = self.condition
        if any(self.pending.values()):
            if self.pending_condition != self.condition:
                raise ExportError(f"line {ln.lineno}: stage changes in and out of @if before the same line")
            entry["characters"] = {k: v for k, v in self.pending.items() if v}
            self.pending = {k: [] for k in CHARACTER_CHANGE_KEYS}
            self.pending_condition = None
        self.dialogues.append(entry)

    def finish(self) -> None:
        if any(self.pending.values()):
            raise ExportError(f"scene {self.sid}: stage changes after the last line cannot be exported")
        if self.choices is not None:
            if not self.dialogues:
                raise ExportError(f"scene {self.sid}: @choice needs a line before it")
            self.dialogues[-1]["choices"] = self.choices


def export_chapter(
    root: Path, chapter_id: str, *, route: str = "common", engine_chapter: str | None = None
) -> dict[str, Any]:
    """Engine files of a FINAL chapter as {relative path: data}. Raises ExportError when it cannot be exported."""
    try:
        if read_text_format(root) != "script":
            raise ExportError("the work is not a script work (project.yaml text_format)")
        meta = read_chapter_meta(root, chapter_id)
        if meta is None or meta["state"] != ChapterState.FINAL.name:
            raise ExportError(f"chapter {chapter_id} must be FINAL to export, got {meta['state'] if meta else 'NONE'}")
        plan = read_plan(root, chapter_id)
    except ChapterError as exc:
        raise ExportError(str(exc)) from exc
    draft_path = root / "chapters" / chapter_id / "draft.md"
    if not draft_path.is_file():
        raise ExportError(f"draft.md not found for {chapter_id}")
    parsed = parse_script(draft_path.read_text(encoding="utf-8"))
    problems = [c for c in (check_script_syntax(parsed), check_script_flow(parsed, plan)) if c.status != "PASS"]
    if problems:
        raise ExportError("; ".join(f"{c.name}: {list(c.details)}" for c in problems))

    characters = load_mechanical_inputs(root).characters
    names, dup = display_names(characters)
    if dup:
        raise ExportError("; ".join(dup))
    ids = scene_engine_ids(plan)
    if len(set(ids.values())) != len(ids):
        raise ExportError(f"scene engine ids are not unique: {sorted(ids.values())}")
    chapter_dir = f"{route}/{engine_chapter or default_engine_chapter(chapter_id)}"
    succ = plan_scene_successors(list(plan["scenes"]))
    plan_scenes = {s["id"]: s for s in plan["scenes"]}

    files: dict[str, Any] = {
        f"{chapter_dir}/_meta.yaml": {"chapter": {
            "id": chapter_dir.split("/", 1)[1],
            "number": _chapter_number(chapter_id),
            "title": meta["title"],
            "route": route,
            "scenes": [ids[s["id"]] for s in plan["scenes"]],
        }},
    }
    errors: list[str] = []
    for block in parsed.scenes:
        writer = _SceneWriter(block.scene_id, characters, names, ids)
        try:
            for ln in block.lines:
                writer.add(ln)
            writer.finish()
        except ExportError as exc:
            errors.append(str(exc))
            continue
        ps = plan_scenes[block.scene_id]
        scene: dict[str, Any] = {"id": ids[block.scene_id], "title": ps.get("title") or ps["summary"], "type": "dialogue"}
        if writer.background is not None:
            scene["background"] = writer.background
        if writer.initial:
            scene["characters"] = {"initial": writer.initial}
        if writer.choices is not None:
            next_scene = None
        elif writer.goto is not None:
            next_scene = ids[writer.goto]
        else:
            nxt = succ[block.scene_id]
            next_scene = ids[nxt[0]] if nxt else None
        files[f"{chapter_dir}/{ids[block.scene_id]}.yaml"] = {
            "scene": scene, "dialogues": writer.dialogues, "next_scene": next_scene,
        }
    if errors:
        raise ExportError("; ".join(errors))
    return files


def write_export(files: Mapping[str, Any], out_dir: Path) -> list[Path]:
    """Write the files under out_dir (existing files are overwritten). Returns the written paths."""
    written: list[Path] = []
    for rel, data in files.items():
        path = out_dir / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(dumps_yaml(data), encoding="utf-8")
        written.append(path)
    return written
