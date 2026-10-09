from pathlib import Path

import pytest

from novui.script import ParsedScript, ScriptError, ScriptLine, body_text, parse_line, parse_script

SPEC = Path(__file__).resolve().parents[1] / "docs" / "script-format.md"


def spec_example() -> str:
    text = SPEC.read_text(encoding="utf-8")
    return text.split("## 5. 例", 1)[1].split("```text\n", 1)[1].split("```", 1)[0]


def L(raw: str) -> ScriptLine:
    out = parse_line(raw, 1)
    assert isinstance(out, ScriptLine), out
    return out


# --- parse_line：各行の種類


@pytest.mark.parametrize(
    "raw,kind,args",
    [
        ("<!-- scene: S12 -->", "scene", {"scene_id": "S12"}),
        ("@bg 市場_昼", "bg", {"name": "市場_昼"}),
        ("@enter C001 left 通常", "enter", {"character_id": "C001", "position": "left", "expression": "通常"}),
        ("@enter C001 right", "enter", {"character_id": "C001", "position": "right", "expression": None}),
        ("@exit C002", "exit", {"character_id": "C002"}),
        ("@face C002 怒り", "face", {"character_id": "C002", "expression": "怒り"}),
        ("@choice", "choice", {}),
        ("- 一緒に市場を回る -> S2 +market_together", "option",
         {"text": "一緒に市場を回る", "goto": "S2", "flag": "market_together"}),
        ("- 船を見に行く -> S3", "option", {"text": "船を見に行く", "goto": "S3", "flag": None}),
        ("@goto S4", "goto", {"scene_id": "S4"}),
        ("@if market_together", "if", {"flag": "market_together", "negated": False}),
        ("@if not met_rou", "if", {"flag": "met_rou", "negated": True}),
        ("@else", "else", {}),
        ("@endif", "endif", {}),
        ("カイ［驚き］「美咲、何してるんだ」", "say", {"speaker": "カイ", "expression": "驚き", "text": "美咲、何してるんだ"}),
        ("美咲「私は『魚』を買いに来たの」", "say", {"speaker": "美咲", "expression": None, "text": "私は『魚』を買いに来たの"}),
        ("カイ（言えるわけがない）", "think", {"speaker": "カイ", "expression": None, "text": "言えるわけがない"}),
        ("カイ［困惑］（どうしよう）", "think", {"speaker": "カイ", "expression": "困惑", "text": "どうしよう"}),
        ("カイは港の市場で美咲を見つけた。", "narration", {"text": "カイは港の市場で美咲を見つけた。"}),
        ("  前後の空白は除く。  ", "narration", {"text": "前後の空白は除く。"}),
    ],
)
def test_parse_line_kinds(raw: str, kind: str, args: dict) -> None:
    ln = L(raw)
    assert ln.kind == kind
    assert dict(ln.args) == args
    assert ln.raw == raw


def test_speaker_rule_examples() -> None:
    # script-format §2：地の文が 」 で終わると台詞として読まれる
    assert L("看板には「準備中」").kind == "say"
    assert L("看板には「準備中」").args["speaker"] == "看板には"
    assert L("看板には「準備中」とあった。").kind == "narration"
    # 表示名の頭に 「 は来ない
    assert L("「やあ」とカイは言った。").kind == "narration"
    assert L("「やあ」").kind == "narration"
    # 表示名に空白は使えない
    assert L("カイ と美咲「行こう」").kind == "narration"


@pytest.mark.parametrize(
    "raw",
    [
        "@bg",
        "@bg 市場 昼",
        "@enter C001 top",
        "@enter X001 left",
        "@exit",
        "@face C001",
        "@choice now",
        "@goto 4",
        "@if Market",
        "@if not",
        "@wait 3",
        "<!-- scene: X1 -->",
        "<!-- comment -->",
        "- 選択肢だけ",
        "- 行き先が不正 -> X2",
        "- フラグが不正 -> S2 +Bad",
    ],
)
def test_parse_line_errors(raw: str) -> None:
    out = parse_line(raw, 7)
    assert isinstance(out, ScriptError)
    assert out.lineno == 7


def test_halfwidth_brackets_are_narration() -> None:
    assert L("カイ[驚き]「やあ」").kind == "say"  # 半角の [] は表示名の一部として読まれる
    assert L("カイ[驚き]「やあ」").args["speaker"] == "カイ[驚き]"
    assert L('カイ"やあ"').kind == "narration"


# --- parse_script


