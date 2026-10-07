"""Tests for novui.statecheck (summary refs, patch policy, dry-run, consistency, helpers)."""

import copy
from pathlib import Path

import pytest

from novui.statecheck import (
    check_patch_policy,
    check_patch_target,
    check_summary_patch_consistency,
    check_summary_refs,
    derive_patch_targets,
    dry_run_patch,
    next_knowledge_number,
    pointer_hints,
)
from novui.yamlio import load_yaml

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "valid"
BASE_HASH = "sha256:" + "0" * 64
CH = "ch-001"
SCENES = ["S1", "S2"]
CHAR = "characters/C001.yaml"
REG = "foreshadowing/registry.yaml"


def _char() -> dict:
    return load_yaml(FIXTURES_DIR / "character.yaml")


def _reg() -> list:
    return load_yaml(FIXTURES_DIR / "registry.yaml")


def _summary() -> dict:
    return load_yaml(FIXTURES_DIR / "summary.yaml")


def _patch(target: str, ops: list) -> dict:
    return {"type": "state_patch", "target": target, "base_hash": BASE_HASH, "operations": ops, "reason": "理由"}


def _pos(scene: str = "S2", chapter: str = CH) -> dict:
    return {"chapter": chapter, "scene": scene}


def _change(value: str = "親友", scene: str = "S2", chapter: str = CH) -> dict:
    return {"value": value, "from": _pos(scene, chapter), "reason": "共に戦った"}


def _policy(patch: dict, doc: object, scenes: list | None = None):
    return check_patch_policy(patch, chapter_id=CH, scene_ids=scenes or SCENES, doc_before=doc)


# ---------- derive_patch_targets ----------

def test_derive_patch_targets() -> None:
    s = _summary()
    s["characters"] = [
        {"id": "C003", "location": None, "knowledge_added": ["x"], "items_gained": [], "items_lost": [],
         "condition": None, "relationship_changes": []},
        {"id": "C001", "location": None, "knowledge_added": [], "items_gained": [], "items_lost": [],
         "condition": None, "relationship_changes": [{"with": "C002", "change": "親しくなった"}]},
        {"id": "C002", "location": "x", "knowledge_added": [], "items_gained": ["鍵"], "items_lost": [],
         "condition": "疲労", "relationship_changes": []},
    ]
    assert derive_patch_targets(s) == ["characters/C001.yaml", "characters/C003.yaml", REG]
    s["foreshadowing"] = []
    assert derive_patch_targets(s) == ["characters/C001.yaml", "characters/C003.yaml"]
    s["characters"] = []
    assert derive_patch_targets(s) == []


# ---------- check_summary_refs ----------

def _refs(summary: dict, chars=("C001",), fores=("F001",)):
    return check_summary_refs(summary, chapter_id="ch-001", character_ids=chars, foreshadow_ids=fores)


def test_summary_refs_pass() -> None:
    r = _refs(_summary())
    assert r.name == "summary_refs" and r.status == "PASS" and r.details == ()


def test_summary_refs_failures() -> None:
    s = _summary()
    s["chapter_id"] = "ch-002"
    assert _refs(s).status == "FAIL"

    s = _summary()
    s["characters"].append(copy.deepcopy(s["characters"][0]))
    r = _refs(s)
    assert r.status == "FAIL" and any("duplicate character" in d for d in r.details)

    s = _summary()
    r = _refs(s, chars=("C009",))
    assert r.status == "FAIL" and any("no characters/C001.yaml" in d for d in r.details)

    s = _summary()
    r = _refs(s, fores=("F009",))
    assert r.status == "FAIL" and any("F001" in d for d in r.details)

    s = _summary()
    s["foreshadowing"].append(copy.deepcopy(s["foreshadowing"][0]))
    assert any("duplicate foreshadowing" in d for d in _refs(s).details)

    for bad in ("前{{x}}後", "後}}"):
        s = _summary()
        s["events"] = ["ok", bad]
        r = _refs(s)
        assert r.status == "FAIL" and any("/events/1" in d for d in r.details)
    s = _summary()
    s["characters"][0]["knowledge_added"] = ["{{boom"]
    assert any("/characters/0/knowledge_added/0" in d for d in _refs(s).details)


# ---------- check_patch_target ----------

def test_check_patch_target() -> None:
    p = _patch(CHAR, [{"op": "test", "path": "/id", "value": "C001"}])
    assert check_patch_target(p, CHAR).status == "PASS"
    r = check_patch_target(p, "characters/C002.yaml")
    assert r.name == "patch_target" and r.status == "FAIL"


