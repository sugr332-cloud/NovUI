import pytest

from novui.protected import PROTECTED_PATHS, is_protected


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


def test_is_protected_examples() -> None:
    assert is_protected("world/w.md") is True
    assert is_protected("worldmap.md") is False
    assert is_protected("foreshadowing/registry.yaml") is True
    assert is_protected("foreshadowing/notes.md") is False
    assert is_protected(".novui/approvals/A-0001.yaml") is True
    assert is_protected("chapters/ch-001/draft.md") is False

    # Exact matches for directory or file
    assert is_protected("project.yaml") is True
    assert is_protected("chapters-order.yaml") is True
    assert is_protected("world") is True
    assert is_protected("characters/C001.yaml") is True
    assert is_protected("plot/timeline.yaml") is True
    assert is_protected("rules/custom.md") is True


def test_is_protected_invalid_paths() -> None:
    for invalid in ["", "../outside", "/absolute/path", "foo//bar", "a/../b", "trailing/"]:
        with pytest.raises(ValueError):
            is_protected(invalid)
