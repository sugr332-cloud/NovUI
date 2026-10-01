"""Path validation utilities."""

from pathlib import Path


class PathError(Exception):
    """Raised when a path is outside the allowed root or invalid."""


def is_safe_relpath(p: str) -> bool:
    """Check if p is a safe relative POSIX path.

    Returns False if:
      - p is empty
      - contains NUL
      - starts with '/'
      - contains '\\'
      - contains empty segments (e.g. '//', trailing '/')
      - any segment is '.' or '..'
    Otherwise returns True.
    """
    if not p:
        return False
    if "\0" in p or "\\" in p or p.startswith("/"):
        return False
    parts = p.split("/")
    for part in parts:
        if part == "" or part in (".", ".."):
            return False
    return True


def ensure_within(root: Path, target: Path) -> Path:
    """Ensure target resolves to be at or within root.

    Both root and target are resolved with strict=False.
    Returns the resolved target Path if within root.
    Raises ValueError if root is not absolute.
    Raises PathError if resolved target is not within resolved root.
    """
    if not root.is_absolute():
        raise ValueError(f"Root path must be absolute: {root}")
    resolved_root = root.resolve(strict=False)
    resolved_target = target.resolve(strict=False)
    try:
        resolved_target.relative_to(resolved_root)
    except ValueError:
        raise PathError(f"Target '{target}' (resolved '{resolved_target}') is outside '{resolved_root}'")
    return resolved_target
