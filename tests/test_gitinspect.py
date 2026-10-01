"""Tests for novui.gitinspect."""

from pathlib import Path
import subprocess

import pytest

from novui.checks import compare_hash_records, check_ignored_unchanged
from novui.gitinspect import (
    GitDirs,
    GitError,
    PathChange,
    get_changes,
    get_ignored,
    git_protection_targets,
    hash_targets,
    parse_porcelain_z,
    resolve_git_dirs,
    run_git,
)


def _init_test_repo(path: Path) -> Path:
    """Helper to initialize an isolated git repository with test config."""
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "-C", str(path), "init", "-b", "main"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(path), "config", "user.name", "test"], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(path), "config", "user.email", "test@example.invalid"],
        check=True,
        capture_output=True,
    )
    return path


def test_parse_porcelain_z() -> None:
    # rename ('R ' と orig_path)、'??'、'!!'、日本語ファイル名
    raw = (
        b"R  chapters/\xe7\xac\xac\xe4\xba\x8c\xe7\xab\xa0.md\0chapters/\xe7\xac\xac\xe4\xb8\x80\xe7\xab\xa0.md\0"
        b"?? new_file.txt\0"
        b"!! ignored.log\0"
        b"?? \xe7\xac\xac\xe4\xb8\x89\xe7\xab\xa0.md\0"
    )
    changes = parse_porcelain_z(raw)
    assert len(changes) == 4

    assert changes[0] == PathChange(
        status="R ",
        path="chapters/第二章.md",
        orig_path="chapters/第一章.md",
    )
    assert changes[1] == PathChange(
        status="??",
        path="new_file.txt",
        orig_path=None,
    )
    assert changes[2] == PathChange(
        status="!!",
        path="ignored.log",
        orig_path=None,
    )
    assert changes[3] == PathChange(
        status="??",
        path="第三章.md",
        orig_path=None,
    )

    # 空データ
    assert parse_porcelain_z(b"") == []

    # 壊れた入力で ValueError
    with pytest.raises(ValueError):
        # rename なのに orig_path がない
        parse_porcelain_z(b"R  dest.txt\0")

    with pytest.raises(ValueError):
        # 4バイト未満
        parse_porcelain_z(b"abc")

    with pytest.raises(ValueError):
        # 2文字目の区切りが空白でない
        parse_porcelain_z(b"M-invalid_separator\0")

    with pytest.raises(ValueError):
        # ステータスが ASCII でない
        parse_porcelain_z(b"\xff\xff invalid_status\0")


def test_get_changes_japanese_filename(tmp_path: Path) -> None:
    repo = _init_test_repo(tmp_path / "repo")
    ch_dir = repo / "chapters"
    ch_dir.mkdir()
    target_file = ch_dir / "第一章.md"
    target_file.write_text("第1章の内容", encoding="utf-8")

    changes = get_changes(repo)
    assert len(changes) == 1
    # 引用符やエスケープなしのパス
    assert changes[0].status == "??"
    assert changes[0].path == "chapters/第一章.md"
    assert changes[0].orig_path is None


