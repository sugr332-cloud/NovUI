"""2-E5: export to the game engine's YAML (docs/script-format.md §6)."""

import copy
import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_script import spec_example  # noqa: E402

from novui import cli  # noqa: E402
from novui.export import (  # noqa: E402
    CHARACTER_CHANGE_KEYS,
    CHOICE_KEYS,
    DIALOGUE_KEYS,
    ExportError,
    default_engine_chapter,
    export_chapter,
    scene_engine_ids,
    write_export,
)
from novui.yamlio import dumps_yaml, load_yaml, loads_yaml  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures" / "valid"
C001 = {"id": "C001", "name": "カイ", "display_name": "カイ", "engine_id": "kai",
        "expressions": ["neutral", "happy", "surprised"]}
C002 = {"id": "C002", "name": "美咲", "display_name": "美咲", "engine_id": "misaki",
        "expressions": ["neutral", "happy", "angry"]}


def spec_plan() -> dict[str, Any]:
    plan = copy.deepcopy(load_yaml(FIXTURES / "plan.yaml"))
    base = plan["scenes"][0]
    plan["chapter_id"] = "ch-001"
    plan["scenes"] = [
        {**copy.deepcopy(base), "id": "S1", "characters": ["C001", "C002"], "engine_id": "scene_00_market",
         "title": "市場で", "choice": [{"text": "一緒に市場を回る", "goto": "S2", "flag": "market_together"},
                                       {"text": "船を見に行く", "goto": "S3"}]},
        {**copy.deepcopy(base), "id": "S2", "characters": ["C001", "C002"], "next": "S4", "summary": "市場を歩く"},
        {**copy.deepcopy(base), "id": "S3", "characters": ["C001"], "summary": "船を見る"},
        {**copy.deepcopy(base), "id": "S4", "characters": ["C001", "C002"], "summary": "門で別れる"},
    ]
    return plan


def make_work(root: Path, *, text: str | None = None, state: str = "FINAL", text_format: str = "script",
              chars: tuple[dict, ...] = (C001, C002), plan: dict | None = None) -> Path:
    (root / "characters").mkdir(parents=True)
    (root / "chapters" / "ch-001").mkdir(parents=True)
    (root / "project.yaml").write_text(
        dumps_yaml({"format_version": 1, "work_key": "w", "title": "t", "text_format": text_format}), encoding="utf-8")
    for c in chars:
        (root / "characters" / f"{c['id']}.yaml").write_text(dumps_yaml(c), encoding="utf-8")
    meta = load_yaml(FIXTURES / "chapter.yaml")
    meta.update({"id": "ch-001", "title": "港町", "state": state})
    (root / "chapters" / "ch-001" / "chapter.yaml").write_text(dumps_yaml(meta), encoding="utf-8")
    (root / "chapters" / "ch-001" / "plan.yaml").write_text(dumps_yaml(plan or spec_plan()), encoding="utf-8")
    (root / "chapters" / "ch-001" / "draft.md").write_text(text if text is not None else spec_example(), encoding="utf-8")
    return root


