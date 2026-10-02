"""Tests for novui.prompt module."""

import hashlib
from pathlib import Path
import pytest

from novui.prompt import (
    PREAMBLE,
    BuiltPrompt,
    ContextEntry,
    build_agy_prompt,
)


def test_build_agy_prompt_basic(tmp_path: Path) -> None:
    f1 = tmp_path / "world" / "setting.yaml"
    f1.parent.mkdir(parents=True)
    c1 = "location: 港町\n"
    f1.write_text(c1, encoding="utf-8")

    f2 = tmp_path / "chapters" / "0001" / "draft.md"
    f2.parent.mkdir(parents=True)
    c2 = "# 1章\n草稿です。\n"
    f2.write_text(c2, encoding="utf-8")

    instruction = "主人公が名乗る場面を書いてください。"
    built = build_agy_prompt(
        worktree=tmp_path,
        context_paths=["world/setting.yaml", "chapters/0001/draft.md"],
        instruction=instruction,
    )

    assert isinstance(built, BuiltPrompt)
    assert built.size_bytes == len(built.text.encode("utf-8"))
    assert len(built.context) == 2

    h1 = hashlib.sha256(c1.encode("utf-8")).hexdigest().lower()
    h2 = hashlib.sha256(c2.encode("utf-8")).hexdigest().lower()
    assert built.context[0] == ContextEntry(path="world/setting.yaml", sha256=f"sha256:{h1}")
    assert built.context[1] == ContextEntry(path="chapters/0001/draft.md", sha256=f"sha256:{h2}")

    expected = (
        f"{PREAMBLE}\n\n"
        f"【ファイル：world/setting.yaml】\n{c1}\n\n"
        f"【ファイル：chapters/0001/draft.md】\n{c2}\n\n"
        f"【指示】\n{instruction}"
    )
    assert built.text == expected


def test_build_agy_prompt_empty_context(tmp_path: Path) -> None:
    built = build_agy_prompt(tmp_path, [], "指示文")
    assert len(built.context) == 0
    assert built.text == f"{PREAMBLE}\n\n【指示】\n指示文"
    assert built.size_bytes == len(built.text.encode("utf-8"))


def test_build_agy_prompt_validation(tmp_path: Path) -> None:
    # 1. 空の instruction
    with pytest.raises(ValueError, match="instruction cannot be empty"):
        build_agy_prompt(tmp_path, [], "")
    with pytest.raises(ValueError, match="instruction cannot be empty"):
        build_agy_prompt(tmp_path, [], "   \n ")

    # 2. NUL文字 in instruction
    with pytest.raises(ValueError, match="NUL"):
        build_agy_prompt(tmp_path, [], "hello\x00world")

    # 3. 不正な相対パス
    with pytest.raises(ValueError, match="Unsafe relative path"):
        build_agy_prompt(tmp_path, ["../outside.txt"], "指示")
    with pytest.raises(ValueError, match="Unsafe relative path"):
        build_agy_prompt(tmp_path, ["/abs/path.txt"], "指示")

    # 4. ディレクトリが渡された場合
    d = tmp_path / "somedir"
    d.mkdir()
    with pytest.raises(ValueError, match="not a regular file"):
        build_agy_prompt(tmp_path, ["somedir"], "指示")

    # 5. ファイルが存在しない場合
    with pytest.raises(ValueError, match="not a regular file"):
        build_agy_prompt(tmp_path, ["nonexistent.txt"], "指示")

    # 6. 非 UTF-8 ファイル
    bad_file = tmp_path / "bad.bin"
    bad_file.write_bytes(b"\xff\xfe\x00\x00")
    with pytest.raises(ValueError, match="not valid UTF-8"):
        build_agy_prompt(tmp_path, ["bad.bin"], "指示")
