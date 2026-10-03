"""Tests for novui.chapters module."""

from pathlib import Path
import pytest

from novui.chapters import (
    ChapterError,
    add_chapter,
    ensure_main_ready,
    read_chapter_meta,
    read_chapters_order,
    transition_on_main,
)
from novui.config import Settings
from novui.gitinspect import run_git
from novui.schema import SchemaError
from novui.states import ChapterEvent, ChapterState, InvalidTransition
from novui.workinit import init_work
from novui.workrepo import create_job_worktree, remove_job_worktree
from novui.works import WorkInfo
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


@pytest.fixture
def test_work(tmp_path: Path) -> tuple[Settings, WorkInfo]:
    settings = _make_settings(tmp_path)
    repo_path = tmp_path / "novel_repo"
    work = init_work(settings, repo_path, "test-novel", "テスト小説")
    return settings, work


def test_add_chapter_success(test_work: tuple[Settings, WorkInfo]) -> None:
    settings, work = test_work
    repo = work.path

    commit1 = add_chapter(settings, work, "ch-001", "第一章 旅立ち", "カイが旅立つ。")
    assert isinstance(commit1, str)
    assert len(commit1) == 40

    # outline.md の確認
    outline_path = repo / "chapters" / "ch-001" / "outline.md"
    assert outline_path.is_file()
    assert outline_path.read_text(encoding="utf-8") == "カイが旅立つ。\n"

    # chapter.yaml の確認
    meta = read_chapter_meta(repo, "ch-001")
    assert meta is not None
    assert meta["id"] == "ch-001"
    assert meta["title"] == "第一章 旅立ち"
    assert meta["state"] == "OUTLINED"
    assert meta["review_required"] is False
    assert meta["review_reasons"] == []
    assert meta["validation_skipped"] is False
    assert meta["last_job"] is None

    # chapters-order.yaml の確認
    order = read_chapters_order(repo)
    assert order == ["ch-001"]

    # git commit & trailer の確認
    log_msg = run_git(repo, "log", "-1", "--format=%B").decode("utf-8")
    assert "add chapter ch-001" in log_msg
    assert "NovUI-Edit: human-content" in log_msg

    # 2つ目の章を追加
    commit2 = add_chapter(settings, work, "ch-002", "第二章 遭遇", "魔物と遭遇する。\n")
    assert commit2 != commit1
    assert read_chapters_order(repo) == ["ch-001", "ch-002"]


def test_add_chapter_validation_errors(test_work: tuple[Settings, WorkInfo]) -> None:
    settings, work = test_work
    repo = work.path

    # 不正な chapter_id
    with pytest.raises(ValueError, match="Invalid chapter_id"):
        add_chapter(settings, work, "chapter-1", "Title", "Outline")
    with pytest.raises(ValueError, match="Invalid chapter_id"):
        add_chapter(settings, work, "ch-1", "Title", "Outline")
    with pytest.raises(ValueError, match="Invalid chapter_id"):
        add_chapter(settings, work, "ch-01", "Title", "Outline")
    with pytest.raises(ValueError, match="Invalid chapter_id"):
        add_chapter(settings, work, "ch-abc", "Title", "Outline")

    # 空の title
    with pytest.raises(ValueError, match="title cannot be empty"):
        add_chapter(settings, work, "ch-001", "", "Outline")
    with pytest.raises(ValueError, match="title cannot be empty"):
        add_chapter(settings, work, "ch-001", "   \n", "Outline")

    # 空の outline_text
    with pytest.raises(ValueError, match="outline_text cannot be empty"):
        add_chapter(settings, work, "ch-001", "Title", "")
    with pytest.raises(ValueError, match="outline_text cannot be empty"):
        add_chapter(settings, work, "ch-001", "Title", "  \n\t")

    # 正常に ch-001 を追加
    add_chapter(settings, work, "ch-001", "Title 1", "Outline 1")

    # 重複する chapter_id
    with pytest.raises(ChapterError, match="already present in chapters-order.yaml"):
        add_chapter(settings, work, "ch-001", "Title 1 duplicate", "Outline")

    # chapters-order になくてもディレクトリが既に存在する場合
    (repo / "chapters" / "ch-002").mkdir(parents=True)
    with pytest.raises(ChapterError, match="Chapter directory already exists"):
        add_chapter(settings, work, "ch-002", "Title 2", "Outline")


