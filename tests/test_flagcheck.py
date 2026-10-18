from pathlib import Path

from novui.flagcheck import check_flag_conditions, check_new_flag_conditions, load_flag_names
from novui.flagstate import work_flag_states
from novui.yamlio import write_yaml_atomic

ORDER = ["ch-001", "ch-002", "ch-003"]
FLAGS = frozenset({"market_together", "other"})


def market_plan() -> dict:
    return {"type": "plan", "chapter_id": "ch-001", "scenes": [
        {"id": "S1", "choice": [{"text": "a", "goto": "S2", "flag": "market_together"}, {"text": "b", "goto": "S3"}]},
        {"id": "S2", "next": "S4"},
        {"id": "S3"},
        {"id": "S4"},
    ]}


def kw(upto: str = "ch-002", flags=FLAGS) -> dict:
    plans = {"ch-001": market_plan()}
    return dict(order=ORDER, flag_states=work_flag_states(ORDER, plans), plans=plans, flag_names=flags,
                upto_chapter=upto)


def change(scene: str, when: str | None = None, chapter: str = "ch-001") -> dict:
    c = {"value": "君", "from": {"chapter": chapter, "scene": scene}, "reason": "r"}
    if when is not None:
        c["when"] = when
    return c


def kai(*changes: dict, knowledge: list | None = None) -> dict:
    doc = {"id": "C001", "name": "カイ", "address": {"C002": {"default": "美咲", "changes": list(changes)}}}
    if knowledge is not None:
        doc["knowledge"] = knowledge
    return doc


def F(**positions) -> dict:
    item = {"id": "F001", "name": "n", "status": "active", "importance": "major", "introduced": [], "hints": [],
            "developments": [], "planned_resolution": None, "resolved": None, "notes": ""}
    item.update(positions)
    return item


def test_pass_for_conditions_that_fit() -> None:
    doc = kai(change("S2", "market_together"), change("S3", "not market_together"), change("S4"),
              change("S1", "market_together", chapter="ch-002"),
              knowledge=[{"id": "K001", "fact": "f", "source_chapter": "ch-001", "when": "market_together"}])
    reg = [F(introduced=[{"chapter": "ch-001", "scene": "S2", "when": "market_together"}])]
    c = check_flag_conditions({"C001": doc}, reg, **kw())
    assert (c.name, c.status, c.details) == ("flag_conditions", "PASS", ())


def test_branch_scene_without_when_or_with_wrong_when() -> None:
    doc = kai(change("S2"), change("S3", "market_together"))
    reg = [F(hints=[{"chapter": "ch-001", "scene": "S3"}])]
    c = check_flag_conditions({"C001": doc}, reg, **kw())
    assert c.status == "WARNING"
    assert c.details == (
        "C001 address.C002 changes[0] at ch-001 S2 (branch scene) has no when",
        "C001 address.C002 changes[1] when 'market_together' never holds at ch-001 S3",
        "F001 hints[0] at ch-001 S3 (branch scene) has no when",
    )


def test_partial_condition_in_branch_scene() -> None:
    # S4 は分岐の場面ではない。分岐の場面で、成り立たない道がある条件
    plans = {"ch-001": {"type": "plan", "chapter_id": "ch-001", "scenes": [
        {"id": "S1", "choice": [{"text": "a", "goto": "S2", "flag": "other"}, {"text": "b", "goto": "S3"}]},
        {"id": "S2", "choice": [{"text": "a", "goto": "S3", "flag": "market_together"}, {"text": "b", "goto": "S4"}]},
        {"id": "S3"},
        {"id": "S4"},
    ]}}
    args = dict(order=ORDER, flag_states=work_flag_states(ORDER, plans), plans=plans, flag_names=FLAGS,
                upto_chapter="ch-001")
    c = check_flag_conditions({"C001": kai(change("S3", "market_together"))}, None, **args)
    assert c.details == (
        "C001 address.C002 changes[0] when 'market_together' does not hold on every path into ch-001 S3 (branch scene)",
    )


def test_unregistered_flag_and_missing_registry() -> None:
    doc = kai(change("S1", "nosuch", chapter="ch-002"))
    assert "'nosuch' is not in flags/registry.yaml" in check_flag_conditions({"C001": doc}, None, **kw()).details[0]
    c = check_flag_conditions({"C001": doc}, None, **kw(flags=None))
    assert "flags/registry.yaml is missing" in c.details[0]


def test_knowledge_never_holds_and_later_chapters_skipped() -> None:
    doc = kai(change("S1", "nosuch", chapter="ch-003"),
              knowledge=[{"id": "K001", "fact": "f", "source_chapter": "ch-001", "when": "other"}])
    c = check_flag_conditions({"C001": doc}, None, **kw())
    assert c.details == ("C001 knowledge K001 when 'other' never holds at end of ch-001",)


def test_new_issues_only() -> None:
    before = kai(change("S2"))
    after = kai(change("S2"), change("S3"))
    c = check_new_flag_conditions(({"C001": before}, None), ({"C001": after}, None), **kw())
    assert c.details == ("C001 address.C002 changes[1] at ch-001 S3 (branch scene) has no when",)
    assert check_new_flag_conditions(({"C001": before}, None), ({"C001": before}, None), **kw()).status == "PASS"


def test_load_flag_names(tmp_path: Path) -> None:
    assert load_flag_names(tmp_path) is None
    (tmp_path / "flags").mkdir()
    write_yaml_atomic(tmp_path / "flags" / "registry.yaml", [{"name": "f", "description": "d", "notes": ""}])
    assert load_flag_names(tmp_path) == {"f"}
    write_yaml_atomic(tmp_path / "flags" / "registry.yaml", [{"name": "F"}])
    assert load_flag_names(tmp_path) is None
