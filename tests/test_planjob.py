"""Tests for novui.planjob module."""

import copy
import json
from pathlib import Path
from typing import Any
import pytest

from novui.chapters import ChapterError, add_chapter, read_chapter_meta
from novui.claudejob import ClaudeRunner
from novui.config import Settings
from novui.gitinspect import run_git
from novui.planjob import (
    approve_plan,
    collect_plan_context,
    reject_plan,
    run_plan_job,
)
from novui.procrun import ProcResult
from novui.workinit import init_work
from novui.works import WorkInfo
from novui.workrepo import commit_all, head_commit
from novui.yamlio import dumps_yaml, load_yaml

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "valid"


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
        timeouts={"agy_draft": 300, "claude": 300},
        git_name="Tester",
        git_email="tester@novui.local",
    )


def make_envelope(structured_output: Any, *, model: str = "claude-opus-5-5") -> bytes:
    envelope = {
        "type": "result",
        "is_error": False,
        "result": json.dumps(structured_output, ensure_ascii=False),
        "structured_output": structured_output,
        "modelUsage": {model: {}},
        "total_cost_usd": 0.01,
        "permission_denials": [],
    }
    return json.dumps(envelope, ensure_ascii=False).encode("utf-8")


@pytest.fixture
def novel_setup(tmp_path: Path) -> tuple[Settings, WorkInfo]:
    settings = _make_settings(tmp_path)
    repo = tmp_path / "novel_repo"
    work = init_work(settings, repo, "test-novel", "テスト小説")

    # キャラクター C001, C002
    c1 = {"id": "C001", "name": "カイ", "speech": {"first_person": "俺"}}
    (repo / "characters" / "C001.yaml").write_text(dumps_yaml(c1), encoding="utf-8")

    # 伏線 F001
    f1 = [
        {
            "id": "F001",
            "name": "王家の紋章",
            "status": "planned",
            "importance": "major",
            "introduced": None,
            "hints": [],
            "developments": [],
            "planned_resolution": None,
            "resolved": None,
            "notes": "",
        }
    ]
    (repo / "foreshadowing" / "registry.yaml").write_text(dumps_yaml(f1), encoding="utf-8")

    # world ファイル
    (repo / "world" / "b_setting.md").write_text("# B\n", encoding="utf-8")
    (repo / "world" / "a_setting.md").write_text("# A\n", encoding="utf-8")

    commit_all(repo, "setup characters and world", [("NovUI-Edit", "human-content")], name="Tester", email="tester@test")
    return settings, work


def test_collect_plan_context_order(novel_setup: tuple[Settings, WorkInfo]) -> None:
    settings, work = novel_setup
    repo = work.path

    add_chapter(settings, work, "ch-001", "第一章", "アウトライン1")
    add_chapter(settings, work, "ch-002", "第二章", "アウトライン2")

    # ch-001 の summary.yaml を配置
    sum_data = {
        "format_version": 1,
        "chapter_id": "ch-001",
        "title": "第一章",
        "timeline": {"chapter_index": 1, "in_story_range": "1日目", "notes": ""},
        "characters": [],
        "foreshadowing_events": [],
        "synopsis": "要約本文",
        "unresolved_questions": [],
    }
    (repo / "chapters" / "ch-001" / "summary.yaml").write_text(dumps_yaml(sum_data), encoding="utf-8")
    commit_all(repo, "add ch-001 summary", [("NovUI-Edit", "human-content")], name="Tester", email="tester@test")

    ctx = collect_plan_context(repo, "ch-002")
    expected = [
        "project.yaml",
        "rules/style.md",
        "rules/prohibited.yaml",
        "chapters-order.yaml",
        "world/README.md",
        "world/a_setting.md",
        "world/b_setting.md",
        "characters/C001.yaml",
        "foreshadowing/registry.yaml",
        "plot/timeline.yaml",
        "chapters/ch-001/summary.yaml",
        "chapters/ch-002/outline.md",
    ]
    assert ctx == expected


def _make_valid_plan(chapter_id: str = "ch-001") -> dict[str, Any]:
    valid_plan = load_yaml(FIXTURES_DIR / "plan.yaml")
    plan = copy.deepcopy(valid_plan)
    plan["chapter_id"] = chapter_id
    plan["scenes"][0]["characters"] = ["C001"]
    plan["scenes"][0]["foreshadowing"] = ["F001"]
    plan["context"]["past_summaries"] = []
    plan["context"]["past_drafts"] = []
    plan["context"]["settings"] = ["project.yaml"]
    return plan


def test_run_plan_job_success(novel_setup: tuple[Settings, WorkInfo]) -> None:
    settings, work = novel_setup
    repo = work.path
    add_chapter(settings, work, "ch-001", "第一章", "アウトライン本文")

    plan_data = _make_valid_plan("ch-001")

    def fake_runner(*args: Any, **kwargs: Any) -> ProcResult:
        return ProcResult(0, 1.0, False, None, False, make_envelope(plan_data), b"")

    record = run_plan_job(settings, work, "ch-001", claude_runner=fake_runner)

    # 記録の検証
    assert record["state"] == "COMPLETED"
    check_names = {c["name"]: c["status"] for c in record["checks"]}
    assert check_names.get("claude_output") == "PASS"
    assert check_names.get("plan_refs") == "PASS"

    # main の反映確認
    meta = read_chapter_meta(repo, "ch-001")
    assert meta is not None
    assert meta["state"] == "PLANNED"
    assert meta["last_job"] == "job-1"

    plan_path = repo / "chapters" / "ch-001" / "plan.yaml"
    assert plan_path.is_file()
    saved_plan = load_yaml(plan_path)
    assert saved_plan["chapter_id"] == "ch-001"

    # commit trailer の確認
    log_msg = run_git(repo, "log", "-1", "--format=%B").decode("utf-8")
    assert "plan ch-001 by job-1" in log_msg
    assert "NovUI-Job: job-1" in log_msg


