import copy

import pytest

from novui.charrules import (
    RuleTables,
    character_table,
    check_character_rules,
    effective_value,
    is_known_rule,
    render_rule_tables,
    scene_rule_tables,
)
from novui.schema import validate_or_raise
from novui.yamlio import loads_yaml

ORDER = ["ch-004", "ch-010", "ch-017", "ch-018", "ch-019"]


def P(ch: str, sc: str) -> dict:
    return {"chapter": ch, "scene": sc}


def CH(value: str, ch: str, sc: str, reason: str = "r") -> dict:
    return {"value": value, "from": P(ch, sc), "reason": reason}


# 仕様 §6.4.1 の例
SPEC_C001 = {
    "id": "C001",
    "name": "山田太郎",
    "personality": ["冷静", "弱みを見せない"],
    "behavior": ["危険時に仲間を優先する"],
    "speech": {"first_person": "俺", "formality": "casual", "endings": ["だ", "だろ"], "habits": [], "forbidden": ["僕"]},
    "address": {
        "default": "お前",
        "C002": {"default": "美咲", "changes": [CH("君", "ch-018", "S004", "関係性の変化")]},
        "C003": "先生",
    },
    "relationships": [{"with": "C002", "state": "幼なじみ", "changes": []}],
    "exceptions": [
        {"rule": "speech.formality", "from": P("ch-010", "S002"), "to": P("ch-010", "S003"), "reason": "公的な場面"}
    ],
    "knowledge": [{"id": "K014", "fact": "門番が紋章に反応したことを知っている", "source_chapter": "ch-004"}],
    "notes": "",
}


def test_spec_example_is_valid_character() -> None:
    validate_or_raise(SPEC_C001, "character")


# --- is_known_rule


@pytest.mark.parametrize(
    "rule",
    ["speech.first_person", "speech.formality", "speech.endings", "speech.habits", "speech.forbidden",
     "address.default", "address.C002", "relationships.C1234", "personality", "behavior"],
)
def test_known_rules(rule: str) -> None:
    assert is_known_rule(rule)


@pytest.mark.parametrize("rule", ["speech", "address", "address.C02", "relationships.default", "knowledge", "x"])
def test_unknown_rules(rule: str) -> None:
    assert not is_known_rule(rule)


# --- effective_value


def test_effective_no_changes() -> None:
    assert effective_value(ORDER, "美咲", [], P("ch-018", "S4"), label="L") == ("美咲", [])


def test_effective_before_at_after() -> None:
    changes = [CH("君", "ch-018", "S004")]
    assert effective_value(ORDER, "美咲", changes, P("ch-018", "S3"), label="L")[0] == "美咲"
    assert effective_value(ORDER, "美咲", changes, P("ch-018", "S4"), label="L")[0] == "君"  # from を含む
    assert effective_value(ORDER, "美咲", changes, P("ch-019", "S1"), label="L")[0] == "君"


def test_effective_latest_applicable_change_regardless_of_list_order() -> None:
    changes = [CH("c", "ch-019", "S1"), CH("b", "ch-018", "S2"), CH("a", "ch-017", "S5")]
    assert effective_value(ORDER, "d", changes, P("ch-018", "S9"), label="L") == ("b", [])
    assert effective_value(ORDER, "d", list(reversed(changes)), P("ch-018", "S9"), label="L") == ("b", [])
    assert effective_value(ORDER, "d", changes, P("ch-017", "S1"), label="L") == ("d", [])


def test_effective_duplicate_from_takes_later_and_warns() -> None:
    changes = [CH("x", "ch-018", "S2"), CH("y", "ch-018", "S002")]
    value, warnings = effective_value(ORDER, "d", changes, P("ch-018", "S3"), label="C001 address.C002")
    assert value == "y"
    assert warnings == ["C001 address.C002 has multiple changes from ch-018 S002"]


def test_effective_duplicate_warns_even_if_not_applied() -> None:
    changes = [CH("x", "ch-019", "S2"), CH("y", "ch-019", "S2")]
    value, warnings = effective_value(ORDER, "d", changes, P("ch-018", "S3"), label="L")
    assert value == "d"
    assert len(warnings) == 1


