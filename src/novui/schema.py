"""JSON Schema validation using referencing Registry and Draft 2020-12."""

import json
from pathlib import Path
from typing import Any, Sequence
from jsonschema import Draft202012Validator
from referencing import Registry, Resource

SCHEMA_DIR: Path = Path(__file__).resolve().parents[2] / "schemas"


class SchemaError(Exception):
    """Raised when document validation against a schema fails."""

    def __init__(self, message: str, errors: list[str]) -> None:
        super().__init__(message)
        self.errors = errors


def _to_json_pointer(path: Sequence[Any]) -> str:
    """Format path sequence into RFC 6901 JSON Pointer."""
    if not path:
        return ""
    parts = [str(p).replace("~", "~0").replace("/", "~1") for p in path]
    return "/" + "/".join(parts)


def load_registry(schema_dir: Path = SCHEMA_DIR) -> Registry:
    """Load all *.schema.json from schema_dir into a referencing Registry.

    Validates that every schema file is a valid Draft 2020-12 schema.
    """
    registry: Registry = Registry()
    schema_files = sorted(schema_dir.glob("*.schema.json"))
    if not schema_files:
        raise ValueError(f"No schema files found in {schema_dir}")

    for p in schema_files:
        data = json.loads(p.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(data)
        res = Resource.from_contents(data)
        schema_id = data.get("$id")
        if schema_id:
            registry = registry.with_resource(schema_id, res)
        # Also register by filename for relative $ref resolution
        registry = registry.with_resource(p.name, res)

    return registry


def _resolve_schema_file(schema_name: str, schema_dir: Path) -> Path:
    """Resolve schema_name to an existing file in schema_dir."""
    candidates = [
        schema_dir / schema_name,
        schema_dir / f"{schema_name}.schema.json",
        schema_dir / f"{schema_name}.json",
    ]
    for c in candidates:
        if c.is_file():
            return c
    raise ValueError(f"Unknown schema: {schema_name!r} in {schema_dir}")


def validate(doc: Any, schema_name: str, *, schema_dir: Path = SCHEMA_DIR) -> list[str]:
    """Validate doc against schema_name.

    Returns a list of error strings formatted as "<JSON Pointer>: <message>",
    sorted ascending by location.
    Raises ValueError if schema_name does not exist.
    """
    schema_file = _resolve_schema_file(schema_name, schema_dir)
    registry = load_registry(schema_dir)
    schema_data = json.loads(schema_file.read_text(encoding="utf-8"))

    validator = Draft202012Validator(schema_data, registry=registry)
    raw_errors = list(validator.iter_errors(doc))
    if not raw_errors:
        return []

    formatted: list[tuple[str, str]] = []
    for err in raw_errors:
        loc = _to_json_pointer(err.path)
        formatted.append((loc, f"{loc}: {err.message}"))

    # Sort ascending by JSON Pointer location
    formatted.sort(key=lambda item: item[0])
    return [msg for _, msg in formatted]


def validate_or_raise(doc: Any, schema_name: str, *, schema_dir: Path = SCHEMA_DIR) -> None:
    """Validate doc against schema_name and raise SchemaError if invalid."""
    errors = validate(doc, schema_name, schema_dir=schema_dir)
    if errors:
        raise SchemaError(
            f"Validation failed for schema {schema_name!r} with {len(errors)} error(s)",
            errors=errors,
        )
