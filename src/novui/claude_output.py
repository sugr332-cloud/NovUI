"""Parsing and retrying Claude CLI output."""

from dataclasses import dataclass
import json
from typing import Any, Callable

from novui.schema import validate
from novui.semantics import SEMANTIC_CHECKS

CLAUDE_OUTPUT_TYPES: frozenset[str] = frozenset({
    "plan",
    "integrity_review",
    "writing_review",
    "state_patch",
    "summary",
    "instruction_routing",
})


class ClaudeOutputError(Exception):
    """Raised when parsing or validating Claude output fails."""

    def __init__(self, kind: str, errors: list[str]) -> None:
        super().__init__(f"Claude output error ({kind}): {errors}")
        self.kind = kind
        self.errors = errors


@dataclass(frozen=True)
class AttemptRecord:
    attempt: int
    ok: bool
    error_kind: str | None
    errors: tuple[str, ...]


class OutputRetryExhausted(Exception):
    """Raised when all retry attempts fail."""

    def __init__(self, attempts: list[AttemptRecord]) -> None:
        super().__init__(f"All {len(attempts)} attempts to get valid Claude output failed")
        self.attempts = attempts


def parse_claude_output(stdout: bytes, expected_type: str) -> dict[str, Any]:
    """Parse raw stdout from Claude CLI into a validated dict.

    Does not strip code fences or scan text for embedded JSON.
    Validation proceeds through: decode -> json -> type -> schema -> semantic.
    """
    if expected_type not in CLAUDE_OUTPUT_TYPES:
        raise ValueError(f"Unknown expected_type {expected_type!r}; must be one of {sorted(CLAUDE_OUTPUT_TYPES)}")

    # 1. Decode UTF-8
    try:
        text = stdout.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ClaudeOutputError("decode", [str(exc)]) from exc

    # 2. Parse JSON
    trimmed = text.strip()
    try:
        data = json.loads(trimmed)
    except json.JSONDecodeError as exc:
        raise ClaudeOutputError("json", [str(exc)]) from exc

    # 3. Check type
    if not isinstance(data, dict):
        raise ClaudeOutputError("type", [f"Expected JSON object, got {type(data).__name__}"])
    if data.get("type") != expected_type:
        raise ClaudeOutputError(
            "type",
            [f"Expected type property to be {expected_type!r}, got {data.get('type')!r}"],
        )

    # 4. Schema validation
    schema_errors = validate(data, expected_type)
    if schema_errors:
        raise ClaudeOutputError("schema", schema_errors)

    # 5. Semantic validation
    semantic_fn = SEMANTIC_CHECKS.get(expected_type)
    if semantic_fn:
        sem_errors = semantic_fn(data)
        if sem_errors:
            raise ClaudeOutputError("semantic", sem_errors)

    return data


def run_with_output_retry(
    call: Callable[[int], bytes],
    expected_type: str,
    *,
    max_retries: int = 2,
) -> tuple[dict[str, Any], list[AttemptRecord]]:
    """Execute call with retry on ClaudeOutputError up to max_retries times.

    Total calls will be at most max_retries + 1.
    Other exceptions from call are re-raised immediately without recording.
    """
    attempts: list[AttemptRecord] = []
    max_attempts = max_retries + 1

    for attempt in range(1, max_attempts + 1):
        stdout = call(attempt)
        try:
            parsed = parse_claude_output(stdout, expected_type)
            attempts.append(AttemptRecord(attempt=attempt, ok=True, error_kind=None, errors=()))
            return parsed, attempts
        except ClaudeOutputError as exc:
            attempts.append(
                AttemptRecord(
                    attempt=attempt,
                    ok=False,
                    error_kind=exc.kind,
                    errors=tuple(exc.errors),
                )
            )

    raise OutputRetryExhausted(attempts)
