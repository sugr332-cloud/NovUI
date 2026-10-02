"""Tests for novui.models module."""

from pathlib import Path
import pytest

from novui.config import Settings
from novui.models import (
    ModelUnavailable,
    fetch_agy_models,
    load_catalog,
    load_controller_settings,
    new_model_ids,
    parse_agy_models,
    resolve_model,
    save_catalog,
    select_model,
)
from novui.procrun import ProcResult


def _make_settings(tmp_path: Path) -> Settings:
    data_dir = tmp_path / "data"
    worktree_root = data_dir / "worktrees"
    jobhome_root = data_dir / "jobhomes"
    token_path = tmp_path / "fake-token"
    token_path.write_text("token", encoding="utf-8")
    return Settings(
        data_dir=data_dir,
        worktree_root=worktree_root,
        jobhome_root=jobhome_root,
        agy_image="test:img",
        agy_token_path=token_path,
        timeouts={"agy_draft": 300},
    )


def test_parse_agy_models() -> None:
    sample_output = """
[Warning: deprecated feature used]
Available:
gemini-3.8-flash-high   Gemini 3.8 Flash High (Fast, high capability)
gemini-2.5-pro          Gemini 2.5 Pro (Deep reasoning)
gemini-2.5-flash
Invalid:model           Invalid because of colon
"""
    models = parse_agy_models(sample_output)
    # [Warning: starts with [, Available: ends with :, Invalid:model has colon
    ids = [m["id"] for m in models]
    assert "gemini-3.8-flash-high" in ids
    assert "gemini-2.5-pro" in ids
    assert "gemini-2.5-flash" in ids
    assert "[Warning:" not in ids
    assert "Available:" not in ids
    assert "Invalid:model" not in ids

    # Check labels
    m_dict = {m["id"]: m["label"] for m in models}
    assert m_dict["gemini-3.8-flash-high"] == "Gemini 3.8 Flash High (Fast, high capability)"
    assert m_dict["gemini-2.5-pro"] == "Gemini 2.5 Pro (Deep reasoning)"
    assert m_dict["gemini-2.5-flash"] == ""


def test_fetch_agy_models(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _make_settings(tmp_path)

    def mock_runner(*args, **kwargs) -> ProcResult:
        stdout = (
            b"gemini-3.8-flash-high Flash High\n"
            b"gemini-2.5-pro Pro\n"
        )
        return ProcResult(
            exit_code=0,
            elapsed_seconds=1.5,
            timed_out=False,
            signal_sent=None,
            group_remaining=False,
            stdout=stdout,
            stderr=b"",
        )

    monkeypatch.setattr("novui.models.image_label", lambda img, key: "1.2.14")
    monkeypatch.setattr("novui.models.image_id", lambda img: "sha256:1234567890abcdef1234567890abcdef1234567890abcdef1234567890abcdef")

    cat = fetch_agy_models(settings, container_runner=mock_runner)
    assert cat["agy_image"] == "test:img"
    assert cat["agy_version"] == "1.2.14"
    assert cat["image_id"] == "sha256:1234567890abcdef1234567890abcdef1234567890abcdef1234567890abcdef"
    assert len(cat["models"]) == 2


def test_save_and_load_catalog(tmp_path: Path) -> None:
    settings = _make_settings(tmp_path)
    assert load_catalog(settings) is None

    catalog_data = {
        "fetched_at": "2026-10-02T12:00:00+09:00",
        "agy_image": "test:img",
        "image_id": "sha256:1234567890abcdef1234567890abcdef1234567890abcdef1234567890abcdef",
        "agy_version": "1.2.14",
        "models": [
            {"id": "gemini-3.8-flash-high", "label": "Flash High"},
            {"id": "gemini-2.5-pro", "label": "Pro"},
        ],
    }
    saved_path = save_catalog(settings, catalog_data)
    assert saved_path.is_file()

    loaded = load_catalog(settings)
    assert loaded == catalog_data


def test_new_model_ids() -> None:
    old = {
        "models": [
            {"id": "m1", "label": "Model 1"},
            {"id": "m2", "label": "Model 2"},
        ]
    }
    new = {
        "models": [
            {"id": "m1", "label": "Model 1"},
            {"id": "m2", "label": "Model 2"},
            {"id": "m3", "label": "Model 3"},
        ]
    }
    assert new_model_ids(old, new) == ["m3"]
    assert new_model_ids(None, new) == ["m1", "m2", "m3"]


def test_select_and_resolve_model(tmp_path: Path) -> None:
    settings = _make_settings(tmp_path)

    # 初期状態：カタログも設定もない
    with pytest.raises(ValueError):
        select_model(settings, "invalid_role", "gemini-3.8-flash-high")

    # カタログなしで select -> ModelUnavailable
    with pytest.raises(ModelUnavailable):
        select_model(settings, "draft", "gemini-3.8-flash-high")

    # カタログを用意
    catalog_data = {
        "fetched_at": "2026-10-02T12:00:00+09:00",
        "agy_image": "test:img",
        "image_id": "sha256:1234567890abcdef1234567890abcdef1234567890abcdef1234567890abcdef",
        "agy_version": "1.2.14",
        "models": [
            {"id": "gemini-3.8-flash-high", "label": "Flash High"},
            {"id": "gemini-2.5-pro", "label": "Pro"},
        ],
    }
    save_catalog(settings, catalog_data)

    # カタログにないモデルを指定 -> ModelUnavailable
    with pytest.raises(ModelUnavailable):
        select_model(settings, "draft", "gemini-nonexistent")

    # グローバル設定
    select_model(settings, "draft", "gemini-3.8-flash-high")
    assert resolve_model(settings, "draft") == "gemini-3.8-flash-high"
    assert resolve_model(settings, "draft", work_key="work-a") == "gemini-3.8-flash-high"

    # 作品別設定
    select_model(settings, "draft", "gemini-2.5-pro", work_key="work-a")
    assert resolve_model(settings, "draft", work_key="work-a") == "gemini-2.5-pro"
    assert resolve_model(settings, "draft") == "gemini-3.8-flash-high"

    # 未設定のロール -> ModelUnavailable
    with pytest.raises(ModelUnavailable):
        resolve_model(settings, "range_edit")

    # カタログからモデルが消えた場合、resolve_model で ModelUnavailable
    updated_catalog = {
        "fetched_at": "2026-10-02T12:05:00+09:00",
        "agy_image": "test:img",
        "image_id": "sha256:1234567890abcdef1234567890abcdef1234567890abcdef1234567890abcdef",
        "agy_version": "1.2.14",
        "models": [
            {"id": "gemini-2.5-pro", "label": "Pro"},
        ],
    }
    save_catalog(settings, updated_catalog)
    # draft (gemini-3.8-flash-high) はカタログから消えたので ModelUnavailable
    with pytest.raises(ModelUnavailable):
        resolve_model(settings, "draft")
    # work-a の draft (gemini-2.5-pro) は残っているので成功
    assert resolve_model(settings, "draft", work_key="work-a") == "gemini-2.5-pro"
