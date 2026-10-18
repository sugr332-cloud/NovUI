from pathlib import Path

import pytest

from novui.chapters import ChapterError
from novui.textformat import read_text_format
from novui.yamlio import dumps_yaml


def _project(tmp_path: Path, **extra: object) -> Path:
    doc = {"format_version": 1, "work_key": "w", "title": "t", **extra}
    (tmp_path / "project.yaml").write_text(dumps_yaml(doc), encoding="utf-8")
    return tmp_path


def test_default_novel(tmp_path: Path) -> None:
    assert read_text_format(_project(tmp_path)) == "novel"


@pytest.mark.parametrize("fmt", ["novel", "script"])
def test_explicit(tmp_path: Path, fmt: str) -> None:
    assert read_text_format(_project(tmp_path, text_format=fmt)) == fmt


def test_invalid_value(tmp_path: Path) -> None:
    with pytest.raises(ChapterError, match="invalid"):
        read_text_format(_project(tmp_path, text_format="game"))


def test_missing_project(tmp_path: Path) -> None:
    with pytest.raises(ChapterError, match="not found"):
        read_text_format(tmp_path)
