"""Tests for novui.schema module."""

import copy
from pathlib import Path
import pytest

from novui.schema import SCHEMA_DIR, bundle_schema, load_registry, validate, validate_or_raise
from novui.semantics import SEMANTIC_CHECKS
from novui.yamlio import load_yaml
from jsonschema import Draft202012Validator

import json

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "valid"

ALL_19_SCHEMAS = [
    "chapter",
    "plan",
    "integrity_review",
    "writing_review",
    "review",
    "requests",
    "summary",
    "state_patch",
    "instruction_routing",
    "approval",
    "job_record",
    "character",
    "registry",
    "model_catalog",
    "controller_settings",
    "project",
    "chapters_order",
    "prohibited",
    "works_registry",
]


def test_load_registry_all_schemas() -> None:
    # schemas/ の全ファイルが check_schema を通り、registry に登録される
    registry = load_registry(SCHEMA_DIR)
    assert registry is not None


@pytest.mark.parametrize("schema_name", ALL_19_SCHEMAS)
def test_valid_fixtures_pass_schema_and_semantics(schema_name: str) -> None:
    # 各正例フィクスチャが検証エラー0件、かつ semantics エラー0件
    path = FIXTURES_DIR / f"{schema_name}.yaml"
    assert path.is_file(), f"Fixture missing: {path}"
    doc = load_yaml(path)

    errors = validate(doc, schema_name)
    assert errors == [], f"Schema errors in {schema_name}: {errors}"

    semantic_errors = SEMANTIC_CHECKS[schema_name](doc)
    assert semantic_errors == [], f"Semantic errors in {schema_name}: {semantic_errors}"

    # validate_or_raise does not raise
    validate_or_raise(doc, schema_name)


@pytest.mark.parametrize("schema_name", ALL_19_SCHEMAS)
def test_missing_required_property_fails(schema_name: str) -> None:
    schema_file = SCHEMA_DIR / f"{schema_name}.schema.json"
    schema_data = json.loads(schema_file.read_text(encoding="utf-8"))

    if schema_data.get("type") == "array" and "$ref" in schema_data.get("items", {}):
        pytest.skip(f"Schema {schema_name} is an array of scalar/ref values without required properties")

    path = FIXTURES_DIR / f"{schema_name}.yaml"
    doc = load_yaml(path)
    corrupted = copy.deepcopy(doc)

    if isinstance(corrupted, dict):
        required_keys = schema_data.get("required", [])
        assert required_keys, f"Schema {schema_name} has no top-level required keys"
        target_key = required_keys[0]
        del corrupted[target_key]
    elif isinstance(corrupted, list) and corrupted:
        items_schema = schema_data.get("items", {})
        required_keys = items_schema.get("required")
        if not required_keys and "oneOf" in items_schema:
            for branch in items_schema["oneOf"]:
                branch_type = branch.get("properties", {}).get("type", {}).get("const")
                if corrupted[0].get("type") == branch_type:
                    required_keys = branch.get("required")
                    break
            if not required_keys:
                required_keys = items_schema["oneOf"][0].get("required", [])
        assert required_keys, f"Array schema {schema_name} items has no required keys"
        target_key = required_keys[0]
        del corrupted[0][target_key]

    errors = validate(corrupted, schema_name)
    assert len(errors) > 0, f"Expected validation error for missing property in {schema_name}"


@pytest.mark.parametrize("schema_name", ALL_19_SCHEMAS)
def test_additional_property_rejected(schema_name: str) -> None:
    path = FIXTURES_DIR / f"{schema_name}.yaml"
    doc = load_yaml(path)
    corrupted = copy.deepcopy(doc)

    if isinstance(corrupted, dict):
        corrupted["unexpected_extra_field"] = "some_value"
    elif isinstance(corrupted, list) and corrupted:
        if not isinstance(corrupted[0], dict):
            pytest.skip(f"Schema {schema_name} is an array of non-object items")
        corrupted[0]["unexpected_extra_field"] = "some_value"

    errors = validate(corrupted, schema_name)
    assert len(errors) > 0, f"Expected additionalProperties error in {schema_name}"


