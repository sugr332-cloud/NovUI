"""Works registry management."""

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from novui.config import Settings
from novui.schema import SchemaError, validate_or_raise
from novui.semantics import check_works_registry
from novui.yamlio import load_yaml, write_yaml_atomic


@dataclass(frozen=True)
class WorkInfo:
    work_key: str
    path: Path
    format_version: int
    state: str


def registry_path(settings: Settings) -> Path:
    """Return path to works registry: <data_dir>/registry.yaml."""
    return settings.data_dir / "registry.yaml"


def _load_registry_doc(settings: Settings) -> dict[str, Any]:
    p = registry_path(settings)
    if not p.is_file():
        return {"works": []}
    doc = load_yaml(p)
    if not isinstance(doc, dict):
        raise SchemaError("Works registry must be a mapping", errors=["root is not a mapping"])
    validate_or_raise(doc, "works_registry")
    sem_errs = check_works_registry(doc)
    if sem_errs:
        raise SchemaError(f"Semantic validation failed for works_registry: {sem_errs}", errors=sem_errs)
    return doc


def list_works(settings: Settings) -> list[WorkInfo]:
    """List all registered works in registry.yaml, or empty list if file does not exist."""
    doc = _load_registry_doc(settings)
    result: list[WorkInfo] = []
    for w in doc.get("works", []):
        result.append(
            WorkInfo(
                work_key=w["work_key"],
                path=Path(w["path"]),
                format_version=int(w["format_version"]),
                state=w["state"],
            )
        )
    return result


def get_work(settings: Settings, work_key: str) -> WorkInfo:
    """Return registered WorkInfo for work_key, or raise KeyError if not found."""
    works = list_works(settings)
    for w in works:
        if w.work_key == work_key:
            return w
    raise KeyError(f"Work {work_key!r} not found in registry")


def register_work(settings: Settings, work_key: str, path: Path, format_version: int) -> WorkInfo:
    """Register a new work in the registry.

    Raises:
        ValueError: If path is not absolute or work_key already exists.
    """
    if not path.is_absolute():
        raise ValueError(f"Path must be absolute, got: {path}")

    doc = _load_registry_doc(settings)
    works = doc.get("works", [])
    for w in works:
        if w["work_key"] == work_key:
            raise ValueError(f"Work {work_key!r} already registered")

    new_entry = {
        "work_key": work_key,
        "path": str(path.resolve(strict=False)),
        "format_version": format_version,
        "state": "active",
    }
    works.append(new_entry)
    doc["works"] = works

    # Validate against schema and semantics before atomic write
    validate_or_raise(doc, "works_registry")
    sem_errs = check_works_registry(doc)
    if sem_errs:
        raise SchemaError(f"Semantic validation failed for works_registry: {sem_errs}", errors=sem_errs)

    p = registry_path(settings)
    write_yaml_atomic(p, doc)

    return WorkInfo(
        work_key=work_key,
        path=Path(new_entry["path"]),
        format_version=format_version,
        state="active",
    )
