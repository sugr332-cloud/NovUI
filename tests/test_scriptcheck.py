from pathlib import Path
from typing import Any

import pytest

from novui.script import parse_script
from novui.scriptcheck import (
    ScriptInputs,
    check_script_backgrounds,
    check_script_expressions,
    check_script_flow,
    check_script_line_length,
    check_script_speaker_words,
    check_script_speakers,
    check_script_stage,
    check_script_suspicious_lines,
    check_script_syntax,
    display_names,
    load_script_inputs,
    run_script_checks,
    script_char_count,
)
from novui.yamlio import dumps_yaml
from test_script import spec_example

C001 = {"id": "C001", "name": "カイ", "display_name": "カイ", "engine_id": "kai",
        "expressions": ["neutral", "happy", "surprised"], "speech": {"first_person": "俺", "forbidden": ["僕"]}}
C002 = {"id": "C002", "name": "美咲", "display_name": "美咲", "expressions": ["neutral", "happy", "angry"]}
CHARS = {"C001": C001, "C002": C002}
BGS = frozenset({"bg_market_day", "bg_harbor_day", "bg_gate_evening"})


def inputs(**over: Any) -> ScriptInputs:
    base: dict[str, Any] = dict(characters=CHARS, backgrounds=BGS, flags=frozenset({"market_together"}),
                                max_line_chars=None, load_errors=())
    base.update(over)
    return ScriptInputs(**base)


def spec_plan() -> dict:
    """The plan that matches docs/script-format.md §5."""
    return {"scenes": [
        {"id": "S1", "characters": ["C001", "C002"], "background": "bg_market_day",
         "choice": [{"text": "一緒に市場を回る", "goto": "S2", "flag": "market_together"},
                    {"text": "船を見に行く", "goto": "S3"}]},
        {"id": "S2", "characters": ["C001", "C002"], "next": "S4"},
        {"id": "S3", "characters": ["C001"]},
        {"id": "S4", "characters": ["C001", "C002"]},
    ]}


def P(text: str):
    return parse_script(text)


def details(c) -> list[str]:
    return list(c.details)


# --- 全体：§5 の例はすべて PASS


def test_spec_example_all_pass() -> None:
    results = run_script_checks(spec_example(), spec_plan(), inputs(), for_draft=False)
    assert [(c.name, c.status) for c in results] == [
        ("script_syntax", "PASS"), ("script_flow", "PASS"), ("script_speakers", "PASS"),
        ("script_expressions", "PASS"), ("script_stage", "PASS"), ("script_backgrounds", "PASS"),
        ("script_speaker_words", "PASS"), ("script_line_length", "PASS"), ("script_suspicious_lines", "PASS"),
    ], [c for c in results if c.status != "PASS"]


def test_for_draft_turns_syntax_and_flow_into_fail_only() -> None:
    text = spec_example().replace("@enter C001 left neutral", "@enter C001 top neutral", 1) + "\nカイ「僕だ」\n"
    draft = {c.name: c.status for c in run_script_checks(text, spec_plan(), inputs(), for_draft=True)}
    valid = {c.name: c.status for c in run_script_checks(text, spec_plan(), inputs(), for_draft=False)}
    assert draft["script_syntax"] == "FAIL" and valid["script_syntax"] == "WARNING"
    assert draft["script_speaker_words"] == "WARNING" == valid["script_speaker_words"]


def test_load_errors_first() -> None:
    results = run_script_checks(spec_example(), spec_plan(), inputs(load_errors=("x: bad",)), for_draft=False)
    assert (results[0].name, results[0].status, results[0].details) == ("script_inputs", "WARNING", ("x: bad",))


# --- 個別の検査


def test_syntax() -> None:
    c = check_script_syntax(P("<!-- scene: S1 -->\n@enter C001 top\n"))
    assert c.status == "WARNING" and details(c) == ["line 2: unknown or malformed command '@enter'"]