def test_specific_negative_cases() -> None:
    # 1. chapter_id: 'ch-1' (3桁未満)
    chapter_doc = load_yaml(FIXTURES_DIR / "chapter.yaml")
    bad_ch = copy.deepcopy(chapter_doc)
    bad_ch["id"] = "ch-1"
    assert len(validate(bad_ch, "chapter")) > 0

    # 2. datetime: '2026-10-01 20:10:00' (T がなく空白)
    bad_dt = copy.deepcopy(chapter_doc)
    bad_dt["review_required"] = True
    bad_dt["review_reasons"] = [{
        "code": "external_change",
        "detail": "test",
        "at": "2026-10-01 20:10:00",
    }]
    assert len(validate(bad_dt, "chapter")) > 0

    # 3. sha256: 大文字16進
    patch_doc = load_yaml(FIXTURES_DIR / "state_patch.yaml")
    bad_hash = copy.deepcopy(patch_doc)
    bad_hash["base_hash"] = "sha256:0123456789ABCDEF0123456789abcdef0123456789abcdef0123456789abcdef"
    assert len(validate(bad_hash, "state_patch")) > 0

    # 4. state_patch: op 'move' は不許可
    bad_op = copy.deepcopy(patch_doc)
    bad_op["operations"] = [{"op": "move", "path": "/foo", "from": "/bar"}]
    assert len(validate(bad_op, "state_patch")) > 0

    # 5. state_patch: op 'add' で value なし
    bad_add = copy.deepcopy(patch_doc)
    bad_add["operations"] = [{"op": "add", "path": "/foo"}]
    assert len(validate(bad_add, "state_patch")) > 0

    # 6. requests: type 不明
    req_doc = load_yaml(FIXTURES_DIR / "requests.yaml")
    bad_req = copy.deepcopy(req_doc)
    bad_req[0]["type"] = "unknown_type"
    assert len(validate(bad_req, "requests")) > 0

    # 7. review: integrity に壊れた integrity_review (checks が欠落)
    rev_doc = load_yaml(FIXTURES_DIR / "review.yaml")
    bad_rev = copy.deepcopy(rev_doc)
    bad_rev["integrity"] = {
        "type": "integrity_review",
        "chapter_id": "ch-001",
        "result": "PASS",
        # checks is missing
    }
    assert len(validate(bad_rev, "review")) > 0

    # 8. plan: scenes[].foreshadowing に '王家の紋章'（F001形式でない）
    plan_doc = load_yaml(FIXTURES_DIR / "plan.yaml")
    bad_plan = copy.deepcopy(plan_doc)
    bad_plan["scenes"][0]["foreshadowing"] = ["王家の紋章"]
    assert len(validate(bad_plan, "plan")) > 0

    # 9. character: address に 'X001' キー（C001形式でない）
    char_doc = load_yaml(FIXTURES_DIR / "character.yaml")
    bad_char_address = copy.deepcopy(char_doc)
    bad_char_address["address"]["X001"] = "先生"
    assert len(validate(bad_char_address, "character")) > 0

    # 10. character: speech.formality に 'rude'（casual, polite, mixed 以外）
    bad_char_formality = copy.deepcopy(char_doc)
    bad_char_formality["speech"]["formality"] = "rude"
    assert len(validate(bad_char_formality, "character")) > 0

    # 11. registry: status 'done'（planned, active, resolved, cancelled 以外）
    reg_doc = load_yaml(FIXTURES_DIR / "registry.yaml")
    bad_reg_status = copy.deepcopy(reg_doc)
    bad_reg_status[0]["status"] = "done"
    assert len(validate(bad_reg_status, "registry")) > 0


def test_unknown_schema_name_value_error() -> None:
    with pytest.raises(ValueError) as exc_info:
        validate({}, "non_existent_schema")
    assert "Unknown schema" in str(exc_info.value)