# ---------- check_patch_policy: characters ----------

def _k(kid: str = "K015", fact: str = "新しい事実", source: str = CH) -> dict:
    return {"op": "add", "path": "/knowledge/-", "value": {"id": kid, "fact": fact, "source_chapter": source}}


def test_policy_character_allowed_examples() -> None:
    doc = _char()
    allowed = [
        [{"op": "test", "path": "/id", "value": "C001"}],
        [_k()],
        [_k("K015"), _k("K016", "別の事実")],
        [
            {"op": "test", "path": "/relationships/0/with", "value": "C002"},
            {"op": "add", "path": "/relationships/0/changes/-", "value": _change()},
        ],
        [{"op": "add", "path": "/relationships/-", "value": {"with": "C003", "state": "知り合い"}}],
        [{"op": "add", "path": "/address/C002/changes/-", "value": _change("あなた", "S1")}],
        [
            {"op": "test", "path": "/address/C003", "value": "先生"},
            {"op": "replace", "path": "/address/C003",
             "value": {"default": "先生", "changes": [_change("師匠")]}},
        ],
        [{"op": "add", "path": "/address/C004", "value": "君"}],
    ]
    for ops in allowed:
        r = _policy(_patch(CHAR, ops), doc)
        assert r.status == "PASS", (ops, r.details)
        assert r.name == "patch_policy"


def test_policy_character_forbidden_examples() -> None:
    doc = _char()
    cases = {
        "remove": [{"op": "remove", "path": "/knowledge/0"}],
        "move": [{"op": "move", "path": "/a", "from": "/b"}],
        "personality replace": [{"op": "replace", "path": "/personality", "value": ["x"]}],
        "personality add": [{"op": "add", "path": "/personality/-", "value": "x"}],
        "speech replace": [{"op": "replace", "path": "/speech/first_person", "value": "僕"}],
        "exceptions add": [{"op": "add", "path": "/exceptions/-", "value": {}}],
        "name replace": [{"op": "replace", "path": "/name", "value": "x"}],
        "notes add": [{"op": "add", "path": "/notes", "value": "x"}],
        "knowledge whole replace": [{"op": "replace", "path": "/knowledge", "value": []}],
        "knowledge item replace": [{"op": "replace", "path": "/knowledge/0/fact", "value": "x"}],
        "relationship change without test": [
            {"op": "add", "path": "/relationships/0/changes/-", "value": _change()}],
        "relationship change wrong test": [
            {"op": "test", "path": "/relationships/1/with", "value": "C002"},
            {"op": "add", "path": "/relationships/0/changes/-", "value": _change()}],
        "relationship state replace": [{"op": "replace", "path": "/relationships/0/state", "value": "x"}],
        "scene not in plan": [
            {"op": "test", "path": "/relationships/0/with", "value": "C002"},
            {"op": "add", "path": "/relationships/0/changes/-", "value": _change(scene="S9")}],
        "change from other chapter": [
            {"op": "add", "path": "/address/C002/changes/-", "value": _change(chapter="ch-002")}],
        "source chapter other": [_k(source="ch-002")],
        "bad knowledge id": [_k("K1")],
        "knowledge missing key": [{"op": "add", "path": "/knowledge/-", "value": {"id": "K015", "fact": "x"}}],
        "knowledge extra key": [{"op": "add", "path": "/knowledge/-",
                                 "value": {"id": "K015", "fact": "x", "source_chapter": CH, "z": 1}}],
        "duplicate id in patch": [_k("K015"), _k("K015", "別")],
        "existing knowledge id": [_k("K014")],
        "empty fact": [_k("K015", "")],
        "address string needs object": [
            {"op": "add", "path": "/address/C003/changes/-", "value": _change()}],
        "address replace without test": [
            {"op": "replace", "path": "/address/C003",
             "value": {"default": "先生", "changes": [_change("師匠")]}}],
        "address replace wrong default": [
            {"op": "test", "path": "/address/C003", "value": "先生"},
            {"op": "replace", "path": "/address/C003",
             "value": {"default": "別", "changes": [_change("師匠")]}}],
        "address replace empty changes": [
            {"op": "test", "path": "/address/C003", "value": "先生"},
            {"op": "replace", "path": "/address/C003", "value": {"default": "先生", "changes": []}}],
        "address replace of object": [
            {"op": "test", "path": "/address/C002", "value": {}},
            {"op": "replace", "path": "/address/C002", "value": {"default": "x", "changes": [_change()]}}],
        "address add existing key": [{"op": "add", "path": "/address/C003", "value": "x"}],
        "bad pointer": [{"op": "add", "path": "knowledge/-", "value": {}}],
        "bad relationship value": [{"op": "add", "path": "/relationships/-", "value": {"with": "x", "state": "y"}}],
    }
    for label, ops in cases.items():
        patch = _patch(CHAR, ops)
        r = _policy(patch, doc)
        assert r.status == "FAIL", label
        assert r.details, label


