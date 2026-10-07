"""Claude Job runner module for NovUI."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import shutil
import subprocess
from typing import Any, Callable

from novui.claude_cli import run_claude
from novui.claude_output import (
    AttemptRecord,
    OutputRetryExhausted,
    parse_claude_meta,
    run_with_output_retry,
)
from novui.config import Settings
from novui.jobrecord import apply_transition, new_job_record, save_job_record
from novui.jobrunner import jobs_dir
from novui.procrun import ProcResult
from novui.prompt import build_claude_prompt
from novui.schema import bundle_schema
from novui.states import JobEvent, JobState

ClaudeRunner = Callable[..., ProcResult]


class ClaudeTimedOut(Exception):
    """Raised when Claude CLI execution times out."""


@dataclass(frozen=True)
class ClaudeJobRequest:
    work_key: str
    job_id: str
    job_type: str
    chapter_id: str | None
    root: Path
    context_paths: tuple[str, ...]
    task_text: str
    expected_type: str
    model: str


_claude_version_cached: str | None = None


def claude_version() -> str:
    """Return version string of Claude CLI (first line of claude --version). Cached."""
    global _claude_version_cached
    if _claude_version_cached is not None:
        return _claude_version_cached

    try:
        res = subprocess.run(["claude", "--version"], capture_output=True, text=True, check=False)
        if res.returncode == 0 and res.stdout.strip():
            _claude_version_cached = res.stdout.strip().splitlines()[0].strip()
        else:
            _claude_version_cached = "unknown"
    except Exception:
        _claude_version_cached = "unknown"

    return _claude_version_cached


def run_claude_job(
    settings: Settings,
    req: ClaudeJobRequest,
    *,
    claude_runner: ClaudeRunner = run_claude,
    max_retries: int = 2,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Execute a Claude Job lifecycle and return (job_record, output_data)."""
    jdir = jobs_dir(settings, req.work_key)
    jdir.mkdir(parents=True, exist_ok=True)

    # 1. new_job_record in QUEUED state
    record = new_job_record(req.job_id, req.job_type, chapter_id=req.chapter_id)
    save_job_record(jdir, record)

    # 2. build_claude_prompt. ValueError raises while leaving record QUEUED.
    try:
        built = build_claude_prompt(req.root, req.context_paths, req.task_text)
    except ValueError:
        raise

    record["context"] = [{"path": e.path, "sha256": e.sha256} for e in built.context]
    save_job_record(jdir, record)

    # 3. LOCK_ACQUIRED -> RUNNING
    record = apply_transition(record, JobEvent.LOCK_ACQUIRED)
    save_job_record(jdir, record)

    # 4. JSON Schema bundling
    schema_dict = bundle_schema(req.expected_type)
    cli_schema = {k: v for k, v in schema_dict.items() if k != "$schema"}
    schema_json = json.dumps(cli_schema, ensure_ascii=False, separators=(",", ":"))

    # 5. Create isolated cwd
    cwd = settings.data_dir / "claude-cwd" / req.job_id
    if cwd.exists():
        raise FileExistsError(f"Claude cwd already exists: {cwd}")
    cwd.mkdir(parents=True, exist_ok=False)

    log_dir = jdir / f"{req.job_id}-logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    last_result: ProcResult | None = None
    last_stdout: bytes = b""
    attempts_count = 0

    def call(attempt: int) -> bytes:
        nonlocal last_result, last_stdout, attempts_count
        attempts_count = attempt
        res = claude_runner(
            prompt=built.text,
            cwd=cwd,
            model=req.model,
            timeout_seconds=settings.timeouts["claude"],
            log_dir=log_dir,
            name=f"{req.job_id}-a{attempt}",
            json_schema=schema_json,
        )
        last_result = res
        last_stdout = res.stdout
        if res.timed_out:
            raise ClaudeTimedOut(f"Claude timed out after {settings.timeouts['claude']}s")
        return res.stdout

    def _update_record_run_and_cli(attempt_num: int) -> None:
        nonlocal record
        meta = parse_claude_meta(last_stdout) if last_stdout else None
        actual_model = meta.model_usage_keys[0] if (meta and meta.model_usage_keys) else None
        record["cli"] = {
            "name": "claude",
            "version": claude_version(),
            "model": req.model,
            "actual_model": actual_model,
        }
        record["container"] = None
        if last_result is not None:
            record["run"] = {
                "exit_code": last_result.exit_code,
                "elapsed_seconds": last_result.elapsed_seconds,
                "timed_out": last_result.timed_out,
                "signal": last_result.signal_sent,
                "timeout_seconds": int(settings.timeouts["claude"]),
            }
        record["attempt"] = attempt_num

    try:
        try:
            data, attempts = run_with_output_retry(call, req.expected_type, max_retries=max_retries)
            _update_record_run_and_cli(len(attempts))
            record = apply_transition(record, JobEvent.CLI_EXITED)
            record["checks"].append({
                "name": "claude_output",
                "status": "PASS",
                "details": [],
            })
            save_job_record(jdir, record)
            return record, data
        except ClaudeTimedOut:
            _update_record_run_and_cli(attempts_count if attempts_count > 0 else 1)
            record = apply_transition(record, JobEvent.TIMED_OUT, reason="claude timed out")
            save_job_record(jdir, record)
            return record, None
        except OutputRetryExhausted as exc:
            _update_record_run_and_cli(len(exc.attempts))
            record = apply_transition(record, JobEvent.CLI_EXITED)
            details = [
                f"attempt {a.attempt}: {a.error_kind}: {a.errors[0]}" if a.errors else f"attempt {a.attempt}: {a.error_kind}"
                for a in exc.attempts
            ]
            record["checks"].append({
                "name": "claude_output",
                "status": "FAIL",
                "details": details,
            })
            record = apply_transition(record, JobEvent.CHECK_FAILED, reason="claude output retry exhausted")
            save_job_record(jdir, record)
            return record, None
        except Exception as exc:
            _update_record_run_and_cli(attempts_count if attempts_count > 0 else 1)
            if record.get("state") == JobState.RUNNING.name:
                record = apply_transition(record, JobEvent.CLI_EXITED)
                record["checks"].append({
                    "name": "claude_output",
                    "status": "FAIL",
                    "details": [str(exc)],
                })
                record = apply_transition(record, JobEvent.CHECK_FAILED, reason=str(exc))
                save_job_record(jdir, record)
            raise
    finally:
        shutil.rmtree(cwd, ignore_errors=True)