def test_flow_scenes() -> None:
    plan = {"scenes": [{"id": "S1"}, {"id": "S2"}, {"id": "S3"}]}
    c = check_script_flow(P("<!-- scene: S2 -->\na。\n<!-- scene: S1 -->\nb。\n<!-- scene: S9 -->\nc。\n"), plan)
    assert details(c) == ["scene S9 is not in plan", "missing scene S3", "scene order mismatch: expected S1, S2; got S2, S1"]


def test_flow_endings() -> None:
    text = spec_example()
    plan = spec_plan()
    plan["scenes"][0]["choice"][0]["flag"] = None
    plan["scenes"][0]["choice"][0].pop("flag")
    c = check_script_flow(P(text), plan)
    assert c.status == "WARNING" and "scene S1: choice options" in details(c)[0]

    plan = spec_plan()
    plan["scenes"][1]["next"] = "S3"
    assert details(check_script_flow(P(text), plan)) == ["scene S2: expected @goto S3, got ['S4']"]

    plan = spec_plan()
    del plan["scenes"][1]["next"]
    assert details(check_script_flow(P(text), plan)) == ["scene S2: @goto S4 is not in plan"]

    plan = spec_plan()
    del plan["scenes"][0]["choice"]
    assert details(check_script_flow(P(text), plan)) == ["scene S1: @choice is not in plan"]

    plan = spec_plan()
    plan["scenes"][3]["choice"] = [{"text": "a", "goto": "S4"}, {"text": "b", "goto": "S4"}]
    assert details(check_script_flow(P(text), plan)) == ["scene S4: @choice is missing"]


def test_speakers() -> None:
    plan = {"scenes": [{"id": "S1", "characters": ["C001"], "extras": ["門番", "美咲"]}]}
    text = "<!-- scene: S1 -->\nカイ「a」\n門番「b」\n店主「c」\n"
    assert details(check_script_speakers(P(text), plan, inputs())) == [
        "scene S1: extra '美咲' is the display_name of C002",
        "line 4: speaker '店主' is not a character or extra of scene S1",
    ]


def test_speakers_missing_display_name_and_duplicates() -> None:
    chars = {"C001": C001, "C002": {"id": "C002", "name": "x"}, "C003": {"id": "C003", "name": "y", "display_name": "カイ"}}
    plan = {"scenes": [{"id": "S1", "characters": ["C001", "C002"]}]}
    d = details(check_script_speakers(P("<!-- scene: S1 -->\nカイ「a」\n"), plan, inputs(characters=chars)))
    assert d == ["display_name 'カイ' is used by C001 and C003", "scene S1: C002 has no display_name"]


def test_display_names() -> None:
    names, warnings = display_names(CHARS)
    assert names == {"カイ": "C001", "美咲": "C002"} and warnings == []


def test_expressions() -> None:
    chars = {**CHARS, "C003": {"id": "C003", "name": "z", "display_name": "ロウ"}}
    text = ("<!-- scene: S1 -->\n@enter C001 left smirk\n@face C002 sad\nカイ［happy］「a」\n美咲［cry］「b」\n"
            "ロウ［any］「c」\n門番［angry］「d」\n")
    assert details(check_script_expressions(P(text), inputs(characters=chars))) == [
        "line 2: C001 has no expression 'smirk'",
        "line 3: C002 has no expression 'sad'",
        "line 5: C002 has no expression 'cry'",
        "line 7: '門番' has no expressions (extra)",
    ]


