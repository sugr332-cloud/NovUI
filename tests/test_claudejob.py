"""Tests for novui.claudejob module."""

import json
from pathlib import Path
from typing import Any
import pytest

from novui.claude_output import CLAUDE_OUTPUT_TYPES
from novui.claudejob import (
    ClaudeJobRequest,
    ClaudeTimedOut,
    claude_version,
    run_claude_job,
)
from novui.config import Settings
from novui.jobrecord import load_job_record
from novui.jobrunner import jobs_dir
from novui.procrun import ProcResult
from novui.schema import bundle_schema
from novui.yamlio import load_yaml

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


def make_envelope(
    structured_output: Any,
    *,
    is_error: bool = False,
    model: str = "claude-opus-5-5",
    cost: float = 0.01,
) -> bytes:
    envelope = {
        "type": "result",
        "is_error": is_error,
        "result": json.dumps(structured_output, ensure_ascii=False) if structured_output is not None else "",
        "structured_output": structured_output,
        "modelUsage": {model: {}},
        "total_cost_usd": cost,
        "permission_denials": [],
    }
    return json.dumps(envelope, ensure_ascii=False).encode("utf-8")


def test_claude_version() -> None:
    v = claude_version()
    assert isinstance(v, str)
    assert len(v) > 0


def test_run_claude_job_success(tmp_path: Path) -> None:
    settings = _make_settings(tmp_path)
    root = tmp_path / "repo"
    root.mkdir()
    (root / "outline.md").write_text("# Outline\nContent\n", encoding="utf-8")

    valid_plan = load_yaml(FIXTURES_DIR / "plan.yaml")

    received_schema = None
    received_cwd = None

    def fake_runner(*, prompt: str, cwd: Path, model: str, timeout_seconds: float, log_dir: Path, name: str, json_schema: str) -> ProcResult:
        nonlocal received_schema, received_cwd
        received_schema = json_schema
        received_cwd = cwd
        # cwd が空ディレクトリであることを確認
        assert list(cwd.iterdir()) == []
        return ProcResult(
            exit_code=0,
            elapsed_seconds=1.5,
            timed_out=False,
            signal_sent=None,
            group_remaining=False,
            stdout=make_envelope(valid_plan, model="claude-opus-5-5"),
            stderr=b"",
        )

    req = ClaudeJobRequest(
        work_key="test-work",
        job_id="job-1",
        job_type="plan",
        chapter_id="ch-001",
        root=root,
        context_paths=("outline.md",),
        task_text="【作業】章 ch-001 の執筆計画を作ってください。",
        expected_type="plan",
        model="opus",
    )

    record, data = run_claude_job(settings, req, claude_runner=fake_runner)

    # 1. CHECKING のまま返る
    assert record["state"] == "CHECKING"
    assert data == valid_plan

    # 2. json_schema が一致（$schema がなく、それ以外は bundle_schema("plan") と一致）
    assert received_schema is not None
    loaded_schema = json.loads(received_schema)
    assert "$schema" not in loaded_schema
    expected_schema_dict = {k: v for k, v in bundle_schema("plan").items() if k != "$schema"}
    assert loaded_schema == expected_schema_dict

    # 3. actual_model が記録される
    assert record["cli"]["actual_model"] == "claude-opus-5-5"
    assert record["cli"]["model"] == "opus"
    assert record["cli"]["name"] == "claude"

    # 4. checks に PASS が入る
    assert len(record["checks"]) == 1
    assert record["checks"][0]["name"] == "claude_output"
    assert record["checks"][0]["status"] == "PASS"

    # 5. run 情報
    assert record["run"]["exit_code"] == 0
    assert record["run"]["elapsed_seconds"] == 1.5
    assert record["run"]["timed_out"] is False

    # 6. 作業ディレクトリが削除されていること
    assert received_cwd is not None
    assert not received_cwd.exists()


