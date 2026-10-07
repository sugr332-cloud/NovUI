"""JSON Patch (RFC 6902 subset: add, remove, replace, test), JSON Pointer (RFC 6901) and hashes for State Patch (§12.1).

No external dependency. move and copy are not accepted. Application is all-or-nothing and never mutates its input.
"""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Mapping, Sequence

from novui.schema import SchemaError, validate_or_raise
from novui.semantics import check_state_patch


class PatchError(Exception):
    """Raised when a pointer or a patch operation is invalid or cannot be applied."""


_INDEX_RE = re.compile(r"^(0|[1-9][0-9]*)$")
_BAD_ESCAPE_RE = re.compile(r"~(?![01])")
_OPS = ("add", "remove", "replace", "test")


def parse_pointer(ptr: str) -> list[str]:
    """Parse an RFC 6901 JSON Pointer into reference tokens. "" is the whole document ([])."""
    if not isinstance(ptr, str):
        raise PatchError(f"pointer must be a string: {ptr!r}")
    if ptr == "":
        return []
    if not ptr.startswith("/"):
        raise PatchError(f"pointer must be empty or start with '/': {ptr!r}")
    tokens = ptr[1:].split("/")
    out: list[str] = []
    for tok in tokens:
        if _BAD_ESCAPE_RE.search(tok):
            raise PatchError(f"invalid '~' escape in pointer: {ptr!r}")
        out.append(tok.replace("~1", "/").replace("~0", "~"))
    return out


def _json_equal(a: Any, b: Any) -> bool:
    """JSON equality that distinguishes types (True != 1, 1 != 1.0)."""
    if type(a) is not type(b):
        return False
    if isinstance(a, dict):
        if a.keys() != b.keys():
            return False
        return all(_json_equal(a[k], b[k]) for k in a)
    if isinstance(a, list):
        if len(a) != len(b):
            return False
        return all(_json_equal(x, y) for x, y in zip(a, b))
    return a == b


def _list_index(token: str, length: int, *, allow_end: bool, ptr: str) -> int:
    if not _INDEX_RE.match(token):
        raise PatchError(f"invalid array index {token!r} in {ptr!r}")
    idx = int(token)
    limit = length if allow_end else length - 1
    if idx > limit:
        raise PatchError(f"array index out of range ({idx}) in {ptr!r}")
    return idx


def _walk_parent(doc: Any, tokens: list[str], ptr: str) -> Any:
    cur = doc
    for tok in tokens[:-1]:
        if isinstance(cur, dict):
            if tok not in cur:
                raise PatchError(f"path does not exist: {ptr!r}")
            cur = cur[tok]
        elif isinstance(cur, list):
            cur = cur[_list_index(tok, len(cur), allow_end=False, ptr=ptr)]
        else:
            raise PatchError(f"path does not exist: {ptr!r}")
    return cur


def _get(doc: Any, tokens: list[str], ptr: str) -> Any:
    parent = _walk_parent(doc, tokens, ptr) if tokens else doc
    if not tokens:
        return parent
    last = tokens[-1]
    if isinstance(parent, dict):
        if last not in parent:
            raise PatchError(f"path does not exist: {ptr!r}")
        return parent[last]
    if isinstance(parent, list):
        return parent[_list_index(last, len(parent), allow_end=False, ptr=ptr)]
    raise PatchError(f"path does not exist: {ptr!r}")


