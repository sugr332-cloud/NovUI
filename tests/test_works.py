"""Tests for novui.works module."""

from pathlib import Path
import pytest

from novui.config import Settings
from novui.works import (
    WorkInfo,
    get_work,
    list_works,
    register_work,
    registry_path,
)


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


def test_works_registry_lifecycle(tmp_path: Path) -> None:
    settings = _make_settings(tmp_path)

    # 1. 初期状態は空
    assert list_works(settings) == []
    with pytest.raises(KeyError):
        get_work(settings, "work-1")

    # 2. 登録
    work_path = tmp_path / "repo1"
    info = register_work(settings, "work-1", work_path, 1)
    assert isinstance(info, WorkInfo)
    assert info.work_key == "work-1"
    assert info.path == work_path.resolve(strict=False)
    assert info.format_version == 1
    assert info.state == "active"

    # 3. 取得
    retrieved = get_work(settings, "work-1")
    assert retrieved == info
    assert list_works(settings) == [info]

    # 4. 重複登録で ValueError
    with pytest.raises(ValueError, match="already registered"):
        register_work(settings, "work-1", tmp_path / "repo2", 1)

    # 5. 相対パスで ValueError
    with pytest.raises(ValueError, match="Path must be absolute"):
        register_work(settings, "work-2", Path("relative/path"), 1)
