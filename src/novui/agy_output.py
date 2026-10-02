"""Parsing and transforming AGY CLI text output."""

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Sequence

from novui.schema import SchemaError, validate_or_raise
from novui.semantics import check_requests

MARKER_RE: re.Pattern[str] = re.compile(r"【要確認[：:]\s*([^】]*?)\s*】")
_TRAILING_WS_RE: re.Pattern[str] = re.compile(r"\s*$")


class AgyOutputError(Exception):
    """Raised when parsing AGY text output fails."""

    def __init__(self, kind: str, message: str = "") -> None:
        super().__init__(message or f"AGY output error: {kind}")
        self.kind = kind


@dataclass(frozen=True)
class Marker:
    message: str  # 括弧内の文字列（前後の空白を除く）。空なら "（記載なし）"
    start: int  # text 内の開始位置（文字単位）
    end: int  # text 内の終了位置（文字単位）


@dataclass(frozen=True)
class AgyText:
    text: str
    markers: tuple[Marker, ...]


def parse_agy_text(stdout: bytes, *, trailing_newline: bool = True) -> AgyText:
    """Parse stdout from AGY into validated text and markers.

    1. Decode UTF-8 (strict). Failure -> kind='decode'
    2. rstrip() whitespace. Empty (or only whitespace) -> kind='empty'
    3. If lstrip() starts with ``` -> kind='code_fence'
    4. If trailing_newline is True, append '\n'
    5. Find all matches of MARKER_RE as Marker instances
    """
    # 1. Decode UTF-8
    try:
        decoded = stdout.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise AgyOutputError("decode", f"UTF-8 decode failed: {exc}") from exc

    # 2. rstrip whitespace
    stripped = decoded.rstrip()
    if not stripped:
        raise AgyOutputError("empty", "AGY output is empty or only whitespace")

    # 3. Check for code fence at start
    if stripped.lstrip().startswith("```"):
        raise AgyOutputError("code_fence", "AGY output starts with code fence")

    # 4. Trailing newline
    text = stripped + "\n" if trailing_newline else stripped

    # 5. Extract markers
    markers: list[Marker] = []
    for m in MARKER_RE.finditer(text):
        msg = m.group(1).strip()
        if not msg:
            msg = "（記載なし）"
        markers.append(Marker(message=msg, start=m.start(), end=m.end()))

    return AgyText(text=text, markers=tuple(markers))


def markers_to_requests(
    markers: Sequence[Marker],
    *,
    job_id: str,
    chapter_id: str,
) -> list[dict]:
    """Convert extracted markers to requests document entries and validate."""
    reqs = [
        {
            "type": "request",
            "job_id": job_id,
            "chapter_id": chapter_id,
            "kind": "undefined_setting",
            "target": None,
            "message": m.message,
        }
        for m in markers
    ]

    # Validate against schema and semantics
    validate_or_raise(reqs, "requests")
    sem_errors = check_requests(reqs)
    if sem_errors:
        raise SchemaError(
            f"Semantic validation failed for requests with {len(sem_errors)} error(s)",
            errors=sem_errors,
        )

    return reqs


def splice_range(prefix: bytes, original_range: bytes, replacement: str, suffix: bytes) -> bytes:
    """Splice replacement text into prefix and suffix preserving original trailing whitespace."""
    try:
        orig_text = original_range.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"original_range must be valid UTF-8: {exc}") from exc

    m = _TRAILING_WS_RE.search(orig_text)
    trailing_ws = m.group(0) if m else ""

    new_content = replacement.rstrip() + trailing_ws
    return prefix + new_content.encode("utf-8") + suffix