def test_stage() -> None:
    plan = {"scenes": [{"id": "S1", "characters": ["C001", "C002"]}, {"id": "S2", "characters": ["C001"]}]}
    text = (
        "<!-- scene: S1 -->\n@bg bg_market_day\n@enter C001 left\n@enter C002 left\n@enter C001 right\n"
        "@exit C002\n@exit C002\n美咲「外から」\n@face C002 happy\n@enter C003 center\n"
        "<!-- scene: S2 -->\nカイ「前の場面の立ち絵は残らない」\n"
    )
    assert details(check_script_stage(P(text), plan, inputs())) == [
        "line 4: C002 enters at left where C001 already is",
        "line 5: C001 enters but is already on stage",
        "line 7: C002 exits but is not on stage",
        "line 8: C002 speaks off-screen (not on stage)",
        "line 9: @face C002 who is not on stage",
        "line 10: C003 is not a character of scene S1 in plan",
        "scene S2 has no @bg",
        "line 12: C001 speaks off-screen (not on stage)",
    ]


def test_backgrounds() -> None:
    plan = {"scenes": [{"id": "S1", "background": "bg_market_day"}, {"id": "S2", "background": "bg_gate_evening"}]}
    text = "<!-- scene: S1 -->\n@bg bg_unknown\n<!-- scene: S2 -->\na。\n"
    assert details(check_script_backgrounds(P(text), plan, inputs())) == [
        "line 2: background 'bg_unknown' is not in assets/backgrounds.yaml",
        "scene S1: background 'bg_unknown' does not match plan 'bg_market_day'",
        "scene S2: background 'none' does not match plan 'bg_gate_evening'",
    ]
    assert check_script_backgrounds(P(text), plan, inputs(backgrounds=None)).status == "PASS"


def test_speaker_words() -> None:
    text = "<!-- scene: S1 -->\nカイ「僕は」\nカイ（僕なんて）\n美咲「僕？」\n"
    assert details(check_script_speaker_words(P(text), inputs())) == ["line 2: C001 says forbidden word '僕'"]


def test_line_length() -> None:
    text = "<!-- scene: S1 -->\nカイ「あいうえお」\nかきくけこさ。\n@bg x\n"
    assert check_script_line_length(P(text), inputs()).status == "PASS"
    assert details(check_script_line_length(P(text), inputs(max_line_chars=5))) == ["line 3: 7 chars > 5"]


def test_suspicious_lines() -> None:
    text = "<!-- scene: S1 -->\n「やあ」\nカイ 「やあ」\n看板には「準備中」とあった。\nずっと昔、彼は言った「やあ」\n"
    d = details(check_script_suspicious_lines(P(text)))
    assert [x.split(":")[0] for x in d] == ["line 2", "line 3"]


def test_script_char_count() -> None:
    text = "<!-- scene: S1 -->\n@bg bg_market_day\n@enter C001 left happy\nカイ［happy］「あいう」\nえお。\n"
    c = script_char_count(text, 1, 100)
    assert c.name == "char_count" and c.details == ("count=6 range=1-100",)


def test_load_script_inputs(tmp_path: Path) -> None:
    assert load_script_inputs(tmp_path, CHARS) == ScriptInputs(CHARS, None, None, None, ())
    (tmp_path / "assets").mkdir()
    (tmp_path / "flags").mkdir()
    (tmp_path / "rules").mkdir()
    (tmp_path / "assets" / "backgrounds.yaml").write_text(dumps_yaml(["bg_a"]), encoding="utf-8")
    (tmp_path / "flags" / "registry.yaml").write_text(
        dumps_yaml([{"name": "f", "description": "d", "notes": ""}]), encoding="utf-8")
    (tmp_path / "rules" / "script.yaml").write_text(dumps_yaml({"max_line_chars": 40}), encoding="utf-8")
    got = load_script_inputs(tmp_path, CHARS)
    assert (got.backgrounds, got.flags, got.max_line_chars, got.load_errors) == (frozenset({"bg_a"}), frozenset({"f"}), 40, ())
    (tmp_path / "flags" / "registry.yaml").write_text(
        dumps_yaml([{"name": "f", "description": "d", "notes": ""}] * 2), encoding="utf-8")
    bad = load_script_inputs(tmp_path, CHARS)
    assert bad.flags is None and len(bad.load_errors) == 1 and bad.load_errors[0].startswith("flags/registry.yaml:")
