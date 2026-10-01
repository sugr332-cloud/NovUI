"""Tests for novui.mountinfo."""

from pathlib import Path

import pytest

from novui.mountinfo import find_unexpected_mounts, parse_mountinfo

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "mountinfo_spike02.txt"


def test_spike02_overlay_not_detected_as_unexpected() -> None:
    # Phase 0 の overlay オプション誤検出の再現防止:
    # / の overlay の lowerdir に /var/home/... が含まれていても検出しない
    text = FIXTURE_PATH.read_text(encoding="utf-8")
    entries = parse_mountinfo(text)
    assert len(entries) > 0

    unexpected = find_unexpected_mounts(entries, allowed=["/workspace"])
    assert unexpected == []


def test_unexpected_mount_host_added() -> None:
    text = FIXTURE_PATH.read_text(encoding="utf-8")
    added_line = "9999 2101 0:99 /home/hankyuu1 /mnt/host rw,relatime - btrfs /dev/sda1 rw\n"
    entries = parse_mountinfo(text + added_line)

    unexpected = find_unexpected_mounts(entries, allowed=["/workspace"])
    assert len(unexpected) == 1
    assert unexpected[0].mount_id == 9999
    assert unexpected[0].mount_point == "/mnt/host"
    assert unexpected[0].root == "/home/hankyuu1"


def test_nested_allowed_vs_sibling_allowed() -> None:
    # allowed=["/workspace"] のとき /workspace/world (重ねマウント) は検出せず、
    # /workspace2 は検出する
    content = (
        "100 1 0:1 / /workspace/world rw,relatime - tmpfs tmpfs rw\n"
        "101 1 0:1 / /workspace2 rw,relatime - tmpfs tmpfs rw\n"
    )
    entries = parse_mountinfo(content)
    unexpected = find_unexpected_mounts(entries, allowed=["/workspace"])
    assert len(unexpected) == 1
    assert unexpected[0].mount_id == 101
    assert unexpected[0].mount_point == "/workspace2"


def test_octal_escape_restored() -> None:
    # mount_point や root に \040 などの8進エスケープを含む行が正しく復元される
    line = "102 1 0:1 /foo\\040bar /my\\040dir\\011tab\\012nl\\134bs rw - tmpfs tmpfs rw\n"
    entries = parse_mountinfo(line)
    assert len(entries) == 1
    assert entries[0].root == "/foo bar"
    assert entries[0].mount_point == "/my dir\ttab\nnl\\bs"


def test_parse_mountinfo_missing_separator_value_error() -> None:
    # " - " のない行で ValueError
    invalid_line = "2094 2101 0:35 /path /workspace rw,relatime btrfs /dev/sda rw"
    with pytest.raises(ValueError):
        parse_mountinfo(invalid_line)


def test_parse_mountinfo_insufficient_fields_value_error() -> None:
    # 前半フィールド数が足りない場合
    with pytest.raises(ValueError):
        parse_mountinfo("1 2 3 - a b c")
    # 後半フィールド数が足りない（1つしかない）場合
    with pytest.raises(ValueError):
        parse_mountinfo("1 2 3 4 5 6 - only_fstype")
