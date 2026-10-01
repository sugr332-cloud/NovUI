"""Inspection and validation checks."""

from dataclasses import dataclass
from typing import Collection, Mapping, Sequence, Set

from novui.gitinspect import PathChange
from novui.paths import is_safe_relpath


@dataclass(frozen=True)
class CheckResult:
    name: str
    status: str
    details: tuple[str, ...] = ()


def check_cli_output(exit_code: int, stdout: bytes) -> CheckResult:
    """Check CLI exit code and standard output.

    FAIL if exit_code != 0 or stdout is empty after strip.
    PASS otherwise.
    """
    details: list[str] = []
    if exit_code != 0:
        details.append(f"exit_code={exit_code}")
    if not stdout.strip():
        details.append("empty_stdout")

    if details:
        return CheckResult(name="cli_output", status="FAIL", details=tuple(details))
    return CheckResult(name="cli_output", status="PASS")


def check_allowed_paths(changes: Sequence[PathChange], allowed: Collection[str]) -> CheckResult:
    """Check that all changed paths fall within allowed path list.

    Raises ValueError if any path in allowed is not a safe relative path.
    FAIL if any change path or orig_path is not in allowed.
    """
    for a in allowed:
        if not is_safe_relpath(a):
            raise ValueError(f"Invalid allowed path (not a safe relative path): {a!r}")

    disallowed: set[str] = set()
    for c in changes:
        if c.path not in allowed:
            disallowed.add(c.path)
        if c.orig_path is not None and c.orig_path not in allowed:
            disallowed.add(c.orig_path)

    if disallowed:
        return CheckResult(name="allowed_paths", status="FAIL", details=tuple(sorted(disallowed)))
    return CheckResult(name="allowed_paths", status="PASS")


def check_ignored_unchanged(before: Set[str], after: Set[str]) -> CheckResult:
    """Check that ignored files list did not change."""
    added = [f"added: {p}" for p in after - before]
    removed = [f"removed: {p}" for p in before - after]
    details = sorted(added + removed)
    if details:
        return CheckResult(name="ignored_files", status="FAIL", details=tuple(details))
    return CheckResult(name="ignored_files", status="PASS")


def check_append_only(before: bytes | None, after: bytes | None) -> CheckResult:
    """Check that changes to a file are append-only.

    - before is None and after is None -> PASS
    - before is None and after is not None -> PASS (new file)
    - before is not None and after is None -> FAIL (deleted)
    - after == before -> PASS
    - after starts with before and (before is empty or ends with \\n) -> PASS
    - after starts with before but before does not end with \\n -> FAIL (last_line_modified)
    - otherwise -> FAIL (existing_content_modified)
    """
    if before is None:
        return CheckResult(name="append_only", status="PASS")
    if after is None:
        return CheckResult(name="append_only", status="FAIL", details=("deleted",))
    if after == before:
        return CheckResult(name="append_only", status="PASS")
    if after.startswith(before):
        if len(before) == 0 or before.endswith(b"\n"):
            return CheckResult(name="append_only", status="PASS")
        return CheckResult(name="append_only", status="FAIL", details=("last_line_modified",))
    return CheckResult(name="append_only", status="FAIL", details=("existing_content_modified",))


def check_prefix_suffix(after: bytes, prefix: bytes, suffix: bytes) -> CheckResult:
    """Check that after preserves prefix and suffix unchanged."""
    if len(after) >= len(prefix) + len(suffix):
        if after.startswith(prefix) and (len(suffix) == 0 or after.endswith(suffix)):
            return CheckResult(name="prefix_suffix", status="PASS")
    return CheckResult(name="prefix_suffix", status="FAIL")


def compare_hash_records(before: Mapping[str, str], after: Mapping[str, str]) -> CheckResult:
    """Compare hash maps and detect changed, missing, or added keys."""
    diffs: list[str] = []
    all_keys = set(before.keys()) | set(after.keys())
    for k in all_keys:
        if k not in after:
            diffs.append(f"missing_after: {k}")
        elif k not in before:
            diffs.append(f"added_after: {k}")
        elif before[k] != after[k]:
            diffs.append(f"changed: {k}")

    diffs.sort()
    if diffs:
        return CheckResult(name="hash_compare", status="FAIL", details=tuple(diffs))
    return CheckResult(name="hash_compare", status="PASS")


def count_chars(text: str) -> int:
    """Count non-whitespace Unicode codepoints."""
    return sum(1 for ch in text if not ch.isspace())


def check_char_range(text: str, min_chars: int, max_chars: int) -> CheckResult:
    """Check that non-whitespace character count is within [min_chars, max_chars].

    Returns WARNING (not FAIL) if outside range.
    Raises ValueError if min_chars < 0 or min_chars > max_chars.
    """
    if min_chars < 0 or min_chars > max_chars:
        raise ValueError(f"Invalid range: [{min_chars}, {max_chars}]")
    count = count_chars(text)
    detail = f"count={count} range={min_chars}-{max_chars}"
    if min_chars <= count <= max_chars:
        return CheckResult(name="char_count", status="PASS", details=(detail,))
    return CheckResult(name="char_count", status="WARNING", details=(detail,))