def test_run_plan_job_refs_failures(novel_setup: tuple[Settings, WorkInfo]) -> None:
    settings, work = novel_setup
    repo = work.path
    add_chapter(settings, work, "ch-001", "第一章", "アウトライン本文")

    base_plan = _make_valid_plan("ch-001")

    # 1. 存在しない C999
    bad_plan_1 = copy.deepcopy(base_plan)
    bad_plan_1["scenes"][0]["characters"] = ["C999"]

    # 2. 存在しない F999
    bad_plan_2 = copy.deepcopy(base_plan)
    bad_plan_2["scenes"][0]["foreshadowing"] = ["F999"]

    # 3. S1 からの連番でない
    bad_plan_3 = copy.deepcopy(base_plan)
    bad_plan_3["scenes"][0]["id"] = "S2"

    # 4. context.settings に資料にないパス
    bad_plan_4 = copy.deepcopy(base_plan)
    bad_plan_4["context"]["settings"] = ["world/nonexistent.md"]

    # 5. 未来の章を past_summaries に指定
    bad_plan_5 = copy.deepcopy(base_plan)
    bad_plan_5["context"]["past_summaries"] = ["ch-002"]

    cases = [
        ("C999", bad_plan_1, "character file not found"),
        ("F999", bad_plan_2, "foreshadowing ID 'F999' not found"),
        ("S2", bad_plan_3, "scene id mismatch"),
        ("settings", bad_plan_4, "was not provided in context_paths"),
        ("past_summaries", bad_plan_5, "not prior to 'ch-001'"),
    ]

    head_before = head_commit(repo, "main")

    for idx, (label, bad_data, expected_err) in enumerate(cases, start=1):
        def fake_runner(*args: Any, **kwargs: Any) -> ProcResult:
            return ProcResult(0, 1.0, False, None, False, make_envelope(bad_data), b"")

        job_id = f"job-{idx}"
        record = run_plan_job(settings, work, "ch-001", job_id=job_id, claude_runner=fake_runner)

        assert record["state"] == "FAILED", f"Expected FAILED for {label}"
        plan_chk = [c for c in record["checks"] if c["name"] == "plan_refs"][0]
        assert plan_chk["status"] == "FAIL"
        assert any(expected_err in d for d in plan_chk["details"])

        # main が変わっていないこと
        assert head_commit(repo, "main") == head_before


def test_run_plan_job_chapter_state_error(novel_setup: tuple[Settings, WorkInfo]) -> None:
    settings, work = novel_setup
    repo = work.path
    add_chapter(settings, work, "ch-001", "第一章", "アウトライン本文")

    # 成功させて PLANNED にする
    plan_data = _make_valid_plan("ch-001")
    run_plan_job(
        settings,
        work,
        "ch-001",
        claude_runner=lambda *args, **kwargs: ProcResult(0, 1.0, False, None, False, make_envelope(plan_data), b""),
    )

    # PLANNED の状態で再度 run_plan_job を呼ぶと ChapterError
    with pytest.raises(ChapterError, match="must be in OUTLINED state"):
        run_plan_job(settings, work, "ch-001")


def test_approve_and_reject_plan(novel_setup: tuple[Settings, WorkInfo]) -> None:
    settings, work = novel_setup
    repo = work.path
    add_chapter(settings, work, "ch-001", "第一章", "アウトライン本文")

    plan_data = _make_valid_plan("ch-001")
    run_plan_job(
        settings,
        work,
        "ch-001",
        job_id="job-1",
        claude_runner=lambda *args, **kwargs: ProcResult(0, 1.0, False, None, False, make_envelope(plan_data), b""),
    )

    # 1. approve_plan
    c_hash = approve_plan(settings, work, "ch-001")
    assert isinstance(c_hash, str) and len(c_hash) == 40
    meta = read_chapter_meta(repo, "ch-001")
    assert meta["state"] == "PLAN_APPROVED"

    log_msg = run_git(repo, "log", "-1", "--format=%B").decode("utf-8")
    assert "approve plan ch-001" in log_msg

    # 2. reject_plan のテストのために ch-002 を作成
    add_chapter(settings, work, "ch-002", "第二章", "アウトライン本文2")
    plan_data_2 = _make_valid_plan("ch-002")
    run_plan_job(
        settings,
        work,
        "ch-002",
        job_id="job-2",
        claude_runner=lambda *args, **kwargs: ProcResult(0, 1.0, False, None, False, make_envelope(plan_data_2), b""),
    )

    c_hash2 = reject_plan(settings, work, "ch-002")
    assert isinstance(c_hash2, str) and len(c_hash2) == 40
    meta2 = read_chapter_meta(repo, "ch-002")
    # PLANNED から PLAN_REJECTED イベントにより OUTLINED に戻る
    assert meta2["state"] == "OUTLINED"

    log_msg2 = run_git(repo, "log", "-1", "--format=%B").decode("utf-8")
    assert "reject plan ch-002" in log_msg2
