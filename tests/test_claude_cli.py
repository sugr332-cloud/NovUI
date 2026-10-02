"""Tests for novui.claude_cli module."""

import pytest

from novui.claude_cli import claude_args


def test_claude_args_full_match() -> None:
    schema_str = '{"type":"object"}'
    model_name = "claude-sonnet-4-6"
    args = claude_args(model=model_name, json_schema=schema_str)

    expected = [
        "claude",
        "-p",
        "--tools",
        "Read",
        "--permission-prompts",
        "none",
        "--no-session-persistence",
        "--safe-mode",
        "--restricted",
        "--strict-mcp-config",
        "--model",
        model_name,
        "--output-format",
        "json",
        "--json-schema",
        schema_str,
    ]
    assert args == expected
    assert "--dangerously-skip-permissions" not in args
    assert "--permission-mode" not in args


def test_claude_args_empty_values_raise_value_error() -> None:
    schema_str = '{"type":"object"}'

    # model が空
    with pytest.raises(ValueError, match="model"):
        claude_args(model="", json_schema=schema_str)

    with pytest.raises(ValueError, match="model"):
        claude_args(model="   ", json_schema=schema_str)

    # json_schema が空
    with pytest.raises(ValueError, match="json_schema"):
        claude_args(model="claude-sonnet-4-6", json_schema="")

    with pytest.raises(ValueError, match="json_schema"):
        claude_args(model="claude-sonnet-4-6", json_schema="   ")
