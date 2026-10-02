"""Tests for novui.agy_output module."""

import pytest

from novui.agy_output import (
    AgyOutputError,
    AgyText,
    Marker,
    markers_to_requests,
    parse_agy_text,
    splice_range,
)
from novui.checks import check_prefix_suffix
from novui.schema import SchemaError


def test_parse_agy_text_whitespace_and_newline() -> None:
    # 末尾の空白の除去と改行の付加（既定: trailing_newline=True）
    raw = b"   \xe7\xac\xac1\xe7\xab\xa0\xe3\x81\xae\xe6\x9c\xac\xe6\x96\x87\xe3\x81\xa7\xe3\x81\x99\xe3\x80\x82   \n\n\t  "
    res = parse_agy_text(raw)
    assert res.text == "   第1章の本文です。\n"
    assert res.markers == ()

    # trailing_newline=False
    res_no_nl = parse_agy_text(raw, trailing_newline=False)
    assert res_no_nl.text == "   第1章の本文です。"
    assert res_no_nl.markers == ()


def test_parse_agy_text_errors() -> None:
    # 1. decode error
    with pytest.raises(AgyOutputError) as exc_dec:
        parse_agy_text(b"\xff\xfe invalid utf8")
    assert exc_dec.value.kind == "decode"

    # 2. empty (empty bytes or whitespace only)
    with pytest.raises(AgyOutputError) as exc_empty1:
        parse_agy_text(b"")
    assert exc_empty1.value.kind == "empty"

    with pytest.raises(AgyOutputError) as exc_empty2:
        parse_agy_text(b"   \n\t  \r\n  ")
    assert exc_empty2.value.kind == "empty"

    # 3. code_fence (starts with ```, directly or after whitespace)
    with pytest.raises(AgyOutputError) as exc_fence1:
        parse_agy_text(b"```markdown\n# Title\n```")
    assert exc_fence1.value.kind == "code_fence"

    with pytest.raises(AgyOutputError) as exc_fence2:
        parse_agy_text(b"  \n  ```\ncode block\n```")
    assert exc_fence2.value.kind == "code_fence"


def test_parse_agy_text_markers() -> None:
    # 全角コロン、半角コロン、前後の空白、複数、空マーカー
    text = (
        "冒頭文。"
        "【要確認：主人公の年齢】"
        "中盤文。"
        "【要確認:  街の名前  】"
        "終盤文。"
        "【要確認：】"
        "【要確認:   】"
    )
    res = parse_agy_text(text.encode("utf-8"))
    assert len(res.markers) == 4

    # 1. 全角コロン
    m0 = res.markers[0]
    assert m0.message == "主人公の年齢"
    assert res.text[m0.start:m0.end] == "【要確認：主人公の年齢】"

    # 2. 半角コロン & 前後の空白トリム
    m1 = res.markers[1]
    assert m1.message == "街の名前"
    assert res.text[m1.start:m1.end] == "【要確認:  街の名前  】"

    # 3. 空マーカー（全角） -> "（記載なし）"
    m2 = res.markers[2]
    assert m2.message == "（記載なし）"
    assert res.text[m2.start:m2.end] == "【要確認：】"

    # 4. 空マーカー（半角・空白のみ） -> "（記載なし）"
    m3 = res.markers[3]
    assert m3.message == "（記載なし）"
    assert res.text[m3.start:m3.end] == "【要確認:   】"

    # マーカーのない本文で markers が空
    clean_text = "これはマーカーを一切含まない通常の本文です。"
    clean_res = parse_agy_text(clean_text.encode("utf-8"))
    assert clean_res.markers == ()
    assert clean_res.text == clean_text + "\n"


def test_markers_to_requests() -> None:
    markers = [
        Marker(message="主人公の名前", start=10, end=23),
        Marker(message="港の名称", start=30, end=41),
    ]
    reqs = markers_to_requests(markers, job_id="job-1", chapter_id="ch-001")
    assert len(reqs) == 2
    assert reqs[0] == {
        "type": "request",
        "job_id": "job-1",
        "chapter_id": "ch-001",
        "kind": "undefined_setting",
        "target": None,
        "message": "主人公の名前",
    }
    assert reqs[1] == {
        "type": "request",
        "job_id": "job-1",
        "chapter_id": "ch-001",
        "kind": "undefined_setting",
        "target": None,
        "message": "港の名称",
    }

    # 不正な job_id (job-x は common.schema.json の pattern ^job-[0-9]+$ に反する)
    with pytest.raises(SchemaError):
        markers_to_requests(markers, job_id="job-x", chapter_id="ch-001")


def test_splice_range() -> None:
    # 1. 元の範囲の末尾が \n の場合
    prefix = b"prefix line\n"
    orig_with_nl = b"original target\n"
    replacement = "new text line"
    suffix = b"suffix line\n"
    res1 = splice_range(prefix, orig_with_nl, replacement, suffix)
    assert res1 == b"prefix line\nnew text line\nsuffix line\n"

    # check_prefix_suffix が PASS
    chk1 = check_prefix_suffix(after=res1, prefix=prefix, suffix=suffix)
    assert chk1.status == "PASS"

    # 2. 元の範囲の末尾が \n なしの場合
    orig_no_nl = b"original target"
    res2 = splice_range(prefix, orig_no_nl, replacement, suffix)
    assert res2 == b"prefix line\nnew text linesuffix line\n"

    chk2 = check_prefix_suffix(after=res2, prefix=prefix, suffix=suffix)
    assert chk2.status == "PASS"

    # 3. 置き換えが日本語の場合（元の末尾空白が複数改行 \n\n の場合）
    orig_jp = "古い文章。\n\n".encode("utf-8")
    repl_jp = "新しい日本語の文章。"
    res3 = splice_range(prefix, orig_jp, repl_jp, suffix)
    assert res3 == prefix + "新しい日本語の文章。\n\n".encode("utf-8") + suffix

    chk3 = check_prefix_suffix(after=res3, prefix=prefix, suffix=suffix)
    assert chk3.status == "PASS"

    # 4. original_range が不正な UTF-8 の場合は ValueError
    with pytest.raises(ValueError, match="UTF-8"):
        splice_range(prefix, b"\xff\xfe invalid", replacement, suffix)
