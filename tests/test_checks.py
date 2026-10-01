"""Tests for novui.checks module."""

import pytest

from novui.checks import (
    check_allowed_paths,
    check_append_only,
    check_char_range,
    check_cli_output,
    check_ignored_unchanged,
    check_prefix_suffix,
    compare_hash_records,
    count_chars,
)
from novui.gitinspect import PathChange


def test_check_cli_output() -> None:
    # exit 0 かつ空 stdout が FAIL
    r1 = check_cli_output(0, b"")
    assert r1.status == "FAIL"
    assert "empty_stdout" in r1.details

    # stdout が空白と改行だけでも FAIL
    r2 = check_cli_output(0, b"   \n  \t  \n")
    assert r2.status == "FAIL"
    assert "empty_stdout" in r2.details

    # exit 2 で FAIL
    r3 = check_cli_output(2, b"some output")
    assert r3.status == "FAIL"
    assert "exit_code=2" in r3.details

    # exit 0 かつ内容ありで PASS
    r4 = check_cli_output(0, b"valid response")
    assert r4.status == "PASS"
    assert r4.details == ()


def test_check_allowed_paths() -> None:
    allowed = ["chapters/ch-001/draft.md", "chapters/ch-001/requests.yaml"]

    # 許可内だけで PASS
    changes_ok = [
        PathChange(status=" M", path="chapters/ch-001/draft.md"),
        PathChange(status="??", path="chapters/ch-001/requests.yaml"),
    ]
    r1 = check_allowed_paths(changes_ok, allowed)
    assert r1.status == "PASS"

    # 許可外を含むと FAIL で detail にそのパス
    changes_bad = [
        PathChange(status=" M", path="chapters/ch-001/draft.md"),
        PathChange(status=" M", path="world/world.md"),
        PathChange(status="??", path="outside.txt"),
    ]
    r2 = check_allowed_paths(changes_bad, allowed)
    assert r2.status == "FAIL"
    assert r2.details == ("outside.txt", "world/world.md")

    # rename の orig_path が許可外なら FAIL
    changes_rename = [
        PathChange(
            status="R ",
            path="chapters/ch-001/draft.md",
            orig_path="secret/leaked.md",
        )
    ]
    r3 = check_allowed_paths(changes_rename, allowed)
    assert r3.status == "FAIL"
    assert "secret/leaked.md" in r3.details

    # allowed に安全でないパスがあれば ValueError
    with pytest.raises(ValueError):
        check_allowed_paths([], ["../bad_relpath"])


def test_check_ignored_unchanged() -> None:
    before = {"file1.tmp", "dir/file2.log"}
    # 変化なし
    assert check_ignored_unchanged(before, before).status == "PASS"

    # 増加
    r_add = check_ignored_unchanged(before, before | {"file3.tmp"})
    assert r_add.status == "FAIL"
    assert "added: file3.tmp" in r_add.details

    # 減少
    r_rem = check_ignored_unchanged(before, {"file1.tmp"})
    assert r_rem.status == "FAIL"
    assert "removed: dir/file2.log" in r_rem.details


def test_check_append_only_table() -> None:
    # 判定表の全行を網羅
    # 1. before None, after None -> PASS
    assert check_append_only(None, None).status == "PASS"

    # 2. before None, after あり -> PASS
    assert check_append_only(None, b"initial content").status == "PASS"

    # 3. before あり, after None -> FAIL (detail 'deleted')
    r3 = check_append_only(b"some content", None)
    assert r3.status == "FAIL"
    assert r3.details == ("deleted",)

    # 4. after == before -> PASS
    assert check_append_only(b"hello\n", b"hello\n").status == "PASS"

    # 5. after starts with before, before is empty -> PASS
    assert check_append_only(b"", b"appended").status == "PASS"

    # 6. after starts with before, before ends with \n -> PASS
    assert check_append_only(b"line1\n", b"line1\nline2\n").status == "PASS"

    # 7. after starts with before, but before does not end with \n -> FAIL (detail 'last_line_modified')
    r7 = check_append_only(b"line1", b"line1appended")
    assert r7.status == "FAIL"
    assert r7.details == ("last_line_modified",)

    # 8. それ以外 -> FAIL (detail 'existing_content_modified')
    r8 = check_append_only(b"line1\nline2\n", b"lineX\nline2\nline3\n")
    assert r8.status == "FAIL"
    assert r8.details == ("existing_content_modified",)


