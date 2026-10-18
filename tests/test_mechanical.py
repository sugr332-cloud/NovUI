"""Tests for novui.mechanical module."""

from pathlib import Path

from novui.mechanical import (
    MechanicalInputs,
    check_forbidden_words,
    check_prohibited_phrases,
    check_ref_ids,
    check_scene_markers,
    find_scene_markers,
    load_mechanical_inputs,
    run_draft_mechanical_checks,
    strip_scene_markers,
)
from novui.yamlio import dumps_yaml


def _text(*parts: str) -> str:
    return "\n".join(parts) + "\n"


PLAN_IDS = ["S1", "S2", "S3"]


def test_find_scene_markers_strict_and_malformed() -> None:
    text = _text(
        "<!-- scene: S1 -->",
        "本文1",
        "<!--scene: S2-->",
        "<!-- scene: S2 --> 余計な文字",
        "<!-- scene: S3 -->",
    )
    markers, malformed = find_scene_markers(text)
    assert [(m.scene_id, m.line) for m in markers] == [("S1", 1), ("S3", 5)]
    assert malformed == [3, 4]


def test_scene_markers_pass() -> None:
    text = _text("<!-- scene: S1 -->", "a", "<!-- scene: S2 -->", "b", "<!-- scene: S3 -->", "c")
    res = check_scene_markers(text, PLAN_IDS)
    assert res.status == "PASS"
    assert res.details == ()


def test_scene_markers_missing() -> None:
    text = _text("<!-- scene: S1 -->", "a", "<!-- scene: S3 -->", "c")
    res = check_scene_markers(text, PLAN_IDS)
    assert res.status == "WARNING"
    assert res.details == ("missing scene S2",)


def test_scene_markers_none_at_all() -> None:
    res = check_scene_markers(_text("本文だけ"), PLAN_IDS)
    assert res.status == "WARNING"
    assert res.details == ("missing scene S1", "missing scene S2", "missing scene S3")


def test_scene_markers_duplicate() -> None:
    text = _text(
        "<!-- scene: S1 -->", "a",
        "<!-- scene: S2 -->", "b",
        "<!-- scene: S2 -->", "b2",
        "<!-- scene: S3 -->", "c",
    )
    res = check_scene_markers(text, PLAN_IDS)
    assert res.status == "WARNING"
    assert res.details == ("duplicate scene S2 at lines 3, 5",)


def test_scene_markers_order() -> None:
    text = _text("<!-- scene: S2 -->", "b", "<!-- scene: S1 -->", "a", "<!-- scene: S3 -->", "c")
    res = check_scene_markers(text, PLAN_IDS)
    assert res.status == "WARNING"
    assert res.details == ("scene order mismatch: expected S1, S2, S3; got S2, S1, S3",)


def test_scene_markers_not_in_plan() -> None:
    text = _text(
        "<!-- scene: S1 -->", "a",
        "<!-- scene: S2 -->", "b",
        "<!-- scene: S3 -->", "c",
        "<!-- scene: S4 -->", "d",
    )
    res = check_scene_markers(text, PLAN_IDS)
    assert res.status == "WARNING"
    assert res.details == ("scene S4 at line 7 is not in plan",)


def test_scene_markers_malformed_reported() -> None:
    text = _text("<!-- scene: S1 -->", "a", "<!-- scene:S2 -->", "b", "<!-- scene: S3 -->", "c")
    res = check_scene_markers(text, PLAN_IDS)
    assert res.status == "WARNING"
    assert res.details == ("malformed scene marker at line 3", "missing scene S2")


def _inputs(**kw) -> MechanicalInputs:
    base = dict(characters={}, foreshadow_ids=frozenset(), prohibited_phrases=(), load_errors=())
    base.update(kw)
    return MechanicalInputs(**base)


def test_ref_ids() -> None:
    inputs = _inputs(characters={"C001": {"id": "C001", "name": "カイ"}}, foreshadow_ids=frozenset({"F001"}))
    assert check_ref_ids(["C001"], ["F001"], inputs).status == "PASS"
    res = check_ref_ids(["C001", "C999"], ["F001", "F999"], inputs)
    assert res.status == "WARNING"
    assert res.details == (
        "character C999 not found in characters/",
        "foreshadowing F999 not found in foreshadowing/registry.yaml",
    )


