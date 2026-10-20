"""Impact analysis job for protected changes (Phase 3-C1, phase3c-design §6.1)."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Sequence

from novui.chapters import ChapterError, mark_review_required
from novui.claude_cli import run_claude
from novui.claudejob import ClaudeCallOutcome, ClaudeRunner, claude_version, run_claude_call
from novui.config import Settings
from novui.gitinspect import GitError, run_git
from novui.ids import next_job_id
from novui.impact import analyze_impact
from novui.jobrecord import apply_transition, new_job_record, save_job_record
from novui.jobrunner import jobs_dir
from novui.paths import is_safe_relpath
from novui.prompt import build_claude_prompt
from novui.protected import is_protected
from novui.settingchange import setting_context
from novui.states import JobEvent, JobState
from novui.validatejob import _claude_check
from novui.works import WorkInfo


def run_impact_job(
    settings: Settings,
    work: WorkInfo,
    *,
    base: str,
    head: str,
    reason: str,
    job_id: str | None = None,
    claude_runner: ClaudeRunner = run_claude,
) -> tuple[dict[str, Any], list[dict], list[str]]:
    """Execute impact_analysis Job for protected changes between base and head on main branch."""
    repo = work.path

    # 1. head must be HEAD of main or its ancestor
    try:
        run_git(repo, "merge-base", "--is-ancestor", head, "main")
    except GitError:
        raise ValueError(f"head commit {head} is not an ancestor of main")

    # 2. base must be ancestor of head
    try:
        run_git(repo, "merge-base", "--is-ancestor", base, head)
    except GitError:
        raise ValueError(f"base {base} is not an ancestor of head {head}")

    # 3. Find protected paths changed in base..head (excluding .novui/)
    diff_out = run_git(repo, "diff", "--name-only", "-z", "--no-renames", base, head)
    raw_paths = [p for p in diff_out.decode("utf-8", errors="surrogateescape").split("\0") if p]

    protected_paths: list[str] = []
    for p in raw_paths:
        if not is_safe_relpath(p):
            continue
        if is_protected(p) and not (p == ".novui" or p.startswith(".novui/")):
            protected_paths.append(p)

    if not protected_paths:
        raise ValueError(f"no protected change between {base} and {head}")

    protected_paths.sort()

    # Generate unified diff for protected paths
    diff_text = run_git(repo, "diff", "-u", base, head, "--", *protected_paths).decode("utf-8")

    # 4. Job record creation
    actual_job_id = job_id or next_job_id(settings, work.work_key)
    jdir = jobs_dir(settings, work.work_key)
    jdir.mkdir(parents=True, exist_ok=True)

    record = new_job_record(actual_job_id, "impact_analysis", chapter_id=None)
    record["base_commit"] = base
    record = apply_transition(record, JobEvent.LOCK_ACQUIRED)
    save_job_record(jdir, record)

    def fail(fail_reason: str) -> tuple[dict[str, Any], list[dict], list[str]]:
        nonlocal record
        if record["state"] == JobState.RUNNING.name:
            record = apply_transition(record, JobEvent.CLI_EXITED)
        record = apply_transition(record, JobEvent.CHECK_FAILED, reason=fail_reason)
        save_job_record(jdir, record)
        return record, [], []

    log_dir = jdir / f"{actual_job_id}-logs"
    last_outcome: ClaudeCallOutcome | None = None

    def call(
        expected_type: str,
        task_text: str,
        log_name: str,
        check_name: str,
        *,
        root: Path | None = None,
        ctx_paths: Sequence[str] | None = None,
    ) -> ClaudeCallOutcome:
        nonlocal record, last_outcome
        r = root if root is not None else repo
        cp = ctx_paths if ctx_paths is not None else setting_context(repo)
        built = build_claude_prompt(r, cp, task_text)
        record["context"] = [{"path": e.path, "sha256": e.sha256} for e in built.context]
        actual_log_name = log_name if log_name.startswith(actual_job_id) else f"{actual_job_id}-{log_name}"
        outcome = run_claude_call(
            settings,
            job_id=actual_job_id,
            prompt_text=built.text,
            expected_type=expected_type,
            model=settings.claude_model,
            log_dir=log_dir,
            log_name=actual_log_name,
            claude_runner=claude_runner,
        )
        last_outcome = outcome
        record["cli"] = {
            "name": "claude",
            "version": claude_version(),
            "model": settings.claude_model,
            "actual_model": outcome.actual_model or ((record.get("cli") or {}).get("actual_model")),
        }
        record["container"] = None
        if outcome.last_result is not None:
            res = outcome.last_result
            record["run"] = {
                "exit_code": res.exit_code,
                "elapsed_seconds": res.elapsed_seconds,
                "timed_out": res.timed_out,
                "signal": res.signal_sent,
                "timeout_seconds": int(settings.timeouts["claude"]),
            }
        record["attempt"] = max(1, len(outcome.attempts))
        chk = _claude_check(check_name, outcome)
        record["checks"].append(chk)
        save_job_record(jdir, record)
        return outcome

    # 5. Call analyze_impact
    affected, notes, impact_checks = analyze_impact(
        settings,
        repo=repo,
        instruction=reason,
        diff_text=diff_text,
        call=call,
    )

    for c in impact_checks:
        record["checks"].append({"name": c.name, "status": c.status, "details": list(c.details)})
    save_job_record(jdir, record)

    # Check for FAIL in checks or missing Claude output
    if last_outcome is not None and last_outcome.data is None:
        return fail("claude unavailable")

    if any(c["status"] == "FAIL" for c in record["checks"]):
        return fail("impact analysis check failed")

    # 6. Mark review required on affected chapters
    for item in affected:
        cid = item.get("chapter_id")
        ch_reason = item.get("reason", "")
        detail = f"{reason[:60]}: {ch_reason[:100]}"
        try:
            mark_review_required(settings, work, [cid], code="setting_changed", detail=detail)
        except ChapterError as exc:
            return fail(f"review flags could not be set: {exc}")

    # 7. Complete the Job
    if record["state"] == JobState.RUNNING.name:
        record = apply_transition(record, JobEvent.CLI_EXITED)
    record = apply_transition(record, JobEvent.CHECK_PASSED, needs_validation=False)

    record["checks"].append({
        "name": "impact_affected",
        "status": "PASS",
        "details": [f"{item.get('chapter_id')}: {item.get('reason', '')}" for item in affected],
    })
    if notes:
        record["checks"].append({
            "name": "impact_notes",
            "status": "WARNING",
            "details": list(notes),
        })

    save_job_record(jdir, record)
    return record, affected, notes