@dataclass(frozen=True)
class ClaudeCallOutcome:
    """Result of run_claude_call. data is None unless a valid output was obtained."""

    data: dict[str, Any] | None
    attempts: tuple[AttemptRecord, ...]
    is_error_flags: tuple[bool, ...]  # 試行ごとに、封筒の is_error が true だったか
    last_result: ProcResult | None
    actual_model: str | None
    timed_out: bool

    @property
    def unavailable(self) -> bool:
        """True if no valid output was obtained and every attempt returned is_error: true."""
        return (
            self.data is None
            and not self.timed_out
            and len(self.is_error_flags) > 0
            and all(self.is_error_flags)
        )


def run_claude_call(
    settings: Settings,
    *,
    job_id: str,
    prompt_text: str,
    expected_type: str,
    model: str,
    log_dir: Path,
    log_name: str,
    claude_runner: ClaudeRunner = run_claude,
    max_retries: int = 2,
) -> ClaudeCallOutcome:
    """Call Claude for one output type with the same launch as run_claude_job, without a Job record.

    The caller manages the Job record and state transitions.
    """
    schema_dict = bundle_schema(expected_type)
    cli_schema = {k: v for k, v in schema_dict.items() if k != "$schema"}
    schema_json = json.dumps(cli_schema, ensure_ascii=False, separators=(",", ":"))

    cwd = settings.data_dir / "claude-cwd" / job_id
    if cwd.exists():
        raise FileExistsError(f"Claude cwd already exists: {cwd}")
    cwd.mkdir(parents=True, exist_ok=False)
    log_dir.mkdir(parents=True, exist_ok=True)

    last_result: ProcResult | None = None
    is_error_flags: list[bool] = []

    def call(attempt: int) -> bytes:
        nonlocal last_result
        res = claude_runner(
            prompt=prompt_text,
            cwd=cwd,
            model=model,
            timeout_seconds=settings.timeouts["claude"],
            log_dir=log_dir,
            name=f"{log_name}-a{attempt}",
            json_schema=schema_json,
        )
        last_result = res
        if res.timed_out:
            raise ClaudeTimedOut(f"Claude timed out after {settings.timeouts['claude']}s")
        meta = parse_claude_meta(res.stdout) if res.stdout else None
        is_error_flags.append(bool(meta is not None and meta.is_error is True))
        return res.stdout

    def actual_model() -> str | None:
        if last_result is None or not last_result.stdout:
            return None
        meta = parse_claude_meta(last_result.stdout)
        return meta.model_usage_keys[0] if (meta and meta.model_usage_keys) else None

    try:
        try:
            data, attempts = run_with_output_retry(call, expected_type, max_retries=max_retries)
            return ClaudeCallOutcome(data, tuple(attempts), tuple(is_error_flags), last_result, actual_model(), False)
        except OutputRetryExhausted as exc:
            return ClaudeCallOutcome(None, tuple(exc.attempts), tuple(is_error_flags), last_result, actual_model(), False)
        except ClaudeTimedOut:
            return ClaudeCallOutcome(None, (), tuple(is_error_flags), last_result, actual_model(), True)
    finally:
        shutil.rmtree(cwd, ignore_errors=True)