EXPECTED = {
    "common/chapter_01/_meta.yaml": {"chapter": {
        "id": "chapter_01", "number": 1, "title": "港町", "route": "common",
        "scenes": ["scene_00_market", "scene_01", "scene_02", "scene_03"],
    }},
    "common/chapter_01/scene_00_market.yaml": {
        "scene": {"id": "scene_00_market", "title": "市場で", "type": "dialogue", "background": "bg_market_day",
                  "characters": {"initial": [{"id": "kai", "position": "left", "emotion": "neutral"},
                                             {"id": "misaki", "position": "right", "emotion": "happy"}]}},
        "dialogues": [
            {"type": "narration", "text": "カイは港の市場で美咲を見つけた。"},
            {"speaker": "kai", "text": "美咲、こんなところで何してるんだ", "emotion": "surprised"},
            {"speaker": "misaki", "text": "私は魚を買いに来たの。カイこそ、どうしたの"},
            {"type": "thought", "speaker": "kai", "text": "言えるわけがない",
             "choices": [{"text": "一緒に市場を回る", "next_scene": "scene_01", "set_flag": "market_together"},
                         {"text": "船を見に行く", "next_scene": "scene_02"}]},
        ],
        "next_scene": None,
    },
    "common/chapter_01/scene_01.yaml": {
        "scene": {"id": "scene_01", "title": "市場を歩く", "type": "dialogue", "background": "bg_market_day",
                  "characters": {"initial": [{"id": "kai", "position": "left", "emotion": "happy"},
                                             {"id": "misaki", "position": "right", "emotion": "happy"}]}},
        "dialogues": [{"type": "narration", "text": "二人は市場を歩いた。"}],
        "next_scene": "scene_03",
    },
    "common/chapter_01/scene_02.yaml": {
        "scene": {"id": "scene_02", "title": "船を見る", "type": "dialogue", "background": "bg_harbor_day",
                  "characters": {"initial": [{"id": "kai", "position": "center", "emotion": "neutral"}]}},
        "dialogues": [{"type": "narration", "text": "カイは一人で船を眺めた。"}],
        "next_scene": "scene_03",
    },
    "common/chapter_01/scene_03.yaml": {
        "scene": {"id": "scene_03", "title": "門で別れる", "type": "dialogue", "background": "bg_gate_evening",
                  "characters": {"initial": [{"id": "kai", "position": "left", "emotion": "neutral"},
                                             {"id": "misaki", "position": "right", "emotion": "neutral"}]}},
        "dialogues": [
            {"speaker": "kai", "text": "君と回れて楽しかった", "condition": "market_together"},
            {"speaker": "kai", "text": "美咲、船はすごかったぞ", "condition": "!market_together"},
            {"speaker": "misaki", "text": "また明日ね"},
        ],
        "next_scene": None,
    },
}


def test_spec_example_export(tmp_path: Path) -> None:
    files = export_chapter(make_work(tmp_path), "ch-001")
    assert files == EXPECTED
    # キーの順（エンジンでの読みやすさのため）
    assert list(files["common/chapter_01/scene_03.yaml"]) == ["scene", "dialogues", "next_scene"]


def test_only_engine_keys_are_used(tmp_path: Path) -> None:
    files = export_chapter(make_work(tmp_path), "ch-001")
    for rel, data in files.items():
        if rel.endswith("_meta.yaml"):
            continue
        for d in data["dialogues"]:
            assert set(d) <= set(DIALOGUE_KEYS), d
            assert set(d.get("characters", {})) <= set(CHARACTER_CHANGE_KEYS)
            for c in d.get("choices", []):
                assert set(c) <= set(CHOICE_KEYS)


def test_stage_changes_between_lines(tmp_path: Path) -> None:
    text = (
        "<!-- scene: S1 -->\n@bg bg_a\n@enter C001 left\nカイ「a」\n@enter C002 right angry\n@face C001 happy\n"
        "美咲「b」\n@exit C002\nカイ「c」\n@choice\n- x -> S2\n- y -> S3\n"
        "<!-- scene: S2 -->\n@goto S4\n<!-- scene: S3 -->\nd。\n<!-- scene: S4 -->\ne。\n"
    )
    plan = spec_plan()
    plan["scenes"][0]["choice"] = [{"text": "x", "goto": "S2"}, {"text": "y", "goto": "S3"}]
    files = export_chapter(make_work(tmp_path, text=text, plan=plan), "ch-001")
    s1 = files["common/chapter_01/scene_00_market.yaml"]
    assert s1["scene"]["characters"] == {"initial": [{"id": "kai", "position": "left", "emotion": "neutral"}]}
    assert s1["dialogues"] == [
        {"speaker": "kai", "text": "a"},
        {"speaker": "misaki", "text": "b", "characters": {
            "add": [{"id": "misaki", "position": "right", "emotion": "angry"}],
            "update": [{"id": "kai", "emotion": "happy"}]}},
        {"speaker": "kai", "text": "c", "characters": {"remove": ["misaki"]},
         "choices": [{"text": "x", "next_scene": "scene_01"}, {"text": "y", "next_scene": "scene_02"}]},
    ]
    assert files["common/chapter_01/scene_01.yaml"]["dialogues"] == []


