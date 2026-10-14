"""Flag states of each scene (phase2f-design §2).

A state is the set of flags that are set. Flags are set only by choices and are never cleared, so the states
that can reach each scene follow from the choice/next of every plan in chapters-order.yaml. All functions
except load_plans are pure.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from novui.schema import SchemaError, validate_or_raise
from novui.semantics import check_plan, plan_scene_successors
from novui.yamlio import YamlError, load_yaml

MAX_STATES = 256

State = frozenset[str]
States = frozenset[State]

START_STATES: States = frozenset({frozenset()})  # どのフラグも立っていない


@dataclass(frozen=True)
class FlagStates:
    at_start: Mapping[str, States]  # 章 → 最初の場面に入る時点の状態
    at_scene: Mapping[str, Mapping[str, States]]  # 章 → 場面 → 入る時点の状態（plan のある章だけ）
    at_end: Mapping[str, States]  # 章 → 章の終わりの状態
    warnings: tuple[str, ...]

    def states_at(self, chapter_id: str, scene_id: str | None = None) -> States | None:
        """States on entering the scene; the chapter's start states for a chapter without a plan or when
        scene_id is None. None when the chapter was not computed (not in the order, or after an overflow)
        or the scene is not in the chapter's plan."""
        if chapter_id not in self.at_start:
            return None
        scenes = self.at_scene.get(chapter_id)
        if scenes is None or scene_id is None:
            return self.at_start[chapter_id]
        return scenes.get(scene_id)

    def end_of(self, chapter_id: str) -> States | None:
        return self.at_end.get(chapter_id)


def parse_condition(cond: str) -> tuple[str, bool]:
    """'f' -> ('f', True), 'not f' -> ('f', False)."""
    if cond.startswith("not "):
        return cond[4:], False
    return cond, True


def condition_holds(cond: str | None, state: State) -> bool:
    """True when cond is None or holds in state."""
    if cond is None:
        return True
    flag, wanted = parse_condition(cond)
    return (flag in state) == wanted


def holds_count(cond: str | None, states: Iterable[State]) -> tuple[int, int]:
    """(number of states where cond holds, number of states)."""
    states = list(states)
    return sum(1 for s in states if condition_holds(cond, s)), len(states)


def chapter_scene_states(plan: Mapping[str, Any], start: States) -> tuple[dict[str, States], States]:
    """States on entering each scene of one chapter, and the states at its end, from the start states."""
    scenes = list(plan.get("scenes", []))
    succ = plan_scene_successors(scenes)
    entry: dict[str, set[State]] = {s["id"]: set() for s in scenes}
    if scenes:
        entry[scenes[0]["id"]] |= set(start)
    end: set[State] = set()
    for s in scenes:
        sid = s["id"]
        here = entry[sid]
        if s.get("choice"):
            for c in s["choice"]:
                add = {c["flag"]} if c.get("flag") else set()
                target = entry.get(c.get("goto"))
                if target is not None:
                    target |= {st | add for st in here}
        elif succ[sid]:
            target = entry.get(succ[sid][0])
            if target is not None:
                target |= here
        else:
            end |= here
    return {sid: frozenset(v) for sid, v in entry.items()}, frozenset(end)


def work_flag_states(order: Sequence[str], plans: Mapping[str, Mapping[str, Any]]) -> FlagStates:
    """Flag states of every chapter in order. A chapter without a plan does not change the states.
    After the number of states exceeds MAX_STATES, later chapters are not computed."""
    at_start: dict[str, States] = {}
    at_scene: dict[str, dict[str, States]] = {}
    at_end: dict[str, States] = {}
    warnings: list[str] = []
    current = START_STATES
    for ch in order:
        at_start[ch] = current
        plan = plans.get(ch)
        if plan is None:
            at_end[ch] = current
            continue
        scenes, end = chapter_scene_states(plan, current)
        at_scene[ch] = scenes
        at_end[ch] = end
        current = end if end else current
        if len(current) > MAX_STATES:
            warnings.append(
                f"more than {MAX_STATES} flag states after {ch}; flag conditions are not computed for later chapters"
            )
            break
    return FlagStates(at_start=at_start, at_scene=at_scene, at_end=at_end, warnings=tuple(warnings))


def branch_scenes(plan: Mapping[str, Any]) -> frozenset[str]:
    """Scenes that not every path from the first scene to the end of the chapter passes (phase2e-design §5.3)."""
    scenes = list(plan.get("scenes", []))
    if not scenes:
        return frozenset()
    succ = plan_scene_successors(scenes)
    first = scenes[0]["id"]

    def reaches_end_without(skip: str) -> bool:
        stack, seen = [first], {first}
        while stack:
            cur = stack.pop()
            if not succ[cur]:
                return True
            for nxt in succ[cur]:
                if nxt != skip and nxt not in seen:
                    seen.add(nxt)
                    stack.append(nxt)
        return False

    return frozenset(s["id"] for s in scenes[1:] if reaches_end_without(s["id"]))


def chapter_flags(plan: Mapping[str, Any]) -> list[str]:
    """Flags set by the choices of the plan, in order of appearance."""
    flags: dict[str, None] = {}
    for s in plan.get("scenes", []):
        for c in s.get("choice") or []:
            if c.get("flag"):
                flags[c["flag"]] = None
    return list(flags)


def branch_conditions(plan: Mapping[str, Any], states: FlagStates, chapter_id: str) -> dict[str, list[str]]:
    """For each branch scene of the chapter, the conditions on this chapter's flags that hold on every path
    into it (e.g. {'S2': ['market_together'], 'S3': ['not market_together']}). [] when there is none."""
    result: dict[str, list[str]] = {}
    flags = chapter_flags(plan)
    for sid in sorted(branch_scenes(plan), key=lambda s: [s2["id"] for s2 in plan["scenes"]].index(s)):
        here = states.states_at(chapter_id, sid)
        conds: list[str] = []
        if here:
            for f in flags:
                if all(f in st for st in here):
                    conds.append(f)
                elif all(f not in st for st in here):
                    conds.append(f"not {f}")
        result[sid] = conds
    return result


def load_plans(root: Path, order: Sequence[str]) -> tuple[dict[str, Mapping[str, Any]], list[str]]:
    """plan.yaml of every chapter in order that has one; plans that cannot be read are skipped with a warning."""
    plans: dict[str, Mapping[str, Any]] = {}
    warnings: list[str] = []
    for ch in order:
        path = root / "chapters" / ch / "plan.yaml"
        if not path.is_file():
            continue
        try:
            doc = load_yaml(path)
            validate_or_raise(doc, "plan")
        except (YamlError, SchemaError, OSError) as exc:
            warnings.append(f"{ch} plan.yaml could not be read for flag states: {exc}")
            continue
        if check_plan(doc):
            warnings.append(f"{ch} plan.yaml has semantic errors; ignored for flag states")
            continue
        plans[ch] = doc
    return plans, warnings


def load_flag_states(
    root: Path, order: Sequence[str], *, chapter_id: str | None = None, plan: Mapping[str, Any] | None = None
) -> FlagStates:
    """work_flag_states from the plans under root. When chapter_id and plan are given, that plan is used for
    the chapter instead of the file (e.g. a plan being validated on the job branch)."""
    plans, warnings = load_plans(root, order)
    if chapter_id is not None and plan is not None:
        plans[chapter_id] = plan
    fs = work_flag_states(order, plans)
    return FlagStates(fs.at_start, fs.at_scene, fs.at_end, tuple(warnings) + fs.warnings)

