import copy
from pathlib import Path
from typing import Any

import pytest

from novui.foreshadowcheck import (
    check_foreshadow_order,
    check_foreshadow_overdue,
    check_foreshadow_plan_refs,
    check_foreshadow_positions,
    check_new_order_issues,
    foreshadow_order_issues,
    foreshadow_status_rows,
    load_registry,
    render_foreshadow_status,
    run_foreshadow_checks,
)
from novui.schema import validate_or_raise
from novui.semantics import check_registry
from novui.yamlio import dumps_yaml, loads_yaml

ORDER = ["ch-001", "ch-004", "ch-008", "ch-010", "ch-015", "ch-016"]


def P(ch: str, sc: str) -> dict:
    return {"chapter": ch, "scene": sc}


def F(fid: str = "F001", **kw: Any) -> dict:
    item = {
        "id": fid, "name": "王家の紋章", "status": "active", "importance": "major",
        "introduced": [], "hints": [], "developments": [],
        "planned_resolution": None, "resolved": None, "notes": "",
    }
    item.update(kw)
    return item


# 仕様 §6.4.2 の例
SPEC_F001 = F(
    introduced=[P("ch-001", "S003")],
    hints=[P("ch-004", "S002"), P("ch-008", "S001")],
    developments=[P("ch-010", "S004")],
    planned_resolution=P("ch-015", "S003"),
)


def test_spec_example_valid() -> None:
    validate_or_raise([SPEC_F001], "registry")
    assert check_registry([SPEC_F001]) == []


# --- load_registry


def test_load_registry(tmp_path: Path) -> None:
    assert load_registry(tmp_path) is None
    (tmp_path / "foreshadowing").mkdir()
    path = tmp_path / "foreshadowing" / "registry.yaml"
    path.write_text(dumps_yaml([SPEC_F001]), encoding="utf-8")
    assert load_registry(tmp_path) == [SPEC_F001]
    path.write_text("- id: bad\n", encoding="utf-8")
    assert load_registry(tmp_path) is None
    path.write_text(dumps_yaml([SPEC_F001, SPEC_F001]), encoding="utf-8")  # semantic: duplicate id
    assert load_registry(tmp_path) is None
    path.write_text(": :\n  - [", encoding="utf-8")
    assert load_registry(tmp_path) is None


# --- positions


def test_positions_pass_and_warning() -> None:
    assert check_foreshadow_positions(ORDER, [SPEC_F001]).status == "PASS"
    item = F(introduced=[P("ch-099", "S1")], planned_resolution=P("ch-098", "S1"))
    c = check_foreshadow_positions(ORDER, [item])
    assert c.name == "foreshadow_positions" and c.status == "WARNING"
    assert len(c.details) == 2
    assert c.details[0].startswith("F001 introduced[0] ch-099 S1:")
    assert c.details[1].startswith("F001 planned_resolution ch-098 S1:")


# --- order


def test_order_spec_example_ok() -> None:
    assert foreshadow_order_issues(ORDER, [SPEC_F001]) == []
    assert check_foreshadow_order(ORDER, [SPEC_F001]).status == "PASS"


def test_order_hint_before_introduced() -> None:
    item = F(introduced=[P("ch-004", "S1")], hints=[P("ch-001", "S5")])
    assert foreshadow_order_issues(ORDER, [item]) == ["F001 hints[0] ch-001 S5 is before introduced ch-004 S1"]
    c = check_foreshadow_order(ORDER, [item])
    assert (c.name, c.status) == ("foreshadow_order", "WARNING")


def test_order_development_before_introduced() -> None:
    item = F(introduced=[P("ch-004", "S2")], developments=[P("ch-004", "S1")])
    assert foreshadow_order_issues(ORDER, [item]) == ["F001 developments[0] ch-004 S1 is before introduced ch-004 S2"]