def test_extras_speak_by_name(tmp_path: Path) -> None:
    text = spec_example().replace("カイは一人で船を眺めた。", "カイは一人で船を眺めた。\n船乗り「いい船だろう」")
    plan = spec_plan()
    plan["scenes"][2]["extras"] = ["船乗り"]
    files = export_chapter(make_work(tmp_path, text=text, plan=plan), "ch-001")
    assert files["common/chapter_01/scene_02.yaml"]["dialogues"][-1] == {"speaker": "船乗り", "text": "いい船だろう"}


def test_route_and_engine_chapter(tmp_path: Path) -> None:
    files = export_chapter(make_work(tmp_path), "ch-001", route="order", engine_chapter="chapter_06")
    assert "order/chapter_06/_meta.yaml" in files
    assert files["order/chapter_06/_meta.yaml"]["chapter"]["id"] == "chapter_06"
    assert files["order/chapter_06/_meta.yaml"]["chapter"]["route"] == "order"


def test_defaults() -> None:
    assert default_engine_chapter("ch-001") == "chapter_01"
    assert default_engine_chapter("ch-012") == "chapter_12"
    assert default_engine_chapter("ch-123") == "chapter_123"
    assert scene_engine_ids({"scenes": [{"id": "S1"}, {"id": "S2", "engine_id": "x"}, {"id": "S3"}]}) == {
        "S1": "scene_00", "S2": "x", "S3": "scene_02"}


@pytest.mark.parametrize("kw,match", [
    ({"text_format": "novel"}, "not a script work"),
    ({"state": "AI_VALIDATED"}, "must be FINAL"),
    ({"text": spec_example().replace("@enter C001 left neutral", "@enter C001 top", 1)}, "script_syntax"),
    ({"text": spec_example().replace("@goto S4\n", "")}, "script_flow"),
    ({"chars": ({k: v for k, v in C001.items() if k != "engine_id"}, C002)},
     "C001 has no engine_id"),
])
def test_export_errors(tmp_path: Path, kw: dict, match: str) -> None:
    with pytest.raises(ExportError, match=match):
        export_chapter(make_work(tmp_path, **kw), "ch-001")


def test_stage_changes_after_last_line(tmp_path: Path) -> None:
    text = spec_example().replace("美咲「また明日ね」", "美咲「また明日ね」\n@exit C002")
    with pytest.raises(ExportError, match="after the last line"):
        export_chapter(make_work(tmp_path, text=text), "ch-001")


def test_choice_without_line(tmp_path: Path) -> None:
    text = spec_example().split("<!-- scene: S2 -->", 1)[1]
    text = ("<!-- scene: S1 -->\n@bg bg_market_day\n@choice\n- 一緒に市場を回る -> S2 +market_together\n"
            "- 船を見に行く -> S3\n<!-- scene: S2 -->" + text)
    with pytest.raises(ExportError, match="@choice needs a line"):
        export_chapter(make_work(tmp_path, text=text), "ch-001")


def test_write_export_and_cli(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    root = make_work(tmp_path / "work")
    files = export_chapter(root, "ch-001")
    written = write_export(files, tmp_path / "out")
    assert sorted(p.relative_to(tmp_path / "out").as_posix() for p in written) == sorted(files)
    assert loads_yaml((tmp_path / "out" / "common/chapter_01/scene_03.yaml").read_text(encoding="utf-8")) == \
        EXPECTED["common/chapter_01/scene_03.yaml"]

    class _W:
        path = root
        work_key = "w"
    monkeypatch.setattr(cli, "get_work", lambda cfg, key: _W())
    code = cli.main(["export", "--work", "w", "--chapter", "ch-001", "--out", str(tmp_path / "out2")], settings=object())
    assert code == 0
    assert "wrote" in capsys.readouterr().out
    (root / "chapters" / "ch-001" / "chapter.yaml").write_text(
        (root / "chapters" / "ch-001" / "chapter.yaml").read_text(encoding="utf-8").replace("FINAL", "DRAFTED"),
        encoding="utf-8")
    assert cli.main(["export", "--work", "w", "--chapter", "ch-001", "--out", str(tmp_path / "out3")], settings=object()) == 1
