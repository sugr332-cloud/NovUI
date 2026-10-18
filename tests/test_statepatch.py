"""Tests for novui.statepatch (JSON Patch subset, pointers and hashes)."""

import copy
from pathlib import Path

import pytest

from novui.prompt import build_claude_prompt
from novui.schema import SchemaError
from novui.statepatch import (
    PatchError,
    apply_operations,
    apply_patch,
    canonical_json,
    file_sha256,
    is_noop_patch,
    parse_pointer,
    patch_set_sha256,
    patch_sha256,
    sha256_bytes,
)

BASE_HASH = "sha256:" + "0" * 64


def _patch(target: str = "characters/C001.yaml", ops: list | None = None) -> dict:
    return {
        "type": "state_patch",
        "target": target,
        "base_hash": BASE_HASH,
        "operations": ops if ops is not None else [{"op": "test", "path": "/id", "value": "C001"}],
        "reason": "理由",
    }


# ---------- parse_pointer ----------

def test_parse_pointer_basic_and_escapes() -> None:
    assert parse_pointer("") == []
    assert parse_pointer("/") == [""]
    assert parse_pointer("/a/b") == ["a", "b"]
    assert parse_pointer("/knowledge/-") == ["knowledge", "-"]
    assert parse_pointer("/a~1b/c~0d") == ["a/b", "c~d"]
    # ~01 is "~1" (unescape ~1 first, then ~0)
    assert parse_pointer("/~01") == ["~1"]


@pytest.mark.parametrize("bad", ["a/b", "x", "/a~", "/a~2", "/~"])
def test_parse_pointer_invalid(bad: str) -> None:
    with pytest.raises(PatchError):
        parse_pointer(bad)


# ---------- add ----------

def test_add_dict_new_and_existing_key() -> None:
    doc = {"a": 1}
    assert apply_operations(doc, [{"op": "add", "path": "/b", "value": 2}]) == {"a": 1, "b": 2}
    assert apply_operations(doc, [{"op": "add", "path": "/a", "value": 9}]) == {"a": 9}


def test_add_list_positions() -> None:
    doc = {"l": [1, 2]}
    assert apply_operations(doc, [{"op": "add", "path": "/l/-", "value": 3}]) == {"l": [1, 2, 3]}
    assert apply_operations(doc, [{"op": "add", "path": "/l/0", "value": 0}]) == {"l": [0, 1, 2]}
    assert apply_operations(doc, [{"op": "add", "path": "/l/1", "value": 9}]) == {"l": [1, 9, 2]}
    assert apply_operations(doc, [{"op": "add", "path": "/l/2", "value": 3}]) == {"l": [1, 2, 3]}


def test_add_list_out_of_range_and_missing_parent() -> None:
    with pytest.raises(PatchError):
        apply_operations({"l": [1]}, [{"op": "add", "path": "/l/5", "value": 1}])
    with pytest.raises(PatchError):
        apply_operations({}, [{"op": "add", "path": "/x/y", "value": 1}])


def test_add_whole_document() -> None:
    assert apply_operations({"a": 1}, [{"op": "add", "path": "", "value": [1]}]) == [1]


def test_add_value_is_copied() -> None:
    value = {"k": [1]}
    out = apply_operations({"l": []}, [{"op": "add", "path": "/l/-", "value": value}])
    value["k"].append(2)
    assert out == {"l": [{"k": [1]}]}


# ---------- remove / replace ----------

def test_remove_and_replace() -> None:
    assert apply_operations({"a": 1, "b": 2}, [{"op": "remove", "path": "/a"}]) == {"b": 2}
    assert apply_operations({"l": [1, 2, 3]}, [{"op": "remove", "path": "/l/1"}]) == {"l": [1, 3]}
    assert apply_operations({"a": 1}, [{"op": "replace", "path": "/a", "value": 5}]) == {"a": 5}
    assert apply_operations({"l": [1, 2]}, [{"op": "replace", "path": "/l/1", "value": 7}]) == {"l": [1, 7]}
    assert apply_operations({"a": 1}, [{"op": "replace", "path": "", "value": 2}]) == 2


