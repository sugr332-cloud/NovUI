"""Tests for novui.anchor."""

import pytest

from novui.anchor import AnchorError, find_anchor, split_by_range
from novui.checks import check_prefix_suffix


def test_find_anchor_japanese_exact() -> None:
    text = "第1章\n夜の帳が下りる頃、彼は静かに歩き始めた。\n月明かりが照らす道。"
    anchor = "静かに歩き始めた。"
    start, end = find_anchor(text, anchor)
    raw = text.encode("utf-8")
    assert raw[start:end].decode("utf-8") == anchor


def test_find_anchor_duplicate_and_context() -> None:
    text = "りんごを食べた。その後、またりんごを食べた。"
    anchor = "りんご"
    with pytest.raises(AnchorError) as exc_info:
        find_anchor(text, anchor)
    assert exc_info.value.count == 2

    # 文脈を付与して一意にする
    start, end = find_anchor(text, anchor, before="その後、また", after="を食べた。")
    raw = text.encode("utf-8")
    assert raw[start:end].decode("utf-8") == "りんご"
    assert start > 0


def test_find_anchor_not_found() -> None:
    text = "吾輩は猫である。"
    anchor = "犬"
    with pytest.raises(AnchorError) as exc_info:
        find_anchor(text, anchor)
    assert exc_info.value.count == 0


def test_find_anchor_empty_anchor_value_error() -> None:
    text = "任意のテキスト"
    with pytest.raises(ValueError):
        find_anchor(text, "")


def test_find_anchor_overlap_counting() -> None:
    text = "ああああ"
    anchor = "ああ"
    with pytest.raises(AnchorError) as exc_info:
        find_anchor(text, anchor)
    # "ああああ" の中で "ああ" はインデックス 0, 1, 2 で3回一致する
    assert exc_info.value.count == 3


def test_split_by_range_boundaries_and_errors() -> None:
    data = b"0123456789"
    pre, suf = split_by_range(data, 3, 7)
    assert pre == b"012"
    assert suf == b"789"

    # 先頭・末尾境界
    assert split_by_range(data, 0, 0) == (b"", b"0123456789")
    assert split_by_range(data, 10, 10) == (b"0123456789", b"")
    assert split_by_range(data, 0, 10) == (b"", b"")

    # 範囲外・逆転
    with pytest.raises(ValueError):
        split_by_range(data, -1, 5)
    with pytest.raises(ValueError):
        split_by_range(data, 5, 4)
    with pytest.raises(ValueError):
        split_by_range(data, 0, 11)


def test_anchor_replacement_combined_with_check_prefix_suffix() -> None:
    text = "前文です。置換前のテキスト。後文です。"
    anchor = "置換前のテキスト。"
    data = text.encode("utf-8")

    start, end = find_anchor(text, anchor)
    prefix, suffix = split_by_range(data, start, end)

    replacement = "置換後の新しい長いテキストです。".encode("utf-8")
    after = prefix + replacement + suffix

    res = check_prefix_suffix(after, prefix, suffix)
    assert res.status == "PASS"
    assert res.details == ()
