"""Tests for protected paths configuration."""

from novui.protected import PROTECTED_PATHS


def test_protected_paths_match_spec_v051_15_2() -> None:
    """Verify PROTECTED_PATHS value and order match spec v0.5.1 section 15.2."""
    expected = (
        "project.yaml",
        "chapters-order.yaml",
        "world",
        "characters",
        "plot",
        "foreshadowing/registry.yaml",
        "rules",
        ".novui",
    )
    assert PROTECTED_PATHS == expected