def test_get_ignored_with_subdirs_and_japanese(tmp_path: Path) -> None:
    repo = _init_test_repo(tmp_path / "repo")
    (repo / ".gitignore").write_text("output/\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", ".gitignore"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-m", "init"], check=True, capture_output=True)

    out_dir = repo / "output"
    out_dir.mkdir()
    (out_dir / "first.log").write_text("log1", encoding="utf-8")

    before_ignored = get_ignored(repo)
    assert before_ignored == {"output/first.log"}

    # サブディレクトリ内のファイルと日本語ファイル名を追加
    sub_dir = out_dir / "sub"
    sub_dir.mkdir()
    (sub_dir / "ログ.txt").write_text("日本語ログ", encoding="utf-8")

    after_ignored = get_ignored(repo)
    assert after_ignored == {"output/first.log", "output/sub/ログ.txt"}

    # check_ignored_unchanged と連携して FAIL になることを確認
    res = check_ignored_unchanged(before_ignored, after_ignored)
    assert res.status == "FAIL"
    assert "added: output/sub/ログ.txt" in res.details


def test_resolve_git_dirs_with_chdir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Phase 0 の相対パス誤りの再現テスト:
    # monkeypatch.chdir で別ディレクトリに移動しても絶対パスが正しく解決される
    parent = _init_test_repo(tmp_path / "parent")
    (parent / "README.md").write_text("root", encoding="utf-8")
    subprocess.run(["git", "-C", str(parent), "add", "README.md"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(parent), "commit", "-m", "init"], check=True, capture_output=True)

    wt = tmp_path / "worktree1"
    subprocess.run(
        ["git", "-C", str(parent), "worktree", "add", str(wt), "-b", "wt1"],
        check=True,
        capture_output=True,
    )

    other_dir = tmp_path / "somewhere_else"
    other_dir.mkdir()
    monkeypatch.chdir(other_dir)

    dirs = resolve_git_dirs(wt)
    assert dirs.git_dir.is_absolute()
    assert dirs.common_dir.is_absolute()

    expected_git_dir = (parent / ".git" / "worktrees" / "worktree1").resolve()
    expected_common_dir = (parent / ".git").resolve()
    assert dirs.git_dir.resolve() == expected_git_dir
    assert dirs.common_dir.resolve() == expected_common_dir


def test_git_protection_targets_and_main_repo_error(tmp_path: Path) -> None:
    parent = _init_test_repo(tmp_path / "parent")
    (parent / "README.md").write_text("root", encoding="utf-8")
    subprocess.run(["git", "-C", str(parent), "add", "README.md"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(parent), "commit", "-m", "init"], check=True, capture_output=True)

    # 本体（.git がディレクトリ）に対しては ValueError
    with pytest.raises(ValueError):
        git_protection_targets(parent)

    wt = tmp_path / "worktree1"
    subprocess.run(
        ["git", "-C", str(parent), "worktree", "add", str(wt), "-b", "wt1"],
        check=True,
        capture_output=True,
    )

    # hooks にファイルを配置
    hooks_dir = parent / ".git" / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    (hooks_dir / "pre-commit").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")

    targets = git_protection_targets(wt)
    assert "worktree:.git" in targets
    assert "common:config" in targets
    assert "common:hooks/pre-commit" in targets
    assert "gitdir:gitdir" in targets
    assert "gitdir:commondir" in targets
    assert "gitdir:HEAD" in targets


def test_hash_targets_combined_with_compare_hash_records(tmp_path: Path) -> None:
    parent = _init_test_repo(tmp_path / "parent")
    (parent / "README.md").write_text("root", encoding="utf-8")
    subprocess.run(["git", "-C", str(parent), "add", "README.md"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(parent), "commit", "-m", "init"], check=True, capture_output=True)

    wt = tmp_path / "worktree1"
    subprocess.run(
        ["git", "-C", str(parent), "worktree", "add", str(wt), "-b", "wt1"],
        check=True,
        capture_output=True,
    )

    targets1 = git_protection_targets(wt)
    hashes1 = hash_targets(targets1)

    # get_changes と get_ignored を実行しても、保護対象のハッシュは不変（PASS）
    get_changes(wt)
    get_ignored(wt)
    targets2 = git_protection_targets(wt)
    hashes2 = hash_targets(targets2)
    res_same = compare_hash_records(hashes1, hashes2)
    assert res_same.status == "PASS"

    # hooks に1ファイル追加すると added_after で FAIL
    hooks_dir = parent / ".git" / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    (hooks_dir / "post-commit").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    targets3 = git_protection_targets(wt)
    hashes3 = hash_targets(targets3)
    res_hook = compare_hash_records(hashes2, hashes3)
    assert res_hook.status == "FAIL"
    assert "added_after: common:hooks/post-commit" in res_hook.details

    # worktree の .git 参照ファイルを書き換えると changed で FAIL
    dot_git = wt / ".git"
    dot_git.write_text("gitdir: /altered/path\n", encoding="utf-8")
    hashes4 = hash_targets(targets3)
    res_altered = compare_hash_records(hashes3, hashes4)
    assert res_altered.status == "FAIL"
    assert "changed: worktree:.git" in res_altered.details


def test_run_git_relative_path_error() -> None:
    with pytest.raises(ValueError):
        run_git(Path("relative/path"), "status")