def test_add_chapter_dirty_or_not_main(test_work: tuple[Settings, WorkInfo]) -> None:
    settings, work = test_work
    repo = work.path

    # 未コミットの変更がある場合
    (repo / "dirty.txt").write_text("dirty")
    with pytest.raises(ChapterError, match="uncommitted changes"):
        add_chapter(settings, work, "ch-001", "Title", "Outline")
    (repo / "dirty.txt").unlink()

    # 別ブランチにいる場合
    run_git(repo, "switch", "-c", "other-branch")
    with pytest.raises(ChapterError, match="must be on 'main' branch"):
        add_chapter(settings, work, "ch-001", "Title", "Outline")
    run_git(repo, "switch", "main")


def test_transition_on_main_success(test_work: tuple[Settings, WorkInfo]) -> None:
    settings, work = test_work
    repo = work.path

    add_chapter(settings, work, "ch-001", "第一章", "アウトライン")

    plan_yaml_bytes = b"format_version: 1\nscenes: []\n"
    c_hash = transition_on_main(
        settings,
        work,
        "ch-001",
        ChapterEvent.PLAN_WRITTEN,
        subject="plan ch-001 by job-1",
        trailers=[("NovUI-Job", "job-1")],
        extra_files={"chapters/ch-001/plan.yaml": plan_yaml_bytes},
        last_job="job-1",
    )
    assert isinstance(c_hash, str)
    assert len(c_hash) == 40

    # 反映確認
    meta = read_chapter_meta(repo, "ch-001")
    assert meta is not None
    assert meta["state"] == "PLANNED"
    assert meta["last_job"] == "job-1"

    # extra_files の確認
    plan_path = repo / "chapters" / "ch-001" / "plan.yaml"
    assert plan_path.is_file()
    assert plan_path.read_bytes() == plan_yaml_bytes

    # commit log の確認
    log_msg = run_git(repo, "log", "-1", "--format=%B").decode("utf-8")
    assert "plan ch-001 by job-1" in log_msg
    assert "NovUI-Job: job-1" in log_msg


def test_transition_on_main_errors(test_work: tuple[Settings, WorkInfo]) -> None:
    settings, work = test_work
    repo = work.path

    add_chapter(settings, work, "ch-001", "第一章", "アウトライン")

    # 存在しない章
    with pytest.raises(ChapterError, match="not found"):
        transition_on_main(settings, work, "ch-999", ChapterEvent.PLAN_WRITTEN, subject="test")

    # 不正な遷移 (OUTLINED から FINAL_APPROVED は不可)
    with pytest.raises(InvalidTransition):
        transition_on_main(settings, work, "ch-001", ChapterEvent.FINAL_APPROVED, subject="test")

    # 作業ブランチが存在する場合 (can_accept_write_job が False)
    jw = create_job_worktree(repo, settings.worktree_root, work.work_key, "ch-001", "job-1")
    with pytest.raises(ChapterError, match="active worktree/branch"):
        transition_on_main(settings, work, "ch-001", ChapterEvent.PLAN_WRITTEN, subject="test")

    # クリーンアップ
    remove_job_worktree(repo, jw, delete_branch=True)

    # 不正な extra_files パス
    with pytest.raises(ValueError, match="Invalid extra file relative path"):
        transition_on_main(
            settings,
            work,
            "ch-001",
            ChapterEvent.PLAN_WRITTEN,
            subject="test",
            extra_files={"../outside.txt": b"evil"},
        )


def test_read_chapters_order_missing(tmp_path: Path) -> None:
    with pytest.raises(ChapterError, match="chapters-order.yaml not found"):
        read_chapters_order(tmp_path)


def test_read_chapter_meta_schema_error(test_work: tuple[Settings, WorkInfo]) -> None:
    settings, work = test_work
    repo = work.path

    add_chapter(settings, work, "ch-001", "第一章", "アウトライン")
    # 不正な内容に上書き
    ch_yaml = repo / "chapters" / "ch-001" / "chapter.yaml"
    ch_yaml.write_text("invalid: [not, a, valid, chapter]\n", encoding="utf-8")

    with pytest.raises(SchemaError):
        read_chapter_meta(repo, "ch-001")