def test_policy_character_allows_change_after_converting_string_address() -> None:
    doc = _char()
    ops = [
        {"op": "test", "path": "/address/C003", "value": "先生"},
        {"op": "replace", "path": "/address/C003", "value": {"default": "先生", "changes": [_change("師匠")]}},
        {"op": "add", "path": "/address/C003/changes/-", "value": _change("父さん", "S1")},
    ]
    assert _policy(_patch(CHAR, ops), doc).status == "PASS"


def test_policy_target_not_allowed() -> None:
    ops = [{"op": "test", "path": "/id", "value": "x"}]
    for t in ("world/setting.md", "characters/C1.yaml", "characters/C001.yml", "plot/timeline.yaml", "project.yaml"):
        r = _policy(_patch(t, ops), {})
        assert r.status == "FAIL" and "not allowed" in r.details[0]


# ---------- check_patch_policy: registry ----------

def _t(i: int = 0, fid: str = "F001") -> dict:
    return {"op": "test", "path": f"/{i}/id", "value": fid}


def test_policy_registry_allowed_examples() -> None:
    doc = _reg()
    allowed = [
        [_t()],
        [_t(), {"op": "add", "path": "/0/hints/-", "value": _pos("S1")}],
        [_t(), {"op": "add", "path": "/0/introduced/-", "value": _pos("S1")},
         {"op": "add", "path": "/0/developments/-", "value": _pos("S2")}],
        [_t(), {"op": "replace", "path": "/0/status", "value": "active"}],
        [_t(), {"op": "replace", "path": "/0/status", "value": "resolved"},
         {"op": "replace", "path": "/0/resolved", "value": _pos("S2")}],
        # several foreshadows, each guarded by its own test
        [_t(0, "F001"), {"op": "add", "path": "/0/hints/-", "value": _pos("S1")},
         _t(1, "F002"), {"op": "add", "path": "/1/hints/-", "value": _pos("S2")}],
    ]
    for ops in allowed:
        r = _policy(_patch(REG, ops), doc)
        assert r.status == "PASS", (ops, r.details)


def test_policy_registry_forbidden_examples() -> None:
    doc = _reg()
    cases = {
        "remove": [_t(), {"op": "remove", "path": "/0/hints/0"}],
        "no leading test": [{"op": "add", "path": "/0/hints/-", "value": _pos("S1")}],
        "wrong leading test path": [{"op": "test", "path": "/0/name", "value": "x"},
                                    {"op": "add", "path": "/0/hints/-", "value": _pos("S1")}],
        "second index unguarded": [_t(), {"op": "add", "path": "/1/hints/-", "value": _pos("S1")}],
        "planned_resolution": [_t(), {"op": "replace", "path": "/0/planned_resolution", "value": _pos("S1")}],
        "cancelled": [_t(), {"op": "replace", "path": "/0/status", "value": "cancelled"}],
        "bad status": [_t(), {"op": "replace", "path": "/0/status", "value": "weird"}],
        "resolved status without position": [_t(), {"op": "replace", "path": "/0/status", "value": "resolved"}],
        "scene not in plan": [_t(), {"op": "add", "path": "/0/hints/-", "value": _pos("S9")}],
        "other chapter": [_t(), {"op": "add", "path": "/0/hints/-", "value": _pos("S1", "ch-002")}],
        "position extra key": [_t(), {"op": "add", "path": "/0/hints/-",
                                      "value": {"chapter": CH, "scene": "S1", "x": 1}}],
        "add whole entry": [{"op": "add", "path": "/-", "value": {"id": "F009"}}],
        "add name": [_t(), {"op": "add", "path": "/0/name", "value": "x"}],
        "replace hints list": [_t(), {"op": "replace", "path": "/0/hints", "value": []}],
        "replace notes": [_t(), {"op": "replace", "path": "/0/notes", "value": "x"}],
        "pointer not index": [{"op": "test", "path": "/x/id", "value": "F001"}],
        "root pointer": [{"op": "test", "path": "", "value": []}],
    }
    for label, ops in cases.items():
        r = _policy(_patch(REG, ops), doc)
        assert r.status == "FAIL", label
        assert r.details, label


