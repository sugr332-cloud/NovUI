"""Text format of a work: novel (prose) or script (novel game script, docs/script-format.md).

Selected by project.yaml text_format (phase2e-design decision 1). Default novel.
"""

from __future__ import annotations

from pathlib import Path

from novui.chapters import ChapterError
from novui.schema import SchemaError, validate_or_raise
from novui.yamlio import YamlError, load_yaml

TEXT_FORMATS: tuple[str, ...] = ("novel", "script")


def read_text_format(root: Path) -> str:
    """text_format of project.yaml ('novel' when absent). ChapterError if project.yaml is missing or invalid."""
    path = root / "project.yaml"
    if not path.is_file():
        raise ChapterError(f"project.yaml not found at {path}")
    try:
        doc = load_yaml(path)
        validate_or_raise(doc, "project")
    except (YamlError, SchemaError) as exc:
        raise ChapterError(f"project.yaml is invalid: {exc}") from exc
    return doc.get("text_format", "novel")