def test_prohibited_phrases_excludes_markers() -> None:
    text = _text("<!-- scene: S1 -->", "まるで嘘のように静かだった。まるで嘘のようだ。")
    res = check_prohibited_phrases(text, ["まるで嘘のよう", "scene", "存在しない語"])
    assert res.status == "WARNING"
    assert res.details == ("'まるで嘘のよう': 2",)
    assert check_prohibited_phrases(text, []).status == "PASS"
    assert "scene" not in strip_scene_markers(text)


def test_forbidden_words_only_registered() -> None:
    chars = {
        "C001": {"id": "C001", "name": "カイ", "speech": {"forbidden": ["僕", "わたくし"]}},
        "C002": {"id": "C002", "name": "ミサ"},
    }
    text = _text("<!-- scene: S1 -->", "僕は行く。わたくしも。")
    # 「僕」だけが rules/prohibited.yaml に登録されている
    res = check_forbidden_words(text, chars, ["僕"])
    assert res.status == "WARNING"
    assert res.details == ("C001 speech.forbidden '僕': 1",)
    assert check_forbidden_words(text, chars, []).status == "PASS"


def test_load_mechanical_inputs(tmp_path: Path) -> None:
    (tmp_path / "characters").mkdir()
    (tmp_path / "characters" / "C001.yaml").write_text(dumps_yaml({"id": "C001", "name": "カイ"}), encoding="utf-8")
    (tmp_path / "characters" / "C002.yaml").write_text(dumps_yaml({"id": "C003", "name": "名前違い"}), encoding="utf-8")
    (tmp_path / "characters" / "C004.yaml").write_text("id: C004\n", encoding="utf-8")  # name がない
    (tmp_path / "foreshadowing").mkdir()
    reg = [{
        "id": "F001", "name": "紋章", "status": "planned", "importance": "major",
        "introduced": [], "hints": [], "developments": [],
        "planned_resolution": None, "resolved": None, "notes": "",
    }]
    (tmp_path / "foreshadowing" / "registry.yaml").write_text(dumps_yaml(reg), encoding="utf-8")
    (tmp_path / "rules").mkdir()
    (tmp_path / "rules" / "prohibited.yaml").write_text(
        dumps_yaml({"phrases": ["定型句"], "repeated_ending_threshold": 3}), encoding="utf-8"
    )

    inputs = load_mechanical_inputs(tmp_path)
    assert set(inputs.characters) == {"C001"}
    assert inputs.foreshadow_ids == frozenset({"F001"})
    assert inputs.prohibited_phrases == ("定型句",)
    assert len(inputs.load_errors) == 2
    assert inputs.load_errors[0].startswith("characters/C002.yaml")
    assert inputs.load_errors[1].startswith("characters/C004.yaml")


def test_load_mechanical_inputs_empty(tmp_path: Path) -> None:
    inputs = load_mechanical_inputs(tmp_path)
    assert inputs.characters == {}
    assert inputs.foreshadow_ids == frozenset()
    assert inputs.prohibited_phrases == ()
    assert inputs.load_errors == ()


def test_run_draft_mechanical_checks() -> None:
    plan = {"scenes": [
        {"id": "S1", "characters": ["C001"], "foreshadowing": ["F001"]},
        {"id": "S2", "characters": ["C001", "C999"], "foreshadowing": []},
    ]}
    inputs = _inputs(
        characters={"C001": {"id": "C001", "name": "カイ"}},
        foreshadow_ids=frozenset({"F001"}),
        load_errors=("characters/C005.yaml: broken",),
    )
    text = _text("<!-- scene: S1 -->", "a", "<!-- scene: S2 -->", "b")
    results = run_draft_mechanical_checks(text, plan, inputs)
    by_name = {r.name: r for r in results}
    assert [r.name for r in results] == [
        "mechanical_inputs", "scene_markers", "ref_ids", "prohibited_phrases", "forbidden_words",
    ]
    assert by_name["mechanical_inputs"].status == "WARNING"
    assert by_name["scene_markers"].status == "PASS"
    assert by_name["ref_ids"].details == ("character C999 not found in characters/",)
    assert all(r.status in ("PASS", "WARNING") for r in results)
