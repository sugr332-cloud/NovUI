"""Tests for novui.schema module."""

import copy
from pathlib import Path
import pytest

from novui.schema import SCHEMA_DIR, load_registry, validate, validate_or_raise
from novui.semantics import SEMANTIC_CHECKS
from novui.yamlio import load_yaml

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "valid"

ALL_11_SCHEMAS = [
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
]


def test_load_registry_all_schemas() -> None:
    # schemas/ の全ファイルが check_schema を通り、registry に登録される
    registry = load_registry(SCHEMA_DIR)
    assert registry is not None


@pytest.mark.parametrize("schema_name", ALL_11_SCHEMAS)
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


@pytest.mark.parametrize("schema_name", ALL_11_SCHEMAS)
def test_missing_required_property_fails(schema_name: str) -> None:
    path = FIXTURES_DIR / f"{schema_name}.yaml"
    doc = load_yaml(path)
    corrupted = copy.deepcopy(doc)

    if isinstance(corrupted, dict):
        # 必須プロパティを1つ削除
        first_key = next(iter(corrupted.keys()))
        del corrupted[first_key]
    elif isinstance(corrupted, list) and corrupted:
        # requests のようにトップレベルが配列の場合は先頭要素の必須プロパティを削除
        first_key = next(iter(corrupted[0].keys()))
        del corrupted[0][first_key]

    errors = validate(corrupted, schema_name)
    assert len(errors) > 0, f"Expected validation error for missing property in {schema_name}"


@pytest.mark.parametrize("schema_name", ALL_11_SCHEMAS)
def test_additional_property_rejected(schema_name: str) -> None:
    path = FIXTURES_DIR / f"{schema_name}.yaml"
    doc = load_yaml(path)
    corrupted = copy.deepcopy(doc)

    if isinstance(corrupted, dict):
        corrupted["unexpected_extra_field"] = "some_value"
    elif isinstance(corrupted, list) and corrupted:
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


def test_unknown_schema_name_value_error() -> None:
    with pytest.raises(ValueError) as exc_info:
        validate({}, "non_existent_schema")
    assert "Unknown schema" in str(exc_info.value)
