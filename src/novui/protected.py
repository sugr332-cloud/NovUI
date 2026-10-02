"""Protected paths definition for NovUI."""

PROTECTED_PATHS: tuple[str, ...] = (
    "project.yaml",
    "chapters-order.yaml",
    "world",
    "characters",
    "plot",
    "foreshadowing/registry.yaml",
    "rules",
    ".novui",
)


def is_protected(path: str) -> bool:
    """Check if path is or is under one of the PROTECTED_PATHS.

    Raises ValueError if path is not a safe relative path.
    """
    from novui.paths import is_safe_relpath

    if not is_safe_relpath(path):
        raise ValueError(f"Path is not a safe relative path: {path!r}")

    for p in PROTECTED_PATHS:
        if path == p or path.startswith(f"{p}/"):
            return True
    return False