def test_run_claude_job_retry_success(tmp_path: Path) -> None:
    settings = _make_settings(tmp_path)
    root = tmp_path / "repo"
    root.mkdir()
    (root / "outline.md").write_text("# Outline\n", encoding="utf-8")

    valid_plan = load_yaml(FIXTURES_DIR / "plan.yaml")
    call_count = 0

    def fake_runner(*, prompt: str, cwd: Path, model: str, timeout_seconds: float, log_dir: Path, name: str, json_schema: str) -> ProcResult:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            # 1回目は壊れた出力
            return ProcResult(0, 1.0, False, None, False, b"not valid json envelope", b"")
        return ProcResult(0, 1.2, False, None, False, make_envelope(valid_plan), b"")

    req = ClaudeJobRequest(
        work_key="test-work",
        job_id="job-2",
        job_type="plan",
        chapter_id="ch-001",
        root=root,
        context_paths=("outline.md",),
        task_text="【作業】指示",
        expected_type="plan",
        model="opus",
    )

    record, data = run_claude_job(settings, req, claude_runner=fake_runner, max_retries=2)
    assert record["state"] == "CHECKING"
    assert data == valid_plan
    assert record["attempt"] == 2
    assert call_count == 2


def test_run_claude_job_retry_exhausted(tmp_path: Path) -> None:
    settings = _make_settings(tmp_path)
    root = tmp_path / "repo"
    root.mkdir()
    (root / "outline.md").write_text("# Outline\n", encoding="utf-8")

    call_count = 0

    def fake_runner(*, prompt: str, cwd: Path, model: str, timeout_seconds: float, log_dir: Path, name: str, json_schema: str) -> ProcResult:
        nonlocal call_count
        call_count += 1
        return ProcResult(0, 1.0, False, None, False, b"bad output", b"")

    req = ClaudeJobRequest(
        work_key="test-work",
        job_id="job-3",
        job_type="plan",
        chapter_id="ch-001",
        root=root,
        context_paths=("outline.md",),
        task_text="【作業】指示",
        expected_type="plan",
        model="opus",
    )

    record, data = run_claude_job(settings, req, claude_runner=fake_runner, max_retries=2)
    assert record["state"] == "FAILED"
    assert data is None
    assert record["attempt"] == 3
    assert call_count == 3

    assert len(record["checks"]) == 1
    chk = record["checks"][0]
    assert chk["name"] == "claude_output"
    assert chk["status"] == "FAIL"
    assert len(chk["details"]) == 3


def test_run_claude_job_timeout(tmp_path: Path) -> None:
    settings = _make_settings(tmp_path)
    root = tmp_path / "repo"
    root.mkdir()
    (root / "outline.md").write_text("# Outline\n", encoding="utf-8")

    def fake_runner(*, prompt: str, cwd: Path, model: str, timeout_seconds: float, log_dir: Path, name: str, json_schema: str) -> ProcResult:
        return ProcResult(
            exit_code=None,
            elapsed_seconds=300.0,
            timed_out=True,
            signal_sent="SIGKILL",
            group_remaining=False,
            stdout=b"",
            stderr=b"",
        )

    req = ClaudeJobRequest(
        work_key="test-work",
        job_id="job-4",
        job_type="plan",
        chapter_id="ch-001",
        root=root,
        context_paths=("outline.md",),
        task_text="【作業】指示",
        expected_type="plan",
        model="opus",
    )

    record, data = run_claude_job(settings, req, claude_runner=fake_runner)
    assert record["state"] == "FAILED"
    assert data is None
    assert record["run"]["timed_out"] is True
    # 履歴に TIMED_OUT が記録されていること
    events = [h["event"] for h in record["history"]]
    assert "TIMED_OUT" in events


