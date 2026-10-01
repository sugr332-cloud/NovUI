"""Tests for novui.claude_cli module."""

import pytest

from novui.claude_cli import claude_args


def test_claude_args_without_json_schema() -> None:
    args = claude_args(model="opus", output_format="text")
    expected = [
        "claude",
        "-p",
        "--tools",
        "Read",
        "--permission-prompts",
        "none",
        "--no-session-persistence",
        "--model",
        "opus",
        "--output-format",
        "text",
    ]
    assert args == expected
    assert "--dangerously-skip-permissions" not in args
    assert "--permission-mode" not in args


def test_claude_args_with_json_schema() -> None:
    schema_str = '{"type":"object"}'
    args = claude_args(model="sonnet", output_format="json", json_schema=schema_str)
    expected = [
        "claude",
        "-p",
        "--tools",
        "Read",
        "--permission-prompts",
        "none",
        "--no-session-persistence",
        "--model",
        "sonnet",
        "--output-format",
        "json",
        "--json-schema",
        schema_str,
    ]
    assert args == expected


def test_claude_args_invalid_output_format() -> None:
    with pytest.raises(ValueError):
        claude_args(model="opus", output_format="xml")