def test_order_uses_earliest_introduced() -> None:
    item = F(introduced=[P("ch-008", "S1"), P("ch-001", "S2")], hints=[P("ch-004", "S1")])
    assert foreshadow_order_issues(ORDER, [item]) == []


def test_order_no_introduced() -> None:
    assert foreshadow_order_issues(ORDER, [F(hints=[P("ch-004", "S1")])]) == [
        "F001 has hints/developments/resolved but no introduced"
    ]
    assert foreshadow_order_issues(ORDER, [F(status="planned")]) == []


def test_order_resolved_before_introduced() -> None:
    item = F(status="resolved", introduced=[P("ch-008", "S1")], resolved=P("ch-004", "S1"))
    assert foreshadow_order_issues(ORDER, [item]) == ["F001 resolved ch-004 S1 is before introduced ch-008 S1"]


def test_order_after_resolved() -> None:
    item = F(
        status="resolved", introduced=[P("ch-001", "S1")],
        hints=[P("ch-010", "S1")], developments=[P("ch-015", "S1")], resolved=P("ch-008", "S1"),
    )
    assert foreshadow_order_issues(ORDER, [item]) == [
        "F001 hints[0] ch-010 S1 is after resolved ch-008 S1",
        "F001 developments[0] ch-015 S1 is after resolved ch-008 S1",
    ]


def test_order_same_position_is_ok() -> None:
    item = F(
        status="resolved", introduced=[P("ch-004", "S1")],
        hints=[P("ch-004", "S001")], developments=[P("ch-004", "S1")], resolved=P("ch-004", "S1"),
    )
    assert foreshadow_order_issues(ORDER, [item]) == []


def test_order_ignores_uncomparable() -> None:
    item = F(introduced=[P("ch-004", "S1")], hints=[P("ch-099", "S1")])
    assert foreshadow_order_issues(ORDER, [item]) == []


# --- overdue


@pytest.mark.parametrize(
    "planned_ch,status,importance,warn",
    [
        ("ch-004", "active", "major", True),    # 前の章が予定で未回収
        ("ch-004", "planned", "major", True),
        ("ch-008", "active", "major", False),   # この章が予定
        ("ch-010", "active", "major", False),   # 後の章が予定
        ("ch-004", "active", "normal", False),
        ("ch-004", "active", "minor", False),
        ("ch-004", "cancelled", "major", False),
    ],
)
def test_overdue(planned_ch: str, status: str, importance: str, warn: bool) -> None:
    item = F(status=status, importance=importance, introduced=[P("ch-001", "S1")], planned_resolution=P(planned_ch, "S2"))
    c = check_foreshadow_overdue(ORDER, [item], "ch-008")
    assert c.name == "foreshadow_overdue"
    assert c.status == ("WARNING" if warn else "PASS")
    if warn:
        assert c.details == (f"F001 planned_resolution {planned_ch} S2 has passed but not resolved",)


def test_overdue_resolved_and_null() -> None:
    resolved = F(status="resolved", planned_resolution=P("ch-001", "S1"), resolved=P("ch-004", "S1"))
    no_plan = F(fid="F002")
    assert check_foreshadow_overdue(ORDER, [resolved, no_plan], "ch-010").status == "PASS"


# --- plan_refs


def _plan(*scenes: tuple[str, list[str]]) -> dict:
    return {"scenes": [{"id": sid, "foreshadowing": fs} for sid, fs in scenes]}


def test_plan_refs() -> None:
    reg = [
        F("F001", status="cancelled"),
        F("F002", status="resolved", introduced=[P("ch-001", "S1")], resolved=P("ch-004", "S1")),
        F("F003", status="resolved", introduced=[P("ch-001", "S1")], resolved=P("ch-008", "S2")),
        F("F004"),
    ]
    plan = _plan(("S1", ["F001", "F004"]), ("S2", ["F002", "F003", "F999"]))
    c = check_foreshadow_plan_refs(reg, plan, "ch-008")
    assert c.name == "foreshadow_plan_refs"
    assert c.details == ("S1 refers to F001 which is cancelled", "S2 refers to F002 which is resolved")