# ---------- dry_run_patch ----------

def test_dry_run_pass_character_and_registry() -> None:
    r = dry_run_patch(_char(), _patch(CHAR, [_k()]))
    assert r.name == "patch_apply" and r.status == "PASS", r.details
    r = dry_run_patch(_reg(), _patch(REG, [_t(), {"op": "add", "path": "/0/hints/-", "value": _pos("S1")}]))
    assert r.status == "PASS", r.details


def test_dry_run_failures() -> None:
    # index drift: the test op fails
    r = dry_run_patch(_reg(), _patch(REG, [_t(5)]))
    assert r.status == "FAIL"
    # result violates the schema (knowledge item without fact)
    r = dry_run_patch(_char(), _patch(CHAR, [
        {"op": "add", "path": "/knowledge/-", "value": {"id": "K015", "source_chapter": CH}}]))
    assert r.status == "FAIL" and any(d.startswith("schema:") for d in r.details)
    # result violates check_registry (resolved status without position)
    r = dry_run_patch(_reg(), _patch(REG, [_t(), {"op": "replace", "path": "/0/status", "value": "resolved"}]))
    assert r.status == "FAIL" and any(d.startswith("semantics:") for d in r.details)
    # result violates check_character (duplicate knowledge id)
    r = dry_run_patch(_char(), _patch(CHAR, [_k("K014")]))
    assert r.status == "FAIL" and any("Duplicate knowledge id" in d for d in r.details)
    # the patch itself is invalid
    bad = _patch(CHAR, [_k()])
    bad["operations"] = []
    assert dry_run_patch(_char(), bad).status == "FAIL"
    # unsupported target
    assert dry_run_patch({}, _patch("world/setting.md", [_k()])).status == "FAIL"


def test_dry_run_does_not_modify_input() -> None:
    doc = _char()
    snapshot = copy.deepcopy(doc)
    dry_run_patch(doc, _patch(CHAR, [_k()]))
    assert doc == snapshot


# ---------- check_summary_patch_consistency ----------

def test_consistency() -> None:
    s = _summary()
    s["characters"][0]["knowledge_added"] = ["事実A", "事実B"]
    ok = _patch(CHAR, [_k("K015", "事実A"), _k("K016", "事実B")])
    r = check_summary_patch_consistency(s, [ok])
    assert r.name == "summary_patch_consistency" and r.status == "PASS"

    partial = _patch(CHAR, [_k("K015", "事実A")])
    r = check_summary_patch_consistency(s, [partial])
    assert r.status == "WARNING" and r.details == ("C001: 事実B",)

    r = check_summary_patch_consistency(s, [])
    assert r.status == "WARNING" and len(r.details) == 2

    other = _patch("characters/C002.yaml", [_k("K015", "事実A"), _k("K016", "事実B")])
    assert check_summary_patch_consistency(s, [other]).status == "WARNING"

    s["characters"][0]["knowledge_added"] = []
    assert check_summary_patch_consistency(s, []).status == "PASS"


# ---------- next_knowledge_number / pointer_hints ----------

def test_next_knowledge_number() -> None:
    assert next_knowledge_number([]) == 1
    assert next_knowledge_number([{"id": "C001"}]) == 1
    a = {"knowledge": [{"id": "K014"}, {"id": "K002"}]}
    b = {"knowledge": [{"id": "K020"}]}
    assert next_knowledge_number([a, b]) == 21
    assert next_knowledge_number([_char()]) == 15


def test_pointer_hints() -> None:
    reg = _reg() + [dict(_reg()[0], id="F002")]
    assert pointer_hints(REG, reg) == "F001 → /0\nF002 → /1"
    assert pointer_hints(REG, []) == "なし"
    hints = pointer_hints(CHAR, _char())
    assert "関係：C002 → /relationships/0" in hints
    assert "呼び方：C002 → /address/C002（object）" in hints
    assert "呼び方：C003 → /address/C003（文字列）" in hints
    assert "default" not in hints
    assert pointer_hints(CHAR, {"id": "C001"}) == "なし"
