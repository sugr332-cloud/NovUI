"""Tests for novui.prompt module."""

import hashlib
from pathlib import Path
import pytest

from novui.prompt import (
    PREAMBLE,
    BuiltPrompt,
    ContextEntry,
    build_agy_prompt,
    build_claude_prompt,
    load_prompt_template,
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


def test_load_prompt_template() -> None:
    # preamble: 置き換えなしで読み込める
    preamble = load_prompt_template("preamble")
    assert "NovUI" in preamble
    assert "{{" not in preamble

    # plan: {{chapter_id}} の置換
    plan_prompt = load_prompt_template("plan", chapter_id="ch-042")
    assert "ch-042" in plan_prompt
    assert "{{chapter_id}}" not in plan_prompt
    assert "{{" not in plan_prompt

    # {{ が残っていると ValueError
    with pytest.raises(ValueError, match="Unsubstituted template placeholder"):
        load_prompt_template("plan")

    # 不正な name
    with pytest.raises(ValueError, match="Invalid template name"):
        load_prompt_template("Plan")
    with pytest.raises(ValueError, match="Invalid template name"):
        load_prompt_template("plan-test")
    with pytest.raises(ValueError, match="Invalid template name"):
        load_prompt_template("../secret")

    # 存在しないテンプレート
    with pytest.raises(FileNotFoundError):
        load_prompt_template("nonexistent_template_xyz")

    # group：agy のテンプレート、不正な group
    draft_prompt = load_prompt_template(
        "draft", group="agy", chapter_id="ch-001", scene_ids="S1", scene_count="1",
        scene_marker_lines="<!-- scene: S1 -->", min_chars="10", max_chars="20",
    )
    assert draft_prompt.startswith("章 ch-001 の本文を書いてください。")
    assert "{{" not in draft_prompt
    with pytest.raises(FileNotFoundError):
        load_prompt_template("draft")  # prompts/claude/draft.md はない
    with pytest.raises(ValueError, match="Invalid template group"):
        load_prompt_template("draft", group="../claude")


def test_build_claude_prompt(tmp_path: Path) -> None:
    f1 = tmp_path / "world" / "setting.yaml"
    f1.parent.mkdir(parents=True)
    c1 = "location: 港町\n"
    f1.write_text(c1, encoding="utf-8")

    task_text = "【作業】章 ch-001 の執筆計画（plan）を作ってください。\n詳細..."
    built = build_claude_prompt(
        root=tmp_path,
        context_paths=["world/setting.yaml"],
        task_text=task_text,
    )

    preamble = load_prompt_template("preamble")
    assert isinstance(built, BuiltPrompt)
    assert built.size_bytes == len(built.text.encode("utf-8"))
    assert len(built.context) == 1
    assert built.context[0].path == "world/setting.yaml"

    # 先頭が preamble.md、末尾が task_text（【指示】は含まれない）
    assert built.text.startswith(preamble)
    assert built.text.endswith(task_text)
    assert "【指示】" not in built.text
    assert "【作業】" in built.text

    # バリデーション
    with pytest.raises(ValueError, match="task_text cannot be empty"):
        build_claude_prompt(tmp_path, [], "")
    with pytest.raises(ValueError, match="NUL"):
        build_claude_prompt(tmp_path, [], "task\x00invalid")