# --- run_foreshadow_checks


def test_run_checks() -> None:
    assert run_foreshadow_checks(ORDER, None, _plan(), "ch-008") == []
    names = [c.name for c in run_foreshadow_checks(ORDER, [SPEC_F001], _plan(), "ch-008")]
    assert names == ["foreshadow_positions", "foreshadow_order", "foreshadow_overdue", "foreshadow_plan_refs"]


# --- status rows


def test_status_rows_selection_and_order() -> None:
    reg = [
        F("F001", importance="major", status="active"),
        F("F002", importance="minor", status="active"),
        F("F003", importance="major", status="planned"),
        F("F004", importance="major", status="resolved", introduced=[P("ch-001", "S1")], resolved=P("ch-004", "S1")),
        F("F005", importance="normal", status="active"),
    ]
    plan = _plan(("S1", ["F005"]), ("S2", ["F002", "F005"]), ("S3", ["F999"]))
    rows = foreshadow_status_rows(ORDER, reg, plan, "ch-008")
    assert [r["id"] for r in rows] == ["F005", "F002", "F001", "F003"]
    assert rows[0]["scenes_in_this_chapter"] == ["S1", "S2"]
    assert rows[2]["scenes_in_this_chapter"] == []


def test_status_row_contents() -> None:
    item = copy.deepcopy(SPEC_F001)
    item["hints"].append(P("ch-099", "S1"))  # 比較できない位置は last_hint にしない
    rows = foreshadow_status_rows(ORDER, [item], _plan(("S3", ["F001"])), "ch-015")
    assert rows == [{
        "id": "F001", "name": "王家の紋章", "status": "active", "importance": "major",
        "introduced": [P("ch-001", "S003")],
        "last_hint": P("ch-008", "S001"),
        "last_development": P("ch-010", "S004"),
        "planned_resolution": P("ch-015", "S003"),
        "resolved": None,
        "planned_resolution_in_this_chapter": True,
        "scenes_in_this_chapter": ["S3"],
    }]
    assert list(rows[0]) == [
        "id", "name", "status", "importance", "introduced", "last_hint", "last_development",
        "planned_resolution", "resolved", "planned_resolution_in_this_chapter", "scenes_in_this_chapter",
    ]


def test_status_row_no_hints() -> None:
    rows = foreshadow_status_rows(ORDER, [F()], _plan(("S1", ["F001"])), "ch-008")
    assert rows[0]["last_hint"] is None and rows[0]["last_development"] is None
    assert rows[0]["planned_resolution_in_this_chapter"] is False


def test_status_rows_none_registry() -> None:
    assert foreshadow_status_rows(ORDER, None, _plan(("S1", ["F001"])), "ch-008") == []


def test_render_status() -> None:
    assert render_foreshadow_status([]) == "なし"
    rows = foreshadow_status_rows(ORDER, [SPEC_F001], _plan(("S1", ["F001"])), "ch-008")
    text = render_foreshadow_status(rows)
    assert not text.endswith("\n")
    assert "王家の紋章" in text
    assert loads_yaml(text) == rows


# --- check_new_order_issues


def test_new_order_issues_only_new() -> None:
    before = [F("F001", introduced=[P("ch-004", "S1")], hints=[P("ch-001", "S1")]), F("F002", introduced=[P("ch-004", "S1")])]
    same = copy.deepcopy(before)
    c = check_new_order_issues(ORDER, before, same)
    assert (c.name, c.status) == ("foreshadow_order", "PASS")

    after = copy.deepcopy(before)
    after[1]["hints"].append(P("ch-001", "S2"))
    c = check_new_order_issues(ORDER, before, after)
    assert c.status == "WARNING"
    assert c.details == ("F002 hints[0] ch-001 S2 is before introduced ch-004 S1",)
