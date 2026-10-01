"""Tests for novui.claude_output module."""

import json
from pathlib import Path
import pytest

from novui.claude_output import (
    CLAUDE_OUTPUT_TYPES,
    ClaudeOutputError,
    OutputRetryExhausted,
    parse_claude_output,
    run_with_output_retry,
)
from novui.yamlio import load_yaml

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "valid"


@pytest.mark.parametrize("out_type", sorted(CLAUDE_OUTPUT_TYPES))
def test_valid_json_parsed_for_all_types(out_type: str) -> None:
    doc = load_yaml(FIXTURES_DIR / f"{out_type}.yaml")
    raw = json.dumps(doc, ensure_ascii=False).encode("utf-8")
    parsed = parse_claude_output(raw, out_type)
    assert parsed == doc


def test_kind_failures() -> None:
    # 1. 不正な UTF-8 バイト (decode)
    with pytest.raises(ClaudeOutputError) as exc_info:
        parse_claude_output(b"\xff\xfe\x00\x00", "plan")
    assert exc_info.value.kind == "decode"

    # 2. ```json ... ``` で囲んだ JSON (json) - フェンスを除去せずエラーにする
    valid_plan = load_yaml(FIXTURES_DIR / "plan.yaml")
    json_str = json.dumps(valid_plan)
    fenced = f"```json\n{json_str}\n```".encode("utf-8")
    with pytest.raises(ClaudeOutputError) as exc_info:
        parse_claude_output(fenced, "plan")
    assert exc_info.value.kind == "json"

    # 3. 前置きの文章付き (json)
    prefixed = f"Here is the plan:\n{json_str}".encode("utf-8")
    with pytest.raises(ClaudeOutputError) as exc_info:
        parse_claude_output(prefixed, "plan")
    assert exc_info.value.kind == "json"

    # 4. 配列 (type)
    with pytest.raises(ClaudeOutputError) as exc_info:
        parse_claude_output(b"[]", "plan")
    assert exc_info.value.kind == "type"

    # 5. type 違い (type)
    mismatched = json.dumps({"type": "summary"}).encode("utf-8")
    with pytest.raises(ClaudeOutputError) as exc_info:
        parse_claude_output(mismatched, "plan")
    assert exc_info.value.kind == "type"

    # 6. 必須欠落 (schema)
    missing_fields = json.dumps({"type": "plan"}).encode("utf-8")
    with pytest.raises(ClaudeOutputError) as exc_info:
        parse_claude_output(missing_fields, "plan")
    assert exc_info.value.kind == "schema"

    # 7. semantics 違反 (semantic)
    invalid_sem_plan = dict(valid_plan)
    invalid_sem_plan["target_chars"] = {"min": 5000, "max": 3000}  # min > max
    raw_sem = json.dumps(invalid_sem_plan).encode("utf-8")
    with pytest.raises(ClaudeOutputError) as exc_info:
        parse_claude_output(raw_sem, "plan")
    assert exc_info.value.kind == "semantic"


def test_unknown_expected_type_raises_value_error() -> None:
    with pytest.raises(ValueError):
        parse_claude_output(b"{}", "unregistered_type")


def test_run_with_output_retry_success_on_second_attempt() -> None:
    valid_plan = load_yaml(FIXTURES_DIR / "plan.yaml")
    valid_bytes = json.dumps(valid_plan).encode("utf-8")

    responses = [
        b"not json",       # attempt 1: json error
        valid_bytes,      # attempt 2: success
    ]

    def mock_call(attempt: int) -> bytes:
        return responses[attempt - 1]

    result, attempts = run_with_output_retry(mock_call, "plan", max_retries=2)
    assert result == valid_plan
    assert len(attempts) == 2
    assert attempts[0].attempt == 1
    assert attempts[0].ok is False
    assert attempts[0].error_kind == "json"
    assert attempts[1].attempt == 2
    assert attempts[1].ok is True
    assert attempts[1].error_kind is None


def test_run_with_output_retry_exhausted() -> None:
    responses = [
        b"bad 1",
        b"bad 2",
        b"bad 3",
    ]

    def mock_call(attempt: int) -> bytes:
        return responses[attempt - 1]

    with pytest.raises(OutputRetryExhausted) as exc_info:
        run_with_output_retry(mock_call, "plan", max_retries=2)

    attempts = exc_info.value.attempts
    assert len(attempts) == 3
    for a in attempts:
        assert a.ok is False
        assert a.error_kind == "json"


def test_run_with_output_retry_zero_retries() -> None:
    calls = []

    def mock_call(attempt: int) -> bytes:
        calls.append(attempt)
        return b"bad"

    with pytest.raises(OutputRetryExhausted):
        run_with_output_retry(mock_call, "plan", max_retries=0)

    assert calls == [1]


def test_run_with_output_retry_propagates_other_exceptions() -> None:
    def mock_call(attempt: int) -> bytes:
        raise RuntimeError("Network disconnected")

    with pytest.raises(RuntimeError) as exc_info:
        run_with_output_retry(mock_call, "plan", max_retries=2)

    assert "Network disconnected" in str(exc_info.value)