def _apply_one(doc: Any, op: Mapping[str, Any], n: int) -> Any:
    if not isinstance(op, Mapping):
        raise PatchError(f"operations[{n}] must be an object")
    name = op.get("op")
    if name not in _OPS:
        raise PatchError(f"operations[{n}]: unsupported op {name!r}; must be one of {list(_OPS)}")
    ptr = op.get("path")
    if not isinstance(ptr, str):
        raise PatchError(f"operations[{n}]: path must be a string")
    if name != "remove" and "value" not in op:
        raise PatchError(f"operations[{n}]: {name} requires value")
    tokens = parse_pointer(ptr)
    value = copy.deepcopy(op.get("value"))

    if name == "test":
        actual = _get(doc, tokens, ptr)
        if not _json_equal(actual, value):
            raise PatchError(f"operations[{n}]: test failed at {ptr!r}")
        return doc

    if not tokens:
        if name == "remove":
            raise PatchError(f"operations[{n}]: cannot remove the whole document")
        return value  # add / replace on the whole document

    parent = _walk_parent(doc, tokens, ptr)
    last = tokens[-1]

    if name == "add":
        if isinstance(parent, dict):
            parent[last] = value
        elif isinstance(parent, list):
            if last == "-":
                parent.append(value)
            else:
                parent.insert(_list_index(last, len(parent), allow_end=True, ptr=ptr), value)
        else:
            raise PatchError(f"operations[{n}]: parent of {ptr!r} is not a container")
        return doc

    if isinstance(parent, dict):
        if last not in parent:
            raise PatchError(f"operations[{n}]: path does not exist: {ptr!r}")
        if name == "remove":
            del parent[last]
        else:
            parent[last] = value
    elif isinstance(parent, list):
        idx = _list_index(last, len(parent), allow_end=False, ptr=ptr)
        if name == "remove":
            del parent[idx]
        else:
            parent[idx] = value
    else:
        raise PatchError(f"operations[{n}]: parent of {ptr!r} is not a container")
    return doc


def apply_operations(doc: Any, operations: Sequence[Mapping[str, Any]]) -> Any:
    """Apply operations to a deep copy of doc and return it. doc is never modified. Any failure raises PatchError."""
    result = copy.deepcopy(doc)
    for n, op in enumerate(operations):
        result = _apply_one(result, op, n)
    return result


def apply_patch(doc: Any, patch: Mapping[str, Any]) -> Any:
    """Validate patch with the state_patch schema and semantics, then apply its operations to doc.

    Raises SchemaError if the patch itself is invalid, PatchError if an operation cannot be applied.
    """
    validate_or_raise(dict(patch), "state_patch")
    sem_errors = check_state_patch(patch)
    if sem_errors:
        raise SchemaError(f"Semantic validation failed for state_patch: {sem_errors}", errors=sem_errors)
    return apply_operations(doc, patch["operations"])


def canonical_json(obj: Any) -> bytes:
    """Canonical JSON bytes: sorted keys, no spaces, non-ASCII kept as is."""
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sha256_bytes(data: bytes) -> str:
    """Return 'sha256:<64 lowercase hex>' for data."""
    return "sha256:" + hashlib.sha256(data).hexdigest()


def file_sha256(path: Path) -> str:
    """sha256 of the raw bytes of a file (same value as prompt.ContextEntry.sha256)."""
    return sha256_bytes(path.read_bytes())


def patch_sha256(patch: Mapping[str, Any]) -> str:
    """sha256 of the canonical JSON of one patch. The order of operations is significant."""
    return sha256_bytes(canonical_json(patch))


def patch_set_sha256(patches: Sequence[Mapping[str, Any]]) -> str:
    """sha256 of the canonical JSON of the list of patches sorted by target (value of approval.patch_sha256)."""
    if not patches:
        raise ValueError("patches must not be empty")
    targets = [p["target"] for p in patches]
    if len(set(targets)) != len(targets):
        raise ValueError(f"duplicate target in patches: {targets}")
    ordered = sorted(patches, key=lambda p: p["target"])
    return sha256_bytes(canonical_json([dict(p) for p in ordered]))


def is_noop_patch(patch: Mapping[str, Any]) -> bool:
    """True if every operation is a test (the patch changes nothing)."""
    ops = patch.get("operations", [])
    return len(ops) > 0 and all(op.get("op") == "test" for op in ops)