def test_check_prefix_suffix() -> None:
    prefix = b"BEGIN_CHAPTER\n"
    suffix = b"\nEND_CHAPTER\n"

    # 本文の途中だけ変えた場合 PASS
    after = prefix + b"Middle modified text" + suffix
    assert check_prefix_suffix(after, prefix, suffix).status == "PASS"

    # prefix 側の1バイト変更 -> FAIL
    bad_prefix = b"bEGIN_CHAPTER\n" + b"Middle" + suffix
    assert check_prefix_suffix(bad_prefix, prefix, suffix).status == "FAIL"

    # suffix 側の1バイト変更 -> FAIL
    bad_suffix = prefix + b"Middle" + b"\nEND_CHAPTER."
    assert check_prefix_suffix(bad_suffix, prefix, suffix).status == "FAIL"

    # after が短すぎる場合 -> FAIL
    too_short = b"short"
    assert check_prefix_suffix(too_short, prefix, suffix).status == "FAIL"


def test_compare_hash_records_reproduction_and_diffs() -> None:
    # Phase 0 の見出し行の誤判定の再現テスト：
    # 同じファイル群（config, hooks）のハッシュ記録を dict として比較
    before = {
        "common:config": "sha256:1111",
        "common:hooks/pre-commit": "sha256:2222",
    }
    after_same = {
        "common:config": "sha256:1111",
        "common:hooks/pre-commit": "sha256:2222",
    }
    # 見出し行などに関係なく、ファイルハッシュが一致していれば PASS
    assert compare_hash_records(before, after_same).status == "PASS"

    # 値の変更 -> FAIL (changed)
    after_changed = {
        "common:config": "sha256:9999",
        "common:hooks/pre-commit": "sha256:2222",
    }
    r_chg = compare_hash_records(before, after_changed)
    assert r_chg.status == "FAIL"
    assert r_chg.details == ("changed: common:config",)

    # キーの欠落 -> FAIL (missing_after)
    after_missing = {
        "common:config": "sha256:1111",
    }
    r_mis = compare_hash_records(before, after_missing)
    assert r_mis.status == "FAIL"
    assert r_mis.details == ("missing_after: common:hooks/pre-commit",)

    # キーの追加 -> FAIL (added_after)
    after_added = {
        "common:config": "sha256:1111",
        "common:hooks/pre-commit": "sha256:2222",
        "common:hooks/post-commit": "sha256:3333",
    }
    r_add = compare_hash_records(before, after_added)
    assert r_add.status == "FAIL"
    assert r_add.details == ("added_after: common:hooks/post-commit",)


def test_count_chars() -> None:
    assert count_chars("あい う\n　え\t") == 4
    assert count_chars("   \n\t　 ") == 0
    assert count_chars("Hello, World!") == 12


def test_check_char_range() -> None:
    text = "あいうえお"  # 5 chars
    # 範囲内 PASS
    r1 = check_char_range(text, 3, 10)
    assert r1.status == "PASS"
    assert r1.details == ("count=5 range=3-10",)

    # 境界値 PASS
    assert check_char_range(text, 5, 5).status == "PASS"
    assert check_char_range(text, 1, 5).status == "PASS"
    assert check_char_range(text, 5, 10).status == "PASS"

    # 範囲外 WARNING (FAIL ではない)
    r2 = check_char_range(text, 6, 10)
    assert r2.status == "WARNING"
    assert r2.details == ("count=5 range=6-10",)

    r3 = check_char_range(text, 1, 4)
    assert r3.status == "WARNING"
    assert r3.details == ("count=5 range=1-4",)

    # 不正引数で ValueError
    with pytest.raises(ValueError):
        check_char_range(text, -1, 10)
    with pytest.raises(ValueError):
        check_char_range(text, 10, 5)