def test_effective_uncomparable_change_ignored() -> None:
    changes = [CH("x", "ch-099", "S1"), CH("y", "ch-017", "S1")]
    value, warnings = effective_value(ORDER, "d", changes, P("ch-018", "S3"), label="C001 address.C002")
    assert value == "y"
    assert len(warnings) == 1
    assert warnings[0].startswith("C001 address.C002 change from ch-099 S1:")
    assert "ch-099" in warnings[0]


# --- character_table


def test_table_spec_example_before_change() -> None:
    table, warnings = character_table(ORDER, SPEC_C001, P("ch-018", "S003"))
    assert warnings == []
    assert table == {
        "character": "C001",
        "name": "山田太郎",
        "speech": SPEC_C001["speech"],
        "address": {"default": "お前", "C002": "美咲", "C003": "先生"},
        "relationships": [{"with": "C002", "state": "幼なじみ"}],
        "suspended_rules": [],
        "knowledge": [{"id": "K014", "fact": "門番が紋章に反応したことを知っている", "source_chapter": "ch-004"}],
        "personality": ["冷静", "弱みを見せない"],
        "behavior": ["危険時に仲間を優先する"],
    }
    assert list(table) == [
        "character", "name", "speech", "address", "relationships", "suspended_rules", "knowledge",
        "personality", "behavior",
    ]


def test_table_spec_example_after_change() -> None:
    table, _ = character_table(ORDER, SPEC_C001, P("ch-018", "S004"))
    assert table["address"]["C002"] == "君"


def test_table_includes_all_address_targets() -> None:
    # 同じ場面にいない相手（C003）も出る（design §3）
    table, _ = character_table(ORDER, SPEC_C001, P("ch-018", "S1"))
    assert list(table["address"]) == ["default", "C002", "C003"]


def test_table_does_not_modify_doc() -> None:
    before = copy.deepcopy(SPEC_C001)
    table, _ = character_table(ORDER, SPEC_C001, P("ch-018", "S4"))
    table["speech"]["first_person"] = "x"
    table["personality"].append("x")
    assert SPEC_C001 == before


def test_table_only_fields_in_doc() -> None:
    doc = {"id": "C005", "name": "門番", "speech": {"first_person": "私"}}
    table, warnings = character_table(ORDER, doc, P("ch-018", "S1"))
    assert table == {"character": "C005", "name": "門番", "speech": {"first_person": "私"}, "suspended_rules": []}
    assert warnings == []


def test_table_empty_address_omitted() -> None:
    doc = {"id": "C005", "name": "門番", "address": {}}
    table, _ = character_table(ORDER, doc, P("ch-018", "S1"))
    assert "address" not in table


def test_table_relationship_changes() -> None:
    doc = {
        "id": "C001",
        "name": "a",
        "relationships": [{"with": "C002", "state": "幼なじみ", "changes": [CH("恋人", "ch-018", "S2")]}],
    }
    assert character_table(ORDER, doc, P("ch-018", "S1"))[0]["relationships"] == [{"with": "C002", "state": "幼なじみ"}]
    assert character_table(ORDER, doc, P("ch-018", "S2"))[0]["relationships"] == [{"with": "C002", "state": "恋人"}]


@pytest.mark.parametrize(
    "scene,suspended",
    [("S001", False), ("S002", True), ("S003", True), ("S004", False)],
)
def test_table_exception_range_inclusive(scene: str, suspended: bool) -> None:
    table, _ = character_table(ORDER, SPEC_C001, P("ch-010", scene))
    expected = [{"rule": "speech.formality", "reason": "公的な場面"}] if suspended else []
    assert table["suspended_rules"] == expected


def test_table_exception_other_chapter() -> None:
    assert character_table(ORDER, SPEC_C001, P("ch-017", "S2"))[0]["suspended_rules"] == []


def test_table_exception_from_after_to_warns_and_not_applied() -> None:
    doc = {"id": "C001", "name": "a", "exceptions": [
        {"rule": "speech.formality", "from": P("ch-010", "S5"), "to": P("ch-010", "S1"), "reason": "r"},
    ]}
    table, warnings = character_table(ORDER, doc, P("ch-010", "S3"))
    assert table["suspended_rules"] == []
    assert warnings == ["C001 exceptions[0] from ch-010 S5 is after to ch-010 S1"]


