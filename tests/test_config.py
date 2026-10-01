"""Tests for novui.config module."""

from pathlib import Path
import pytest

from novui.config import DEFAULT_TIMEOUTS, load_settings


def test_default_settings() -> None:
    settings = load_settings({})
    assert settings.data_dir == Path.home() / ".local/share/novui"
    assert settings.worktree_root == settings.data_dir / "worktrees"
    assert settings.jobhome_root == settings.data_dir / "jobhomes"
    assert settings.agy_image == "localhost/novui-spike:agy-1.2.14"
    assert settings.agy_token_path == Path.home() / ".gemini/antigravity-cli/antigravity-oauth-token"
    assert settings.timeouts == DEFAULT_TIMEOUTS


def test_env_override_and_tilde_expansion() -> None:
    env = {
        "NOVUI_DATA_DIR": "~/custom_novui",
        "NOVUI_AGY_IMAGE": "my-registry/image:v1",
        "NOVUI_AGY_TOKEN": "~/custom_token",
    }
    settings = load_settings(env)
    assert settings.data_dir == Path.home() / "custom_novui"
    assert settings.worktree_root == settings.data_dir / "worktrees"
    assert settings.jobhome_root == settings.data_dir / "jobhomes"
    assert settings.agy_image == "my-registry/image:v1"
    assert settings.agy_token_path == Path.home() / "custom_token"


def test_relative_path_raises_value_error() -> None:
    with pytest.raises(ValueError):
        load_settings({"NOVUI_DATA_DIR": "relative/path"})

    with pytest.raises(ValueError):
        load_settings({"NOVUI_AGY_TOKEN": "relative/token"})


def test_load_settings_does_not_create_files(tmp_path: Path) -> None:
    target_data_dir = tmp_path / "non_existent_data_dir"
    target_token = tmp_path / "non_existent_token"

    env = {
        "NOVUI_DATA_DIR": str(target_data_dir),
        "NOVUI_AGY_TOKEN": str(target_token),
    }
    load_settings(env)

    # 読み込み処理がディレクトリやファイルを作成しないこと
    assert not target_data_dir.exists()
    assert not target_token.exists()