def test_spec_example() -> None:
    p = parse_script(spec_example())
    assert p.errors == ()
    assert p.preamble == ()
    assert [s.scene_id for s in p.scenes] == ["S1", "S2", "S3", "S4"]
    assert [ln.kind for ln in p.scenes[0].lines] == [
        "bg", "enter", "enter", "narration", "say", "say", "think", "choice", "option", "option",
    ]
    assert [ln.kind for ln in p.scenes[3].lines] == [
        "bg", "enter", "enter", "if", "say", "else", "say", "endif", "say",
    ]


def test_linenos_are_file_lines_and_blank_lines_ignored() -> None:
    p = parse_script("<!-- scene: S1 -->\n\n@bg a\n\nカイ「やあ」\n")
    assert p.scenes[0].lineno == 1
    assert [(ln.lineno, ln.kind) for ln in p.scenes[0].lines] == [(3, "bg"), (5, "say")]


def _errors(text: str) -> list[tuple[int, str]]:
    return [(e.lineno, e.message) for e in parse_script(text).errors]


def test_line_before_first_scene() -> None:
    p = parse_script("まえがき\n<!-- scene: S1 -->\n本文。\n")
    assert len(p.preamble) == 1
    assert _errors("まえがき\n<!-- scene: S1 -->\n本文。\n") == [(1, "line before the first scene marker")]


def test_duplicate_scene() -> None:
    errs = _errors("<!-- scene: S1 -->\na。\n<!-- scene: S1 -->\nb。\n")
    assert errs == [(3, "scene S1 appears again (first at line 1)")]


def test_choice_rules() -> None:
    assert _errors("<!-- scene: S1 -->\n@choice\n- a -> S2\n") == [(2, "@choice needs at least two options")]
    assert _errors("<!-- scene: S1 -->\n- a -> S2\n") == [(2, "choice option without @choice")]
    errs = _errors("<!-- scene: S1 -->\n@choice\n- a -> S2\n- b -> S3\nまだ続く。\n")
    assert errs == [(5, "no line may follow the options of @choice in the scene")]
    # 次の場面の区切りで閉じるのは正しい
    assert _errors("<!-- scene: S1 -->\n@choice\n- a -> S2\n- b -> S3\n<!-- scene: S2 -->\nx。\n") == []


def test_goto_must_be_last() -> None:
    assert _errors("<!-- scene: S1 -->\n@goto S3\nまだ続く。\n") == [(3, "no line may follow @goto in the scene")]


def test_if_rules() -> None:
    ok = "<!-- scene: S1 -->\n@if f\nカイ「a」\n@face C001 笑顔\n@else\n地の文。\n@endif\n"
    assert _errors(ok) == []
    assert _errors("<!-- scene: S1 -->\n@if f\n@if g\n@endif\n") == [(3, "@if must not be nested")]
    assert _errors("<!-- scene: S1 -->\n@else\n") == [(2, "@else without @if")]
    assert _errors("<!-- scene: S1 -->\n@endif\n") == [(2, "@endif without @if")]
    assert _errors("<!-- scene: S1 -->\n@if f\n@else\n@else\n@endif\n") == [(4, "second @else in the same @if")]
    assert _errors("<!-- scene: S1 -->\n@if f\na。\n<!-- scene: S2 -->\n") == [(2, "@if is not closed by @endif in the scene")]
    errs = _errors("<!-- scene: S1 -->\n@if f\n@enter C001 left\n@bg x\n@endif\n")
    assert errs == [(3, "@enter is not allowed inside @if"), (4, "@bg is not allowed inside @if")]
    assert _errors("<!-- scene: S1 -->\n@if f\n@goto S2\n@endif\n") == [(3, "@goto is not allowed inside @if")]


def test_errors_do_not_stop_parsing() -> None:
    p = parse_script("<!-- scene: S1 -->\n@enter C001 top\nカイ「a」\n<!-- scene: S2 -->\nb。\n")
    assert [e.lineno for e in p.errors] == [2]
    assert [s.scene_id for s in p.scenes] == ["S1", "S2"]
    assert [ln.kind for ln in p.scenes[0].lines] == ["say"]


def test_empty_text() -> None:
    assert parse_script("") == ParsedScript(preamble=(), scenes=(), errors=())


def test_body_text() -> None:
    p = parse_script("<!-- scene: S1 -->\n@bg 市場\n@enter C001 left 通常\nカイ［驚き］「やあ」\nカイ（ふむ）\n地の文。\n@choice\n- a -> S2\n- b -> S2\n")
    assert body_text(p) == "やあ\nふむ\n地の文。"