def test_job_record_with_cli_info() -> None:
    doc = load_yaml(FIXTURES_DIR / "job_record.yaml")

    # Claude: actual_model が文字列
    claude_doc = copy.deepcopy(doc)
    claude_doc["cli"] = {
        "name": "claude",
        "version": "1.0.0",
        "model": "claude-sonnet-4-6",
        "actual_model": "claude-opus-5-5",
    }
    assert validate(claude_doc, "job_record") == []
    assert SEMANTIC_CHECKS["job_record"](claude_doc) == []

    # AGY: actual_model が null
    agy_doc = copy.deepcopy(doc)
    agy_doc["cli"] = {
        "name": "agy",
        "version": "1.2.14",
        "model": "gemini-3.8-flash-high",
        "actual_model": None,
    }
    assert validate(agy_doc, "job_record") == []
    assert SEMANTIC_CHECKS["job_record"](agy_doc) == []

    # actual_model 欠落でエラー
    missing_actual = copy.deepcopy(agy_doc)
    del missing_actual["cli"]["actual_model"]
    assert len(validate(missing_actual, "job_record")) > 0


def _assert_no_external_refs(node: object) -> None:
    if isinstance(node, dict):
        if "$ref" in node:
            ref = node["$ref"]
            assert isinstance(ref, str)
            assert ref.startswith("#/"), f"External or non-#/ $ref found: {ref!r}"
        for v in node.values():
            _assert_no_external_refs(v)
    elif isinstance(node, list):
        for item in node:
            _assert_no_external_refs(item)


NEGATIVE_SAMPLES: dict[str, object] = {
    "chapter": {"id": "ch-001"},
    "plan": {"chapter_id": "ch-001"},
    "integrity_review": {"chapter_id": "ch-001"},
    "writing_review": {"chapter_id": "ch-001"},
    "review": {"chapter_id": "ch-001"},
    "requests": [{"type": "unknown_type"}],
    "summary": {"chapter_id": "ch-001"},
    "state_patch": {"patch_id": "P-001"},
    "instruction_routing": {"instruction_id": "I-001"},
    "approval": {"approval_id": "A-0001"},
    "job_record": {"job_id": "job-1"},
    "character": {"id": "C001"},
    "registry": [{"id": "INVALID"}],
    "model_catalog": {"fetched_at": "not-a-datetime"},
    "controller_settings": {"format_version": 999},
    "project": {"work_key": "INVALID KEY!"},
    "chapters_order": ["invalid-chapter-id"],
    "prohibited": {"terms": "not-a-list"},
    "works_registry": {"format_version": 1, "works": "not-a-dict"},
}


@pytest.mark.parametrize("schema_name", ALL_19_SCHEMAS)
def test_bundle_schema_all_19(schema_name: str) -> None:
    bundled = bundle_schema(schema_name)
    assert "$id" not in bundled
    assert "$schema" in bundled

    # 1. Draft202012Validator.check_schema を通る
    Draft202012Validator.check_schema(bundled)

    # 2. #/ で始まらない $ref を含まない
    _assert_no_external_refs(bundled)

    # 3. tests/fixtures/valid/ の正例が bundle 後の schema（registry なし）で検証エラー0件
    fixture_path = FIXTURES_DIR / f"{schema_name}.yaml"
    assert fixture_path.is_file()
    valid_doc = load_yaml(fixture_path)
    validator = Draft202012Validator(bundled)
    errors = list(validator.iter_errors(valid_doc))
    assert errors == [], f"Validation errors on valid fixture with bundled {schema_name}: {errors}"

    # 4. 負例でエラーになる
    neg_doc = NEGATIVE_SAMPLES[schema_name]
    neg_errors = list(validator.iter_errors(neg_doc))
    assert len(neg_errors) > 0, f"Expected validation failure for negative sample on {schema_name}"


def test_bundle_schema_unknown_and_invalid_ref(tmp_path: Path) -> None:
    # 存在しないスキーマ名で ValueError
    with pytest.raises(ValueError, match="Unknown schema"):
        bundle_schema("non_existent_schema")

    # 不正な $ref 形式で ValueError
    bad_schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "properties": {
            "foo": {"$ref": "#/properties/bar"},
        },
    }
    schema_dir = tmp_path / "bad_schemas"
    schema_dir.mkdir()
    (schema_dir / "bad.schema.json").write_text(json.dumps(bad_schema), encoding="utf-8")

    with pytest.raises(ValueError, match="Invalid or unsupported \\$ref"):
        bundle_schema("bad", schema_dir=schema_dir)


