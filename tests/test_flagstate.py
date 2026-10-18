from pathlib import Path

import pytest

from novui.flagstate import (
    MAX_STATES,
    branch_conditions,
    chapter_scene_states,
    condition_holds,
    holds_count,
    load_flag_states,
    parse_condition,
    work_flag_states,
)
from novui.yamlio import write_yaml_atomic

E = frozenset()


def S(*flags: str) -> frozenset:
    return frozenset(flags)


def market_plan(chapter: str = "ch-001") -> dict:
    """docs/script-format.md §5：S1 の選択肢で S2（+market_together）か S3 へ進み、S4 で合流する。"""
    return {"type": "plan", "chapter_id": chapter, "scenes": [
        {"id": "S1", "choice": [{"text": "一緒に市場を回る", "goto": "S2", "flag": "market_together"},
                                {"text": "船を見に行く", "goto": "S3"}]},
        {"id": "S2", "next": "S4"},
        {"id": "S3"},
        {"id": "S4"},
    ]}


def test_conditions() -> None:
    assert parse_condition("f") == ("f", True)
    assert parse_condition("not f") == ("f", False)
    assert condition_holds(None, E)
    assert condition_holds("f", S("f")) and not condition_holds("f", E)
    assert condition_holds("not f", E) and not condition_holds("not f", S("f"))
    assert holds_count("f", [E, S("f"), S("f", "g")]) == (2, 3)


def test_chapter_scene_states() -> None:
    scenes, end = chapter_scene_states(market_plan(), frozenset({E}))
    assert scenes == {
        "S1": {E}, "S2": {S("market_together")}, "S3": {E}, "S4": {E, S("market_together")},
    }
    assert end == {E, S("market_together")}


def test_states_carry_over_chapters_and_skip_chapters_without_plan() -> None:
    ch2 = {"type": "plan", "chapter_id": "ch-003", "scenes": [
        {"id": "S1", "choice": [{"text": "a", "goto": "S2", "flag": "g"}, {"text": "b", "goto": "S2"}]},
        {"id": "S2"},
    ]}
    fs = work_flag_states(["ch-001", "ch-002", "ch-003"], {"ch-001": market_plan(), "ch-003": ch2})
    both = {E, S("market_together")}
    assert fs.states_at("ch-002") == both
    assert fs.states_at("ch-002", "S5") == both  # plan の無い章は、どの場面でも章の初めの状態
    assert fs.states_at("ch-003", "S1") == both
    assert fs.states_at("ch-003", "S2") == {E, S("g"), S("market_together"), S("market_together", "g")}
    assert fs.end_of("ch-001") == both
    assert fs.states_at("ch-003", "S9") is None
    assert fs.states_at("ch-999") is None
    assert fs.warnings == ()


def test_overflow_stops() -> None:
    # 1章で9個のフラグを独立に立てると 512 通り
    scenes = []
    for i in range(9):
        scenes.append({"id": f"S{i + 1}", "choice": [
            {"text": "a", "goto": f"S{i + 2}", "flag": f"f{i}"}, {"text": "b", "goto": f"S{i + 2}"}]})
    scenes.append({"id": "S10"})
    plan = {"type": "plan", "chapter_id": "ch-001", "scenes": scenes}
    fs = work_flag_states(["ch-001", "ch-002"], {"ch-001": plan})
    assert len(fs.end_of("ch-001")) == 512 > MAX_STATES
    assert fs.states_at("ch-002") is None
    assert "more than 256" in fs.warnings[0]


@pytest.mark.parametrize("plan,expected", [
    (market_plan(), {"S2": ["market_together"], "S3": ["not market_together"]}),
    # フラグの無い選択肢だけの分岐は、条件で区別できない
    ({"scenes": [{"id": "S1", "choice": [{"text": "a", "goto": "S2"}, {"text": "b", "goto": "S3"}]},
                 {"id": "S2", "next": "S4"}, {"id": "S3"}, {"id": "S4"}]},
     {"S2": [], "S3": []}),
    ({"scenes": [{"id": "S1"}, {"id": "S2"}]}, {}),
])
def test_branch_conditions(plan: dict, expected: dict) -> None:
    plan = {"type": "plan", "chapter_id": "ch-001", **plan}
    fs = work_flag_states(["ch-001"], {"ch-001": plan})
    assert branch_conditions(plan, fs, "ch-001") == expected


def test_load_flag_states_uses_given_plan(tmp_path: Path) -> None:
    (tmp_path / "chapters" / "ch-001").mkdir(parents=True)
    plan = {
        "type": "plan", "chapter_id": "ch-001", "target_chars": {"min": 100, "max": 200},
        "scenes": [
            {"id": "S1", "summary": "s", "characters": ["C001"], "foreshadowing": [], "target_chars": 100,
             "choice": [{"text": "a", "goto": "S2", "flag": "f"}, {"text": "b", "goto": "S2"}]},
            {"id": "S2", "summary": "s", "characters": ["C001"], "foreshadowing": [], "target_chars": 100},
        ],
        "connections": {"from_previous": "", "to_next": ""},
    }
    fs = load_flag_states(tmp_path, ["ch-001", "ch-002"])
    assert fs.states_at("ch-002") == {E}
    fs = load_flag_states(tmp_path, ["ch-001", "ch-002"], chapter_id="ch-001", plan=plan)
    assert fs.states_at("ch-002") == {E, S("f")}
    write_yaml_atomic(tmp_path / "chapters" / "ch-001" / "plan.yaml", {"type": "plan"})
    fs = load_flag_states(tmp_path, ["ch-001", "ch-002"])
    assert fs.states_at("ch-002") == {E}
    assert "ch-001 plan.yaml could not be read" in fs.warnings[0]