@pytest.mark.parametrize(
    "ops",
    [
        [{"op": "remove", "path": "/missing"}],
        [{"op": "remove", "path": ""}],
        [{"op": "replace", "path": "/missing", "value": 1}],
        [{"op": "remove", "path": "/l/9"}],
        [{"op": "replace", "path": "/l/-", "value": 1}],
        [{"op": "remove", "path": "/l/-"}],
    ],
)
def test_remove_replace_failures(ops: list) -> None:
    with pytest.raises(PatchError):
        apply_operations({"a": 1, "l": [1]}, ops)


# ---------- test ----------

def test_test_op_match_and_mismatch() -> None:
    doc = {"a": {"b": [1, "x"]}, "n": None}
    apply_operations(doc, [{"op": "test", "path": "/a/b/1", "value": "x"}])
    apply_operations(doc, [{"op": "test", "path": "/a", "value": {"b": [1, "x"]}}])
    apply_operations(doc, [{"op": "test", "path": "/n", "value": None}])
    apply_operations(doc, [{"op": "test", "path": "", "value": doc}])
    with pytest.raises(PatchError):
        apply_operations(doc, [{"op": "test", "path": "/a/b/1", "value": "y"}])
    with pytest.raises(PatchError):
        apply_operations(doc, [{"op": "test", "path": "/missing", "value": 1}])


def test_test_op_distinguishes_types() -> None:
    with pytest.raises(PatchError):
        apply_operations({"a": True}, [{"op": "test", "path": "/a", "value": 1}])
    with pytest.raises(PatchError):
        apply_operations({"a": 1}, [{"op": "test", "path": "/a", "value": True}])
    with pytest.raises(PatchError):
        apply_operations({"a": 1}, [{"op": "test", "path": "/a", "value": 1.0}])
    with pytest.raises(PatchError):
        apply_operations({"a": [True]}, [{"op": "test", "path": "/a", "value": [1]}])


# ---------- unsupported ops, array index forms ----------

@pytest.mark.parametrize("op", ["move", "copy", "bogus"])
def test_unsupported_ops(op: str) -> None:
    with pytest.raises(PatchError):
        apply_operations({"a": 1}, [{"op": op, "from": "/a", "path": "/b"}])


def test_missing_value_and_path() -> None:
    with pytest.raises(PatchError):
        apply_operations({"a": 1}, [{"op": "add", "path": "/b"}])
    with pytest.raises(PatchError):
        apply_operations({"a": 1}, [{"op": "test", "path": "/a"}])
    with pytest.raises(PatchError):
        apply_operations({"a": 1}, [{"op": "remove"}])


@pytest.mark.parametrize("token", ["01", "-1", "+1", "", "1.0", "a"])
def test_invalid_array_index_forms(token: str) -> None:
    with pytest.raises(PatchError):
        apply_operations({"l": [1, 2, 3]}, [{"op": "replace", "path": f"/l/{token}", "value": 0}])


def test_nested_list_index_in_walk() -> None:
    doc = [{"hints": []}, {"hints": []}]
    out = apply_operations(doc, [{"op": "add", "path": "/1/hints/-", "value": {"chapter": "ch-001", "scene": "S1"}}])
    assert out[1]["hints"] == [{"chapter": "ch-001", "scene": "S1"}]
    assert out[0]["hints"] == []


# ---------- all-or-nothing, no mutation ----------

def test_all_or_nothing_and_input_not_modified() -> None:
    doc = {"a": 1, "l": [1]}
    snapshot = copy.deepcopy(doc)
    with pytest.raises(PatchError):
        apply_operations(
            doc,
            [
                {"op": "add", "path": "/b", "value": 2},
                {"op": "test", "path": "/a", "value": 999},
            ],
        )
    assert doc == snapshot

    out = apply_operations(doc, [{"op": "add", "path": "/l/-", "value": 2}])
    assert doc == snapshot
    assert out == {"a": 1, "l": [1, 2]}