def test_table_exception_uncomparable_warns() -> None:
    doc = {"id": "C001", "name": "a", "exceptions": [
        {"rule": "speech.formality", "from": P("ch-099", "S1"), "to": P("ch-010", "S1"), "reason": "r"},
    ]}
    table, warnings = character_table(ORDER, doc, P("ch-010", "S1"))
    assert table["suspended_rules"] == []
    assert len(warnings) == 1 and warnings[0].startswith("C001 exceptions[0]") and "ch-099" in warnings[0]


def test_table_exception_unknown_rule_warns_but_listed() -> None:
    doc = {"id": "C001", "name": "a", "exceptions": [
        {"rule": "speech.tone", "from": P("ch-010", "S1"), "to": P("ch-010", "S3"), "reason": "r"},
    ]}
    table, warnings = character_table(ORDER, doc, P("ch-010", "S2"))
    assert table["suspended_rules"] == [{"rule": "speech.tone", "reason": "r"}]
    assert warnings == ["C001 exceptions[0] unknown rule 'speech.tone'"]


def test_table_knowledge_only_from_earlier_chapters() -> None:
    doc = {"id": "C001", "name": "a", "knowledge": [
        {"id": "K001", "fact": "前", "source_chapter": "ch-017"},
        {"id": "K002", "fact": "この章", "source_chapter": "ch-018"},
        {"id": "K003", "fact": "後", "source_chapter": "ch-019"},
        {"id": "K004", "fact": "不明", "source_chapter": "ch-099"},
    ]}
    table, warnings = character_table(ORDER, doc, P("ch-018", "S1"))
    assert table["knowledge"] == [{"id": "K001", "fact": "前", "source_chapter": "ch-017"}]
    assert warnings == ["C001 knowledge K004 source_chapter ch-099 not in chapters-order.yaml"]


def test_table_knowledge_empty_list_kept() -> None:
    doc = {"id": "C001", "name": "a", "knowledge": [{"id": "K001", "fact": "x", "source_chapter": "ch-018"}]}
    assert character_table(ORDER, doc, P("ch-018", "S1"))[0]["knowledge"] == []


# --- scene_rule_tables


def _plan(*scenes: tuple[str, list[str]]) -> dict:
    return {"scenes": [{"id": sid, "characters": chars} for sid, chars in scenes]}


C002 = {"id": "C002", "name": "美咲", "speech": {"first_person": "私"}, "address": {"C001": "カイ"}}


def test_scene_tables_per_scene() -> None:
    plan = _plan(("S003", ["C002", "C001"]), ("S004", ["C001"]))
    tables = scene_rule_tables(ORDER, "ch-018", plan, {"C001": SPEC_C001, "C002": C002})
    assert tables.warnings == ()
    assert [s["scene"] for s in tables.scenes] == ["S003", "S004"]
    assert [t["character"] for t in tables.scenes[0]["characters"]] == ["C002", "C001"]
    assert tables.scenes[0]["characters"][1]["address"]["C002"] == "美咲"
    assert tables.scenes[1]["characters"][0]["address"]["C002"] == "君"


def test_scene_tables_skip_unknown_characters_and_empty_scenes() -> None:
    plan = _plan(("S1", ["C009"]), ("S2", ["C009", "C002"]), ("S3", []))
    tables = scene_rule_tables(ORDER, "ch-018", plan, {"C002": C002})
    assert [s["scene"] for s in tables.scenes] == ["S2"]
    assert [t["character"] for t in tables.scenes[0]["characters"]] == ["C002"]


def test_scene_tables_chapter_not_in_order() -> None:
    plan = _plan(("S1", ["C002"]), ("S2", ["C002"]))
    tables = scene_rule_tables(ORDER, "ch-099", plan, {"C002": C002})
    assert tables == RuleTables(scenes=(), warnings=("chapter ch-099 not in chapters-order.yaml",))