def test_run_claude_job_runtime_error(tmp_path: Path) -> None:
    settings = _make_settings(tmp_path)
    root = tmp_path / "repo"
    root.mkdir()
    (root / "outline.md").write_text("# Outline\n", encoding="utf-8")

    cwd_path = None

    def fake_runner(*, prompt: str, cwd: Path, model: str, timeout_seconds: float, log_dir: Path, name: str, json_schema: str) -> ProcResult:
        nonlocal cwd_path
        cwd_path = cwd
        raise RuntimeError("runner crashed unexpected")

    req = ClaudeJobRequest(
        work_key="test-work",
        job_id="job-5",
        job_type="plan",
        chapter_id="ch-001",
        root=root,
        context_paths=("outline.md",),
        task_text="【作業】指示",
        expected_type="plan",
        model="opus",
    )

    with pytest.raises(RuntimeError, match="runner crashed"):
        run_claude_job(settings, req, claude_runner=fake_runner)

    # 作業ディレクトリが削除されていること
    assert cwd_path is not None
    assert not cwd_path.exists()

    # ディスク上の Job 記録が FAILED になっていること
    saved = load_job_record(jobs_dir(settings, "test-work") / "job-5.yaml")
    assert saved["state"] == "FAILED"


def test_run_claude_job_prompt_error_queued(tmp_path: Path) -> None:
    settings = _make_settings(tmp_path)
    root = tmp_path / "repo"
    root.mkdir()

    req = ClaudeJobRequest(
        work_key="test-work",
        job_id="job-6",
        job_type="plan",
        chapter_id="ch-001",
        root=root,
        context_paths=("nonexistent.md",),
        task_text="【作業】指示",
        expected_type="plan",
        model="opus",
    )

    with pytest.raises(ValueError, match="not a regular file"):
        run_claude_job(settings, req)

    saved = load_job_record(jobs_dir(settings, "test-work") / "job-6.yaml")
    assert saved["state"] == "QUEUED"


def _proc(stdout: bytes, *, timed_out: bool = False) -> ProcResult:
    return ProcResult(
        exit_code=0, elapsed_seconds=1.0, timed_out=timed_out, signal_sent=None,
        group_remaining=False, stdout=stdout, stderr=b"",
    )


def test_run_claude_call_success_and_same_launch(tmp_path: Path) -> None:
    from novui.claudejob import run_claude_call

    settings = _make_settings(tmp_path)
    valid_plan = load_yaml(FIXTURES_DIR / "plan.yaml")
    calls: list[dict[str, Any]] = []

    def runner(**kwargs: Any) -> ProcResult:
        assert list(kwargs["cwd"].iterdir()) == []
        calls.append(kwargs)
        return _proc(make_envelope(valid_plan))

    out = run_claude_call(
        settings, job_id="job-3", prompt_text="P", expected_type="plan", model="opus",
        log_dir=tmp_path / "logs", log_name="job-3-plan", claude_runner=runner,
    )
    assert out.data == valid_plan
    assert out.actual_model == "claude-opus-5-5"
    assert out.unavailable is False
    assert calls[0]["name"] == "job-3-plan-a1"
    assert calls[0]["cwd"] == settings.data_dir / "claude-cwd" / "job-3"
    assert calls[0]["timeout_seconds"] == 300
    expected = {k: v for k, v in bundle_schema("plan").items() if k != "$schema"}
    assert json.loads(calls[0]["json_schema"]) == expected
    assert not calls[0]["cwd"].exists()


def test_run_claude_call_unavailable_and_exhausted(tmp_path: Path) -> None:
    from novui.claudejob import run_claude_call

    settings = _make_settings(tmp_path)
    kw = dict(prompt_text="P", expected_type="plan", model="opus", log_dir=tmp_path / "logs", log_name="x")

    out = run_claude_call(settings, job_id="job-4", claude_runner=lambda **k: _proc(make_envelope(None, is_error=True)), **kw)
    assert out.data is None
    assert len(out.attempts) == 3
    assert out.is_error_flags == (True, True, True)
    assert out.unavailable is True

    out = run_claude_call(settings, job_id="job-5", claude_runner=lambda **k: _proc(make_envelope({"type": "plan"})), **kw)
    assert out.data is None
    assert out.unavailable is False

    out = run_claude_call(settings, job_id="job-6", claude_runner=lambda **k: _proc(b"", timed_out=True), **kw)
    assert out.timed_out is True
    assert out.unavailable is False
    assert not (settings.data_dir / "claude-cwd" / "job-6").exists()
