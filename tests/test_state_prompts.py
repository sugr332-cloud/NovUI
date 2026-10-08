"""Tests for prompts/claude/state_update.md and prompts/claude/state_patch.md (rendering and the missing-field rules)."""

import re

import pytest

from novui.prompt import PROMPTS_DIR, load_prompt_template

PATCH_VALUES = {
    "chapter_id": "ch-001",
    "target": "characters/C001.yaml",
    "scene_ids": "S1, S2",
    "summary_yaml": "type: summary\nchapter_id: ch-001\n",
    "base_hash": "sha256:" + "0" * 64,
    "pointer_hints": "関係：C002 → /relationships/0",
    "next_knowledge_id": "K002",
}
UPDATE_VALUES = {
    "chapter_id": "ch-001",
    "scene_ids": "S1, S2",
    "character_ids": "C001, C002",
    "foreshadow_ids": "F001",
}


def _placeholders(name: str) -> set[str]:
    return set(re.findall(r"\{\{(\w+)\}\}", (PROMPTS_DIR / "claude" / f"{name}.md").read_text(encoding="utf-8")))


def test_state_patch_prompt_placeholders_and_rendering() -> None:
    assert _placeholders("state_patch") == set(PATCH_VALUES)
    text = load_prompt_template("state_patch", **PATCH_VALUES)
    assert "{{" not in text and "}}}}" not in text
    assert "K002 から始め" in text and "`sha256:" + "0" * 64 + "`" in text
    assert "関係：C002 → /relationships/0" in text
    assert "type：`state_patch`" in text and "target：`characters/C001.yaml`" in text


def test_state_update_prompt_placeholders_and_rendering() -> None:
    assert _placeholders("state_update") == set(UPDATE_VALUES)
    text = load_prompt_template("state_update", **UPDATE_VALUES)
    assert "{{" not in text
    assert "type：`summary`" in text and "C001, C002" in text and "F001" in text


def test_prompts_fail_on_a_missing_value() -> None:
    values = dict(PATCH_VALUES)
    del values["base_hash"]
    with pytest.raises(ValueError, match="Unsubstituted"):
        load_prompt_template("state_patch", **values)


def test_state_patch_prompt_has_the_missing_field_section() -> None:
    text = load_prompt_template("state_patch", **PATCH_VALUES)
    header = "■ 対象ファイルに欄がない場合（重要）"
    assert text.count(header) == 1
    # between the pointer table and the allowed changes
    assert text.index("■ pointer の対応表") < text.index(header) < text.index("■ 書いてよい変更")
    section = text[text.index(header):text.index("■ 書いてよい変更")]

    assert "操作は「資料の対象ファイルに実在する場所」に対してだけ書けます。" in section
    assert "`-` による末尾への追加は、その配列が資料に実在する場合だけです。" in section
    # the structure is never created by the AI
    assert "`add /knowledge`" in section and "`add /relationships/0/changes`" in section
    assert "欄ごと作る" in section and "書いてはいけません" in section
    # the three conditions
    assert "* knowledge：対象ファイルに `knowledge:` の欄があるときだけ、knowledge を追加できます。" in section
    assert "* 関係の変化：その相手の relationships の要素に `changes:` の欄があるときだけ、changes に追加できます。" in section
    assert "* 呼び方の変化：address の相手の値が object で、かつ `changes:` の欄があるときだけ、changes に追加できます。" in section
    # omit what cannot be written, fall back to the test-only patch, and say what was omitted in reason
    assert "書けない部分は省き、書ける部分だけを提案してください。" in section
    assert "`test` 操作1件にしてください" in section
    assert "省いた内容は、reason に1文で書いてください" in section


def test_state_patch_prompt_conditions_are_on_the_allowed_change_bullets() -> None:
    text = load_prompt_template("state_patch", **PATCH_VALUES)
    allowed = text[text.index("■ 書いてよい変更"):]
    assert "* knowledge の追加（対象ファイルに `knowledge:` の欄があるときだけ）：" in allowed
    assert "相手との関係が既に relationships にあり、その要素に `changes:` の欄がある場合だけ" in allowed
    assert "資料の address の相手の値が object で、かつ `changes:` の欄がある場合だけ" in allowed
    # the unconditional wording of the previous version is gone
    assert "* knowledge の追加：" not in allowed
    assert "相手との関係が既に relationships にある場合は" not in allowed
    assert "object（default と changes を持つ形）なら" not in allowed
    # the allowed forms themselves are unchanged
    assert '"/relationships/<添字>/changes/-"' in allowed and '"/address/C002/changes/-"' in allowed
    assert '"/knowledge/-"' in allowed