def test_scene_tables_warnings_deduplicated() -> None:
    doc = copy.deepcopy(SPEC_C001)
    doc["address"]["C002"]["changes"].append(CH("x", "ch-099", "S1"))
    plan = _plan(("S1", ["C001"]), ("S2", ["C001"]))
    tables = scene_rule_tables(ORDER, "ch-018", plan, {"C001": doc})
    assert len(tables.warnings) == 1
    assert "C001 address.C002 change from ch-099 S1" in tables.warnings[0]


# --- render / check


def test_render_empty() -> None:
    assert render_rule_tables(RuleTables(scenes=(), warnings=())) == "なし"


def test_render_round_trip() -> None:
    plan = _plan(("S003", ["C001", "C002"]))
    tables = scene_rule_tables(ORDER, "ch-018", plan, {"C001": SPEC_C001, "C002": C002})
    text = render_rule_tables(tables)
    assert not text.endswith("\n")
    assert loads_yaml(text) == list(tables.scenes)
    assert "山田太郎" in text  # 日本語をエスケープしない


def test_check_character_rules() -> None:
    assert check_character_rules(RuleTables(scenes=(), warnings=())).status == "PASS"
    c = check_character_rules(RuleTables(scenes=(), warnings=("w",)))
    assert (c.name, c.status, c.details) == ("character_rules", "WARNING", ("w",))


# --- 2-F2：フラグごとの規則表（phase2f-design §3） ---

from novui.charrules import MAX_FLAGS_PER_VALUE, flag_value  # noqa: E402
from novui.flagstate import work_flag_states  # noqa: E402

E = frozenset()
M = frozenset({"market_together"})
FORDER = ["ch-001", "ch-002"]


def CW(value: str, ch: str, sc: str, when: str) -> dict:
    return {**CH(value, ch, sc), "when": when}


def market_plan() -> dict:
    return {"type": "plan", "chapter_id": "ch-001", "scenes": [
        {"id": "S1", "characters": ["C001"], "choice": [
            {"text": "一緒に市場を回る", "goto": "S2", "flag": "market_together"}, {"text": "船を見に行く", "goto": "S3"}]},
        {"id": "S2", "characters": ["C001"], "next": "S4"},
        {"id": "S3", "characters": ["C001"]},
        {"id": "S4", "characters": ["C001"]},
    ]}


def kai(**extra) -> dict:
    return {"id": "C001", "name": "カイ", "address": {"C002": {"default": "美咲", "changes": [
        CW("君", "ch-001", "S2", "market_together")]}}, **extra}


def test_effective_value_filters_by_state() -> None:
    changes = [CW("君", "ch-001", "S2", "market_together"), CW("あんた", "ch-001", "S3", "not market_together")]
    assert effective_value(FORDER, "美咲", changes, P("ch-001", "S4"), label="L", state=M)[0] == "君"
    assert effective_value(FORDER, "美咲", changes, P("ch-001", "S4"), label="L", state=E)[0] == "あんた"
    assert effective_value(FORDER, "美咲", changes, P("ch-001", "S4"), label="L")[0] == "あんた"


def test_same_from_different_when_is_not_duplicate() -> None:
    changes = [CW("君", "ch-001", "S2", "f"), CW("あんた", "ch-001", "S2", "not f")]
    assert effective_value(FORDER, "d", changes, P("ch-001", "S2"), label="L")[1] == []
    changes.append(CW("お前", "ch-001", "S2", "f"))
    assert "multiple changes from ch-001 S2 when f" in effective_value(FORDER, "d", changes, P("ch-001", "S2"), label="L")[1][0]


def test_flag_value_rows_and_single_value() -> None:
    changes = [CW("君", "ch-001", "S2", "market_together")]
    value, w = flag_value(FORDER, "美咲", changes, P("ch-002", "S1"), {E, M}, label="L")
    assert value == [
        {"value": "美咲", "flags": {"market_together": False}},
        {"value": "君", "flags": {"market_together": True}},
    ] and w == []
    # 状態が1つに決まる場面では、条件を解いた1つの値
    assert flag_value(FORDER, "美咲", changes, P("ch-001", "S2"), {M}, label="L") == ("君", [])
    assert flag_value(FORDER, "美咲", changes, P("ch-001", "S3"), {E}, label="L") == ("美咲", [])
    # 変化の位置より前では、状態によらず default
    assert flag_value(FORDER, "美咲", changes, P("ch-001", "S1"), {E, M}, label="L") == ("美咲", [])


