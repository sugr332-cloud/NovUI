import pytest

from novui.positions import PositionError, chapter_index, compare_positions, format_position, position_key, scene_number

ORDER = ["ch-001", "ch-002", "ch-010"]


def P(ch: str, sc: str) -> dict:
    return {"chapter": ch, "scene": sc}


@pytest.mark.parametrize("sid,num", [("S1", 1), ("S003", 3), ("S10", 10), ("S0", 0)])
def test_scene_number(sid: str, num: int) -> None:
    assert scene_number(sid) == num


@pytest.mark.parametrize("sid", ["s1", "S", "SX", "S-1", "", "1", None])
def test_scene_number_invalid(sid) -> None:
    with pytest.raises(PositionError):
        scene_number(sid)


def test_chapter_index() -> None:
    assert chapter_index(ORDER, "ch-010") == 2
    with pytest.raises(PositionError, match="ch-099"):
        chapter_index(ORDER, "ch-099")


def test_scene_numeric_order() -> None:
    assert compare_positions(ORDER, P("ch-001", "S2"), P("ch-001", "S10")) == -1
    assert compare_positions(ORDER, P("ch-001", "S10"), P("ch-001", "S2")) == 1


def test_zero_padded_scene_is_same_position() -> None:
    assert compare_positions(ORDER, P("ch-001", "S003"), P("ch-001", "S3")) == 0


def test_chapter_order_wins_over_scene() -> None:
    assert compare_positions(ORDER, P("ch-001", "S99"), P("ch-002", "S1")) == -1
    assert position_key(ORDER, P("ch-010", "S4")) == (2, 4)


def test_order_is_chapters_order_not_id() -> None:
    order = ["ch-002", "ch-001"]
    assert compare_positions(order, P("ch-001", "S1"), P("ch-002", "S1")) == 1


@pytest.mark.parametrize(
    "a,b,which",
    [
        (P("ch-099", "S1"), P("ch-001", "S1"), "first"),
        (P("ch-001", "S1"), P("ch-001", "X1"), "second"),
        ({"chapter": "ch-001"}, P("ch-001", "S1"), "first"),
        ("ch-001 S1", P("ch-001", "S1"), "first"),
    ],
)
def test_compare_invalid(a, b, which: str) -> None:
    with pytest.raises(PositionError, match=which):
        compare_positions(ORDER, a, b)


def test_format_position() -> None:
    assert format_position(P("ch-018", "S004")) == "ch-018 S004"
