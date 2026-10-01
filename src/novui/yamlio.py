"""YAML I/O utilities with strict validation."""

import os
from pathlib import Path
import re
import tempfile
from typing import Any
import uuid
import yaml


class YamlError(Exception):
    """Raised when YAML loading, dumping, or atomic writing fails."""


class NovuiYamlLoader(yaml.SafeLoader):
    """SafeLoader that disables timestamp resolution, enforces strict booleans, and rejects duplicate keys."""
    pass


# 1. Deep copy resolvers so standard yaml.SafeLoader is not modified
NovuiYamlLoader.yaml_implicit_resolvers = {
    k: [list(item) for item in v] for k, v in yaml.SafeLoader.yaml_implicit_resolvers.items()
}

# Remove timestamp, int, and float resolvers from all characters
for ch, res_list in list(NovuiYamlLoader.yaml_implicit_resolvers.items()):
    NovuiYamlLoader.yaml_implicit_resolvers[ch] = [
        item for item in res_list
        if item[0] not in (
            "tag:yaml.org,2002:timestamp",
            "tag:yaml.org,2002:int",
            "tag:yaml.org,2002:float",
        )
    ]

# Restrict bool resolver to only true, True, TRUE, false, False, FALSE
_STRICT_BOOL_RE = re.compile(r"^(?:true|True|TRUE|false|False|FALSE)$")
for ch, res_list in list(NovuiYamlLoader.yaml_implicit_resolvers.items()):
    new_list = []
    for tag, regex in res_list:
        if tag == "tag:yaml.org,2002:bool":
            if ch in ("t", "T", "f", "F"):
                new_list.append((tag, _STRICT_BOOL_RE))
        else:
            new_list.append((tag, regex))
    NovuiYamlLoader.yaml_implicit_resolvers[ch] = new_list

# YAML 1.2 equivalent int and float resolvers
_YAML12_INT_RE = re.compile(r"^[-+]?(0|[1-9][0-9]*)$")
_YAML12_FLOAT_RES = [
    re.compile(r"^[-+]?(\.[0-9]+|[0-9]+\.[0-9]*)([eE][-+]?[0-9]+)?$"),
    re.compile(r"^[-+]?\.(inf|Inf|INF)$"),
    re.compile(r"^\.(nan|NaN|NAN)$"),
]

_INT_CHARS = set("-+0123456789")
_FLOAT_CHARS_1 = set("-+.0123456789")
_FLOAT_CHARS_2 = set("-+.")
_FLOAT_CHARS_3 = set(".")
_ALL_NUM_CHARS = _INT_CHARS | _FLOAT_CHARS_1 | _FLOAT_CHARS_2 | _FLOAT_CHARS_3

for _ch in _ALL_NUM_CHARS:
    if _ch not in NovuiYamlLoader.yaml_implicit_resolvers:
        NovuiYamlLoader.yaml_implicit_resolvers[_ch] = []
    if _ch in _INT_CHARS:
        NovuiYamlLoader.yaml_implicit_resolvers[_ch].append(("tag:yaml.org,2002:int", _YAML12_INT_RE))
    if _ch in _FLOAT_CHARS_1:
        NovuiYamlLoader.yaml_implicit_resolvers[_ch].append(("tag:yaml.org,2002:float", _YAML12_FLOAT_RES[0]))
    if _ch in _FLOAT_CHARS_2:
        NovuiYamlLoader.yaml_implicit_resolvers[_ch].append(("tag:yaml.org,2002:float", _YAML12_FLOAT_RES[1]))
    if _ch in _FLOAT_CHARS_3:
        NovuiYamlLoader.yaml_implicit_resolvers[_ch].append(("tag:yaml.org,2002:float", _YAML12_FLOAT_RES[2]))

# 2. Copy constructors and register mapping constructor that rejects duplicates
NovuiYamlLoader.yaml_constructors = yaml.SafeLoader.yaml_constructors.copy()


def _construct_mapping_rejecting_duplicates(loader: NovuiYamlLoader, node: yaml.MappingNode, deep: bool = False) -> dict[Any, Any]:
    if not isinstance(node, yaml.MappingNode):
        raise YamlError(f"Expected a mapping node, got {node.id}")
    loader.flatten_mapping(node)
    mapping: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        try:
            hash(key)
        except TypeError as exc:
            raise YamlError(f"Unhashable key in YAML mapping: {key}") from exc
        if key in mapping:
            raise YamlError(f"Duplicate key found in YAML mapping: {key!r}")
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


NovuiYamlLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_mapping_rejecting_duplicates,
)


def loads_yaml(text: str) -> Any:
    """Parse YAML from string using NovuiYamlLoader.

    Raises YamlError on syntax errors, duplicate keys, or other load errors.
    """
    try:
        return yaml.load(text, Loader=NovuiYamlLoader)
    except yaml.error.YAMLError as exc:
        raise YamlError(f"Failed to parse YAML: {exc}") from exc


def load_yaml(path: Path) -> Any:
    """Read a YAML file in UTF-8 without BOM and parse it.

    Raises YamlError if file has BOM, invalid UTF-8, syntax error, or duplicate keys.
    """
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise YamlError(f"Failed to read file {path}: {exc}") from exc

    if raw.startswith(b"\xef\xbb\xbf"):
        raise YamlError(f"BOM is not allowed in YAML files: {path}")

    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise YamlError(f"File {path} is not valid UTF-8: {exc}") from exc

    return loads_yaml(text)


def dumps_yaml(data: Any) -> str:
    """Dump data to YAML string without escaping Unicode characters."""
    try:
        return yaml.safe_dump(
            data,
            allow_unicode=True,
            sort_keys=False,
            default_flow_style=False,
        )
    except yaml.error.YAMLError as exc:
        raise YamlError(f"Failed to dump YAML: {exc}") from exc


def write_yaml_atomic(path: Path, data: Any) -> None:
    """Serialize data to YAML and atomically write to path.

    Writes to a temporary file in the same directory, flushes, fsyncs, and replaces.
    Cleans up temporary file if any exception occurs.
    """
    content = dumps_yaml(data)
    parent = path.parent
    parent.mkdir(parents=True, exist_ok=True)

    temp_path = parent / f".tmp_{path.name}_{os.getpid()}_{uuid.uuid4().hex}"
    try:
        with open(temp_path, "w", encoding="utf-8") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp_path, path)
    except Exception as exc:
        if temp_path.exists():
            try:
                temp_path.unlink()
            except OSError:
                pass
        raise YamlError(f"Failed to atomically write YAML to {path}: {exc}") from exc