def test_operations_applied_in_order() -> None:
    out = apply_operations(
        {"l": []},
        [
            {"op": "add", "path": "/l/-", "value": "a"},
            {"op": "test", "path": "/l/0", "value": "a"},
            {"op": "replace", "path": "/l/0", "value": "b"},
        ],
    )
    assert out == {"l": ["b"]}


# ---------- apply_patch ----------

def test_apply_patch_valid_and_invalid() -> None:
    doc = {"id": "C001", "knowledge": []}
    patch = _patch(ops=[{"op": "add", "path": "/knowledge/-", "value": {"id": "K001"}}])
    assert apply_patch(doc, patch)["knowledge"] == [{"id": "K001"}]

    bad_schema = _patch()
    bad_schema["operations"] = []
    with pytest.raises(SchemaError):
        apply_patch(doc, bad_schema)

    bad_target = _patch(target="../etc/passwd")
    with pytest.raises(SchemaError):
        apply_patch(doc, bad_target)

    move = _patch(ops=[{"op": "move", "path": "/a", "from": "/b"}])
    with pytest.raises(SchemaError):
        apply_patch(doc, move)

    failing = _patch(ops=[{"op": "test", "path": "/id", "value": "C999"}])
    with pytest.raises(PatchError):
        apply_patch(doc, failing)


# ---------- hashes ----------

def test_canonical_json_is_stable_and_keeps_unicode() -> None:
    a = {"b": 1, "a": "日本語", "c": [3, 2]}
    b = {"c": [3, 2], "a": "日本語", "b": 1}
    assert canonical_json(a) == canonical_json(b)
    assert canonical_json(a) == '{"a":"日本語","b":1,"c":[3,2]}'.encode("utf-8")


def test_sha256_format_and_file_hash_matches_context_entry(tmp_path: Path) -> None:
    f = tmp_path / "characters" / "C001.yaml"
    f.parent.mkdir()
    f.write_bytes("id: C001\nname: 山田\n".encode("utf-8"))
    h = file_sha256(f)
    assert h.startswith("sha256:") and len(h) == len("sha256:") + 64 and h == h.lower()
    assert h == sha256_bytes(f.read_bytes())
    built = build_claude_prompt(tmp_path, ["characters/C001.yaml"], "【作業】テスト")
    assert built.context[0].sha256 == h


def test_patch_sha256_depends_on_operation_order_and_content() -> None:
    o1 = {"op": "test", "path": "/id", "value": "C001"}
    o2 = {"op": "add", "path": "/knowledge/-", "value": {"id": "K001"}}
    p12 = _patch(ops=[o1, o2])
    p21 = _patch(ops=[o2, o1])
    assert patch_sha256(p12) != patch_sha256(p21)
    assert patch_sha256(p12) == patch_sha256(copy.deepcopy(p12))
    changed = copy.deepcopy(p12)
    changed["reason"] = "別の理由"
    assert patch_sha256(changed) != patch_sha256(p12)


def test_patch_set_sha256_order_independent_and_errors() -> None:
    a = _patch(target="characters/C001.yaml")
    b = _patch(target="foreshadowing/registry.yaml", ops=[{"op": "test", "path": "/0/id", "value": "F001"}])
    assert patch_set_sha256([a, b]) == patch_set_sha256([b, a])
    assert patch_set_sha256([a]) != patch_set_sha256([a, b])
    with pytest.raises(ValueError):
        patch_set_sha256([])
    with pytest.raises(ValueError):
        patch_set_sha256([a, copy.deepcopy(a)])


def test_is_noop_patch() -> None:
    assert is_noop_patch(_patch()) is True
    assert is_noop_patch(_patch(ops=[
        {"op": "test", "path": "/id", "value": "C001"},
        {"op": "add", "path": "/knowledge/-", "value": {"id": "K001"}},
    ])) is False
    assert is_noop_patch({"operations": []}) is False
