"""Tests for novui.paths module."""

from pathlib import Path
import pytest

from novui.paths import PathError, ensure_within, is_safe_relpath


def test_is_safe_relpath_valid() -> None:
    assert is_safe_relpath("chapters/ch-001/draft.md") is True
    assert is_safe_relpath("a/b/c.txt") is True
    assert is_safe_relpath("file.md") is True


def test_is_safe_relpath_invalid() -> None:
    assert is_safe_relpath("") is False
    assert is_safe_relpath("/etc/passwd") is False
    assert is_safe_relpath("../x") is False
    assert is_safe_relpath("a/../b") is False
    assert is_safe_relpath("a//b") is False
    assert is_safe_relpath("a/") is False
    assert is_safe_relpath("./a") is False
    assert is_safe_relpath("a\\b") is False
    assert is_safe_relpath("a\x00b") is False


def test_ensure_within_valid(tmp_path: Path) -> None:
    root = (tmp_path / "root").resolve()
    root.mkdir()
    child = root / "subdir" / "file.txt"
    resolved = ensure_within(root, child)
    assert resolved == child.resolve()
    assert ensure_within(root, root) == root


def test_ensure_within_outside(tmp_path: Path) -> None:
    root = (tmp_path / "root").resolve()
    root.mkdir()
    outside = root / ".." / "outside.txt"
    with pytest.raises(PathError):
        ensure_within(root, outside)


def test_ensure_within_symlink_escape(tmp_path: Path) -> None:
    root = (tmp_path / "root").resolve()
    root.mkdir()
    outside = (tmp_path / "outside").resolve()
    outside.mkdir()
    target_file = outside / "secret.txt"
    target_file.write_text("secret")

    symlink_in_root = root / "leak_link"
    symlink_in_root.symlink_to(outside)

    with pytest.raises(PathError):
        ensure_within(root, symlink_in_root / "secret.txt")


def test_ensure_within_non_absolute_root(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        ensure_within(Path("relative/root"), tmp_path / "target")
