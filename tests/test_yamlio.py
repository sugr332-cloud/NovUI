"""Tests for novui.yamlio module."""

from datetime import datetime
from pathlib import Path
import pytest
import yaml

from novui.yamlio import YamlError, dumps_yaml, load_yaml, loads_yaml, write_yaml_atomic


def test_timestamp_not_implicitly_converted() -> None:
    # 日時の暗黙変換をせず、文字列のまま読み込まれる
    data = loads_yaml("a: 2026-10-01T20:10:00+09:00")
    assert isinstance(data["a"], str)
    assert data["a"] == "2026-10-01T20:10:00+09:00"


def test_strict_boolean_conversion() -> None:
    # yes, no, on は文字列、true, False は bool
    text = (
        "b: no\n"
        "c: yes\n"
        "d: on\n"
        "e: true\n"
        "f: False\n"
        "g: TRUE\n"
        "h: false\n"
    )
    data = loads_yaml(text)
    assert data["b"] == "no"
    assert isinstance(data["b"], str)
    assert data["c"] == "yes"
    assert isinstance(data["c"], str)
    assert data["d"] == "on"
    assert isinstance(data["d"], str)
    assert data["e"] is True
    assert data["f"] is False
    assert data["g"] is True
    assert data["h"] is False


def test_duplicate_keys_and_syntax_error() -> None:
    # トップレベルの重複キー
    with pytest.raises(YamlError):
        loads_yaml("x: 1\nx: 2")

    # 入れ子の mapping 内の重複キー
    with pytest.raises(YamlError):
        loads_yaml("parent:\n  child: 1\n  child: 2")

    # 構文エラー
    with pytest.raises(YamlError):
        loads_yaml("key: [unterminated list")


def test_load_yaml_file_and_bom_error(tmp_path: Path) -> None:
    valid_file = tmp_path / "valid.yaml"
    valid_file.write_text("hello: world\n", encoding="utf-8")
    assert load_yaml(valid_file) == {"hello": "world"}

    # BOM 付きファイルで YamlError
    bom_file = tmp_path / "bom.yaml"
    bom_file.write_bytes(b"\xef\xbb\xbfhello: world\n")
    with pytest.raises(YamlError) as exc_info:
        load_yaml(bom_file)
    assert "BOM" in str(exc_info.value)

    # 不正な UTF-8 で YamlError
    bad_utf8 = tmp_path / "bad.yaml"
    bad_utf8.write_bytes(b"\xff\xfehello")
    with pytest.raises(YamlError):
        load_yaml(bad_utf8)


def test_dumps_and_loads_roundtrip_unicode() -> None:
    data = {
        "title": "第二章　森の追跡",
        "description": "木々の隙間から光が射し込む。",
        "items": ["短剣", "保存食", "水袋"],
    }
    dumped = dumps_yaml(data)
    assert "\\u" not in dumped
    assert "第二章　森の追跡" in dumped

    loaded = loads_yaml(dumped)
    assert loaded == data


def test_global_safeloader_not_modified() -> None:
    # 専用 Loader を使用した後でも、標準 SafeLoader の動作が壊れていないことを確認
    text = "a: 2026-10-01T20:10:00+09:00\nb: yes"
    standard_data = yaml.safe_load(text)
    assert isinstance(standard_data["a"], datetime)
    assert standard_data["b"] is True


def test_write_yaml_atomic_and_rollback_on_failure(tmp_path: Path) -> None:
    target = tmp_path / "data.yaml"
    initial_data = {"version": 1}
    write_yaml_atomic(target, initial_data)
    assert load_yaml(target) == initial_data

    # 上書き書き込み
    updated_data = {"version": 2}
    write_yaml_atomic(target, updated_data)
    assert load_yaml(target) == updated_data

    # dumps_yaml に表現できないオブジェクトを渡して失敗させる
    class Unserializable:
        pass

    with pytest.raises(YamlError):
        write_yaml_atomic(target, {"bad": Unserializable()})

    # 元のファイルが残り、一時ファイル（.tmp_*）が残っていないこと
    assert load_yaml(target) == updated_data
    remaining_temp_files = list(tmp_path.glob(".tmp_*"))
    assert remaining_temp_files == []
