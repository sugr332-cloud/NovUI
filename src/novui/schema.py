import copy
import json
from pathlib import Path
import re
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


def bundle_schema(schema_name: str, *, schema_dir: Path = SCHEMA_DIR) -> dict:
    """Bundle schema and all referenced schemas into a single self-contained schema document.

    Removes $id, keeps $schema, rewrites all $ref to #/$defs/..., and inlines definitions.
    Raises ValueError for unknown schema_name or unsupported $ref formats.
    """
    schema_file = _resolve_schema_file(schema_name, schema_dir)
    root_data = json.loads(schema_file.read_text(encoding="utf-8"))
    root_schema = copy.deepcopy(root_data)
    root_schema.pop("$id", None)

    external_cache: dict[str, dict] = {}

    def get_external_schema(stem: str) -> dict:
        if stem not in external_cache:
            ext_file = schema_dir / f"{stem}.schema.json"
            if not ext_file.is_file():
                raise ValueError(f"Referenced schema file not found: {ext_file}")
            external_cache[stem] = json.loads(ext_file.read_text(encoding="utf-8"))
        return external_cache[stem]

    defs_result: dict[str, Any] = root_schema.get("$defs", {})
    root_schema["$defs"] = defs_result

    queue: list[tuple[str, ...]] = []
    processed: set[str] = set()

    ref_ext_def_re = re.compile(r"^([a-zA-Z0-9_]+)\.schema\.json#/\$defs/([a-zA-Z0-9_]+)$")
    ref_ext_file_re = re.compile(r"^([a-zA-Z0-9_]+)\.schema\.json$")
    ref_local_re = re.compile(r"^#/\$defs/([a-zA-Z0-9_]+)$")

    def rewrite_node(node: Any, context_stem: str | None) -> None:
        if isinstance(node, dict):
            if "$ref" in node:
                ref_val = node["$ref"]
                if not isinstance(ref_val, str):
                    raise ValueError(f"Invalid $ref value: {ref_val!r}")

                m_ext_def = ref_ext_def_re.match(ref_val)
                m_ext_file = ref_ext_file_re.match(ref_val)
                m_local = ref_local_re.match(ref_val)

                if m_ext_def:
                    s, n = m_ext_def.groups()
                    target_key = f"{s}__{n}"
                    node["$ref"] = f"#/$defs/{target_key}"
                    if target_key not in processed:
                        queue.append(("def", s, n))
                elif m_ext_file:
                    s = m_ext_file.group(1)
                    target_key = s
                    node["$ref"] = f"#/$defs/{target_key}"
                    if target_key not in processed:
                        queue.append(("file", s))
                elif m_local:
                    n = m_local.group(1)
                    if context_stem is None:
                        # Root schema local reference; keep unchanged
                        pass
                    else:
                        target_key = f"{context_stem}__{n}"
                        node["$ref"] = f"#/$defs/{target_key}"
                        if target_key not in processed:
                            queue.append(("def", context_stem, n))
                else:
                    raise ValueError(f"Invalid or unsupported $ref: {ref_val!r}")

            for v in node.values():
                rewrite_node(v, context_stem)
        elif isinstance(node, list):
            for item in node:
                rewrite_node(item, context_stem)

    # 1. Rewrite root schema
    rewrite_node(root_schema, context_stem=None)

    # 2. Process queue
    while queue:
        task = queue.pop(0)
        kind = task[0]
        if kind == "def":
            _, stem, name = task
            key = f"{stem}__{name}"
            if key in processed:
                continue
            processed.add(key)
            ext = get_external_schema(stem)
            ext_defs = ext.get("$defs", {})
            if name not in ext_defs:
                raise ValueError(f"Definition {name!r} not found in {stem}.schema.json")
            def_body = copy.deepcopy(ext_defs[name])
            rewrite_node(def_body, context_stem=stem)
            defs_result[key] = def_body
        elif kind == "file":
            _, stem = task
            key = stem
            if key in processed:
                continue
            processed.add(key)
            ext = get_external_schema(stem)
            ext_copy = copy.deepcopy(ext)
            ext_copy.pop("$id", None)
            ext_copy.pop("$schema", None)
            file_defs = ext_copy.pop("$defs", None)
            if file_defs:
                for sub_name in file_defs:
                    sub_key = f"{stem}__{sub_name}"
                    if sub_key not in processed:
                        queue.append(("def", stem, sub_name))
            rewrite_node(ext_copy, context_stem=stem)
            defs_result[key] = ext_copy

    if not defs_result:
        root_schema.pop("$defs", None)

    return root_schema

