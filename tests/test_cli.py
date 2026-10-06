"""Tests for novui.cli module."""

import copy
import json
from pathlib import Path
from typing import Any
import pytest

from novui.cli import main
from novui.config import Settings
from novui.models import save_catalog
from novui.procrun import ProcResult
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


def _make_valid_plan(chapter_id: str = "ch-001") -> dict[str, Any]:
    valid_plan = load_yaml(FIXTURES_DIR / "plan.yaml")
    plan = copy.deepcopy(valid_plan)
    plan["chapter_id"] = chapter_id
    plan["scenes"][0]["characters"] = []
    plan["scenes"][0]["foreshadowing"] = []
    plan["context"]["past_summaries"] = []
    plan["context"]["past_drafts"] = []
    plan["context"]["settings"] = ["project.yaml"]
    return plan


def test_cli_lifecycle(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    settings = _make_settings(tmp_path)
    repo_path = tmp_path / "novel_repo"

    # 1. init-work
    rc = main(
        ["init-work", str(repo_path), "--key", "my-novel", "--title", "港の物語"],
        settings=settings,
    )
    assert rc == 0
    captured = capsys.readouterr()
    assert "Work initialized: my-novel" in captured.out

    # 2. add-chapter
    outline_path = tmp_path / "outline_ch1.md"
    outline_path.write_text("# Chapter 1\nカイが門を通る。\n", encoding="utf-8")

    rc = main(
        [
            "add-chapter",
            "--work",
            "my-novel",
            "--id",
            "ch-001",
            "--title",
            "第一章",
            "--outline-file",
            str(outline_path),
        ],
        settings=settings,
    )
    assert rc == 0
    captured = capsys.readouterr()
    assert "Chapter ch-001 added" in captured.out

    # 3. show (chapter list)
    rc = main(["show", "--work", "my-novel"], settings=settings)
    assert rc == 0
    captured = capsys.readouterr()
    assert "ch-001: 第一章 [OUTLINED]" in captured.out

    # 4. plan (success)
    plan_data = _make_valid_plan("ch-001")

    def fake_runner(*args: Any, **kwargs: Any) -> ProcResult:
        return ProcResult(0, 1.0, False, None, False, make_envelope(plan_data), b"")

    rc = main(
        ["plan", "--work", "my-novel", "--chapter", "ch-001"],
        settings=settings,
        claude_runner=fake_runner,
    )
    assert rc == 0
    captured = capsys.readouterr()
    assert "finished with state: COMPLETED" in captured.out
    assert "Check claude_output: PASS" in captured.out
    assert "Check plan_refs: PASS" in captured.out

    # 5. show (chapter detail + job records)
    rc = main(["show", "--work", "my-novel", "--chapter", "ch-001"], settings=settings)
    assert rc == 0
    captured = capsys.readouterr()
    assert "state: PLANNED" in captured.out
    assert "Job: job-1" in captured.out
    assert "State: COMPLETED" in captured.out

    # 6. approve-plan
    rc = main(["approve-plan", "--work", "my-novel", "--chapter", "ch-001"], settings=settings)
    assert rc == 0
    captured = capsys.readouterr()
    assert "Plan for chapter ch-001 approved" in captured.out

    # 7. add-chapter ch-002 and reject-plan
    outline_path2 = tmp_path / "outline_ch2.md"
    outline_path2.write_text("# Chapter 2\n次の話。\n", encoding="utf-8")
    main(
        [
            "add-chapter",
            "--work",
            "my-novel",
            "--id",
            "ch-002",
            "--title",
            "第二章",
            "--outline-file",
            str(outline_path2),
        ],
        settings=settings,
    )
    plan_data2 = _make_valid_plan("ch-002")
    main(
        ["plan", "--work", "my-novel", "--chapter", "ch-002"],
        settings=settings,
        claude_runner=lambda *args, **kwargs: ProcResult(0, 1.0, False, None, False, make_envelope(plan_data2), b""),
    )

    rc = main(["reject-plan", "--work", "my-novel", "--chapter", "ch-002"], settings=settings)
    assert rc == 0
    captured = capsys.readouterr()
    assert "Plan for chapter ch-002 rejected" in captured.out


def test_cli_plan_failed(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    settings = _make_settings(tmp_path)
    repo_path = tmp_path / "novel_repo"

    main(["init-work", str(repo_path), "--key", "w-fail", "--title", "失敗テスト"], settings=settings)

    outline_path = tmp_path / "outline.md"
    outline_path.write_text("Outline\n", encoding="utf-8")
    main(
        ["add-chapter", "--work", "w-fail", "--id", "ch-001", "--title", "一章", "--outline-file", str(outline_path)],
        settings=settings,
    )

    # 壊れた plan 出力を返す
    bad_plan = _make_valid_plan("ch-001")
    bad_plan["scenes"][0]["id"] = "S99"  # S1 からの連番違反

    def fake_runner(*args: Any, **kwargs: Any) -> ProcResult:
        return ProcResult(0, 1.0, False, None, False, make_envelope(bad_plan), b"")

    rc = main(
        ["plan", "--work", "w-fail", "--chapter", "ch-001"],
        settings=settings,
        claude_runner=fake_runner,
    )
    # FAILED で終わった場合は終了コード 2
    assert rc == 2
    captured = capsys.readouterr()
    assert "finished with state: FAILED" in captured.out
    assert "Check plan_refs: FAIL" in captured.out
    assert "scene id mismatch" in captured.out


def test_cli_models_select(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    settings = _make_settings(tmp_path)
    catalog = {
        "fetched_at": "2026-10-03T00:00:00+09:00",
        "agy_image": "test:img",
        "image_id": "img123",
        "agy_version": "1.0",
        "models": [{"id": "gemini-3.8-flash-high", "label": "Flash High"}],
    }
    save_catalog(settings, catalog)

    rc = main(
        ["models", "select", "--role", "draft", "--model", "gemini-3.8-flash-high"],
        settings=settings,
    )
    assert rc == 0
    captured = capsys.readouterr()
    assert "Model for role 'draft' set to 'gemini-3.8-flash-high'" in captured.out


def test_cli_errors(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    settings = _make_settings(tmp_path)

    # 1. 引数不正 (引数なし) -> 終了コード 1
    rc = main([], settings=settings)
    assert rc == 1

    # 2. 存在しない作品キーでの操作 -> 終了コード 1, stderr に出力
    rc = main(["show", "--work", "nonexistent-work"], settings=settings)
    assert rc == 1
    captured = capsys.readouterr()
    assert "Error:" in captured.err


def _fake_container(stdout: str) -> Any:
    def runner(**kwargs: Any) -> ProcResult:
        return ProcResult(0, 1.0, False, None, False, stdout.encode("utf-8"), b"")
    return runner


def test_cli_draft(tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("novui.jobrunner.image_label", lambda img, key: "1.2.14")
    monkeypatch.setattr(
        "novui.jobrunner.image_id",
        lambda img: "sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
    )
    settings = _make_settings(tmp_path)
    save_catalog(settings, load_yaml(FIXTURES_DIR / "model_catalog.yaml"))
    assert main(["models", "select", "--role", "draft", "--model", "gemini-3.8-flash-high"], settings=settings) == 0

    assert main(["init-work", str(tmp_path / "novel_repo"), "--key", "my-novel", "--title", "港の物語"], settings=settings) == 0
    for ch in ("ch-001", "ch-002"):
        outline = tmp_path / f"{ch}.md"
        outline.write_text("カイが門を通る。\n", encoding="utf-8")
        assert main(["add-chapter", "--work", "my-novel", "--id", ch, "--title", ch,
                     "--outline-file", str(outline)], settings=settings) == 0
        plan_data = _make_valid_plan(ch)
        assert main(["plan", "--work", "my-novel", "--chapter", ch], settings=settings,
                    claude_runner=lambda *a, _p=plan_data, **k: ProcResult(0, 1.0, False, None, False, make_envelope(_p), b"")) == 0
        assert main(["approve-plan", "--work", "my-novel", "--chapter", ch], settings=settings) == 0
    capsys.readouterr()

    # 成功：COMPLETED、終了コード 0。機械検査の結果を表示する
    rc = main(["draft", "--work", "my-novel", "--chapter", "ch-001"], settings=settings,
              container_runner=_fake_container("<!-- scene: S1 -->\nカイは門を通った。\n"))
    assert rc == 0
    out = capsys.readouterr().out
    assert "finished with state: COMPLETED" in out
    assert "Check scene_markers: PASS" in out
    assert "Check char_count: WARNING" in out

    # 【要確認】：WAITING_HUMAN、終了コード 3、理由を表示する
    rc = main(["draft", "--work", "my-novel", "--chapter", "ch-002"], settings=settings,
              container_runner=_fake_container("<!-- scene: S1 -->\n門番は【要確認：門番の名前】。\n"))
    assert rc == 3
    out = capsys.readouterr().out
    assert "finished with state: WAITING_HUMAN" in out
    assert "Reason: undefined_settings: 1" in out

    # 作業ブランチがある章：エラー、終了コード 1
    rc = main(["draft", "--work", "my-novel", "--chapter", "ch-001"], settings=settings,
              container_runner=_fake_container("x\n"))
    assert rc == 1
    assert "active worktree" in capsys.readouterr().err

    # 空出力：FAILED、終了コード 2
    outline = tmp_path / "ch-003.md"
    outline.write_text("次の話。\n", encoding="utf-8")
    main(["add-chapter", "--work", "my-novel", "--id", "ch-003", "--title", "ch-003",
          "--outline-file", str(outline)], settings=settings)
    plan3 = _make_valid_plan("ch-003")
    main(["plan", "--work", "my-novel", "--chapter", "ch-003"], settings=settings,
         claude_runner=lambda *a, **k: ProcResult(0, 1.0, False, None, False, make_envelope(plan3), b""))
    main(["approve-plan", "--work", "my-novel", "--chapter", "ch-003"], settings=settings)
    capsys.readouterr()
    rc = main(["draft", "--work", "my-novel", "--chapter", "ch-003"], settings=settings,
              container_runner=_fake_container(""))
    assert rc == 2
    assert "Check agy_output: FAIL" in capsys.readouterr().out