def test_flag_value_two_flags_and_limit() -> None:
    changes = [CW("君", "ch-001", "S2", "f"), CW("お前", "ch-001", "S3", "g")]
    states = {E, frozenset({"f"}), frozenset({"g"}), frozenset({"f", "g"})}
    value, w = flag_value(FORDER, "d", changes, P("ch-002", "S1"), states, label="L")
    assert value == [
        {"value": "d", "flags": {"f": False, "g": False}},
        {"value": "お前", "flags": {"f": False, "g": True}},
        {"value": "君", "flags": {"f": True, "g": False}},
        {"value": "お前", "flags": {"f": True, "g": True}},
    ] and w == []
    assert MAX_FLAGS_PER_VALUE == 2
    changes.append(CW("あなた", "ch-001", "S4", "h"))
    states = {frozenset(s) for s in ([], ["f"], ["g"], ["h"])}
    value, w = flag_value(FORDER, "d", changes, P("ch-002", "S1"), states, label="C001 address.C002")
    assert value == {"default": "d", "changes": changes}
    assert "C001 address.C002 depends on 3 flags at ch-002 S1 (limit 2)" in w[0]


def test_flag_value_unknown_states_lists_changes() -> None:
    changes = [CW("君", "ch-001", "S2", "f")]
    value, w = flag_value(FORDER, "d", changes, P("ch-002", "S1"), None, label="L")
    assert value == {"default": "d", "changes": changes} and "unknown" in w[0]
    # when の無い changes は、状態が分からなくても今までどおり
    assert flag_value(FORDER, "d", [CH("君", "ch-001", "S2")], P("ch-002", "S1"), None, label="L") == ("君", [])


def test_knowledge_when() -> None:
    doc = kai(knowledge=[
        {"id": "K001", "fact": "a", "source_chapter": "ch-001", "when": "market_together"},
        {"id": "K002", "fact": "b", "source_chapter": "ch-001", "when": "not market_together"},
        {"id": "K003", "fact": "c", "source_chapter": "ch-001"},
    ])
    known = character_table(FORDER, doc, P("ch-002", "S1"), {E, M})[0]["knowledge"]
    assert [k.get("when") for k in known] == ["market_together", "not market_together", None]
    known = character_table(FORDER, doc, P("ch-002", "S1"), {M})[0]["knowledge"]
    assert [(k["id"], "when" in k) for k in known] == [("K001", False), ("K003", False)]
    known = character_table(FORDER, doc, P("ch-002", "S1"), None)[0]["knowledge"]
    assert [k.get("when") for k in known] == ["market_together", "not market_together", None]


def test_relationship_rows() -> None:
    doc = {"id": "C001", "name": "カイ", "relationships": [
        {"with": "C002", "state": "幼なじみ", "changes": [CW("親友", "ch-001", "S2", "market_together")]}]}
    rels = character_table(FORDER, doc, P("ch-002", "S1"), {E, M})[0]["relationships"]
    assert rels == [{"with": "C002", "state": [
        {"value": "幼なじみ", "flags": {"market_together": False}},
        {"value": "親友", "flags": {"market_together": True}},
    ]}]


def test_scene_tables_with_flag_states() -> None:
    plan = market_plan()
    fs = work_flag_states(FORDER, {"ch-001": plan})
    tables = scene_rule_tables(FORDER, "ch-001", plan, {"C001": kai()}, fs)
    by_scene = {s["scene"]: s["characters"][0]["address"]["C002"] for s in tables.scenes}
    assert by_scene["S1"] == "美咲"
    assert by_scene["S2"] == "君"
    assert by_scene["S3"] == "美咲"
    assert by_scene["S4"] == [
        {"value": "美咲", "flags": {"market_together": False}},
        {"value": "君", "flags": {"market_together": True}},
    ]
    assert tables.warnings == ()
    rendered = render_rule_tables(tables)
    assert "market_together: true" in rendered
    # flag_states が無いときは、どのフラグも立っていない
    tables = scene_rule_tables(FORDER, "ch-001", plan, {"C001": kai()})
    assert {s["characters"][0]["address"]["C002"] for s in tables.scenes} == {"美咲"}
