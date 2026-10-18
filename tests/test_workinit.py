"""Tests for novui.workinit module."""

from pathlib import Path
import pytest

from novui.config import Settings
from novui.gitinspect import run_git
from novui.workinit import TEMPLATE_DIR, init_work
from novui.works import get_work
from novui.yamlio import load_yaml


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
        git_name="Tester",
        git_email="tester@novui.local",
    )


def test_init_work_success(tmp_path: Path) -> None:
    settings = _make_settings(tmp_path)
    target_repo = tmp_path / "novel_repo"

    info = init_work(settings, target_repo, "my-work", "港町の物語")

    assert info.work_key == "my-work"
    assert info.path == target_repo.resolve(strict=False)
    assert info.format_version == 1
    assert info.state == "active"

    # 1. テンプレートファイルがすべて存在することを確認
    assert (target_repo / "project.yaml").is_file()
    assert (target_repo / "chapters-order.yaml").is_file()
    assert (target_repo / "world" / "README.md").is_file()
    assert (target_repo / "characters" / ".keep").is_file()
    assert (target_repo / "plot" / "timeline.yaml").is_file()
    assert (target_repo / "foreshadowing" / "registry.yaml").is_file()
    assert (target_repo / "rules" / "style.md").is_file()
    assert (target_repo / "rules" / "prohibited.yaml").is_file()
    assert (target_repo / ".novui" / "approvals" / ".keep").is_file()
    assert (target_repo / ".gitignore").is_file()

    # 2. project.yaml の内容
    p_data = load_yaml(target_repo / "project.yaml")
    assert p_data == {
        "format_version": 1,
        "work_key": "my-work",
        "title": "港町の物語",
    }

    # 3. Git commit と trailer
    log_msg = run_git(target_repo, "log", "-1", "--format=%B").decode("utf-8")
    assert "init work my-work" in log_msg
    assert "NovUI-Edit: human-content" in log_msg

    # 4. registry に登録されていること
    registered = get_work(settings, "my-work")
    assert registered == info


def test_init_work_errors(tmp_path: Path) -> None:
    settings = _make_settings(tmp_path)

    # 1. 既存のパスで FileExistsError
    existing_dir = tmp_path / "exists"
    existing_dir.mkdir()
    with pytest.raises(FileExistsError):
        init_work(settings, existing_dir, "w1", "Title")

    # 2. 親ディレクトリが存在しないパスで FileNotFoundError
    nested_path = tmp_path / "nonexistent_parent" / "target"
    with pytest.raises(FileNotFoundError):
        init_work(settings, nested_path, "w1", "Title")

    # 3. 不正な work_key で ValueError
    with pytest.raises(ValueError, match="Invalid work_key"):
        init_work(settings, tmp_path / "r1", "INVALID_KEY!", "Title")
    with pytest.raises(ValueError, match="Invalid work_key"):
        init_work(settings, tmp_path / "r1", "-invalid", "Title")

    # 4. 空の title で ValueError
    with pytest.raises(ValueError, match="title cannot be empty"):
        init_work(settings, tmp_path / "r2", "w2", "")
    with pytest.raises(ValueError, match="title cannot be empty"):
        init_work(settings, tmp_path / "r2", "w2", "   \n")

    # 5. 登録済みの work_key で ValueError
    r3 = tmp_path / "r3"
    init_work(settings, r3, "w3", "Title 3")
    with pytest.raises(ValueError, match="already registered"):
        init_work(settings, tmp_path / "r4", "w3", "Duplicate Key")
