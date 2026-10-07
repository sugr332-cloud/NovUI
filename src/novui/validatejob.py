"""Validate job: mechanical checks, integrity_review and writing_review (Phase 2-B2).

The validate Job runs on the chapter's job branch created by the draft Job (phase2-plan §2)
and writes review.yaml and chapter.yaml there. It never writes to main.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from novui.chapters import ChapterError, ensure_main_ready
from novui.checks import CheckResult, check_allowed_paths, check_char_range
from novui.claude_cli import run_claude
from novui.claudejob import ClaudeCallOutcome, ClaudeRunner, claude_version, run_claude_call
from novui.config import Settings
from novui.draftjob import read_plan
from novui.gitinspect import GitError, get_changes, run_git
from novui.ids import next_job_id
from novui.jobrecord import apply_transition, load_job_record, new_job_record, now_iso, save_job_record
from novui.jobrunner import jobs_dir
from novui.mechanical import load_mechanical_inputs, run_draft_mechanical_checks
from novui.prompt import build_claude_prompt, load_prompt_template
from novui.schema import SchemaError, validate_or_raise
from novui.semantics import check_chapter
from novui.states import ChapterEvent, ChapterState, JobEvent, JobState, next_chapter_state
from novui.workrepo import chapter_branches, commit_all
from novui.works import WorkInfo
from novui.yamlio import YamlError, load_yaml, loads_yaml, write_yaml_atomic

DECISION_ACTIONS: tuple[str, ...] = ("CONTINUE", "OVERRIDE", "REQUEST_FIX", "CANCEL")
SKIP_REASON_PREFIX = "validation skipped: "

_DECISION_EVENTS: dict[str, JobEvent] = {
    "CONTINUE": JobEvent.HUMAN_CONTINUE,
    "OVERRIDE": JobEvent.HUMAN_OVERRIDE,
    "REQUEST_FIX": JobEvent.HUMAN_REQUEST_FIX,
    "CANCEL": JobEvent.HUMAN_CANCEL,
}


def _chapter_rel(chapter_id: str, name: str) -> str:
    return f"chapters/{chapter_id}/{name}"


def find_chapter_branch(repo: Path, chapter_id: str) -> str:
    """Return the single job branch of the chapter. Raises ChapterError if there is none or more than one."""
    branches = chapter_branches(repo, chapter_id)
    if not branches:
        raise ChapterError(f"Chapter {chapter_id!r} has no job branch; run draft first")
    if len(branches) > 1:
        raise ChapterError(f"Chapter {chapter_id!r} has multiple job branches: {branches}")
    return branches[0]


def _read_branch_yaml(repo: Path, branch: str, rel: str) -> Any | None:
    """Read a YAML file from a branch tip without a worktree. None if the file does not exist."""
    try:
        data = run_git(repo, "show", f"{branch}:{rel}")
    except GitError:
        return None
    return loads_yaml(data.decode("utf-8"))


def _read_branch_chapter_meta(repo: Path, branch: str, chapter_id: str) -> dict[str, Any]:
    meta = _read_branch_yaml(repo, branch, _chapter_rel(chapter_id, "chapter.yaml"))
    if not isinstance(meta, dict):
        raise ChapterError(f"chapter.yaml not found on {branch}")
    validate_or_raise(meta, "chapter")
    sem_errs = check_chapter(meta)
    if sem_errs:
        raise SchemaError(f"Semantic validation failed for chapter: {sem_errs}", errors=sem_errs)
    return meta


def _sha256_file(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest().lower()


def _check_draft_consistency(
    settings: Settings,
    work: WorkInfo,
    chapter_id: str,
    branch: str,
) -> tuple[dict[str, Any] | None, list[str]]:
    """Check the draft Job record, its worktree, base commit and Context hashes (§11.1, §16.2).

    Returns (draft_record, details). details is empty when consistent.
    """
    details: list[str] = []
    draft_job_id = branch.rsplit("/", 1)[-1]
    rec_path = jobs_dir(settings, work.work_key) / f"{draft_job_id}.yaml"
    if not rec_path.is_file():
        return None, [f"draft job record not found: {draft_job_id}"]
    try:
        draft = load_job_record(rec_path)
    except (SchemaError, YamlError) as exc:
        return None, [f"draft job record invalid: {exc}"]

    if draft["job_type"] != "draft":
        details.append(f"job {draft_job_id} is not a draft job: {draft['job_type']}")
    if draft["chapter_id"] != chapter_id:
        details.append(f"draft job chapter_id {draft['chapter_id']!r} does not match {chapter_id!r}")
    if draft["state"] != JobState.COMPLETED.name:
        details.append(f"draft job state is {draft['state']}, expected COMPLETED")
    if draft["branch"] != branch:
        details.append(f"draft job branch {draft['branch']!r} does not match {branch!r}")
    if not draft["base_commit"]:
        details.append("draft job has no base_commit")
    if not draft["worktree"]:
        details.append("draft job has no worktree")
    if details:
        return draft, details

    worktree = Path(draft["worktree"])
    if not worktree.is_dir():
        return draft, [f"worktree not found: {worktree}"]
    try:
        cur = run_git(worktree, "branch", "--show-current").decode("utf-8").strip()
    except GitError as exc:
        return draft, [f"worktree is not a git worktree: {exc}"]
    if cur != branch:
        return draft, [f"worktree branch {cur!r} does not match {branch!r}"]
    changes = get_changes(worktree)
    if changes:
        details.append("worktree has uncommitted changes: " + ", ".join(sorted(c.path for c in changes)))
    try:
        run_git(worktree, "merge-base", "--is-ancestor", draft["base_commit"], "HEAD")
    except GitError:
        details.append(f"base_commit {draft['base_commit']} is not an ancestor of {branch}")

    for name in ("draft.md", "plan.yaml", "chapter.yaml"):
        if not (worktree / _chapter_rel(chapter_id, name)).is_file():
            details.append(f"required file missing: {_chapter_rel(chapter_id, name)}")

    for entry in draft["context"]:
        p = worktree / entry["path"]
        if not p.is_file():
            details.append(f"context file missing: {entry['path']}")
        elif _sha256_file(p) != entry["sha256"]:
            details.append(f"context hash mismatch: {entry['path']}")
    return draft, details


def _check_dict(c: CheckResult) -> dict[str, Any]:
    return {"name": c.name, "status": c.status, "details": list(c.details)}


def _claude_check(name: str, outcome: ClaudeCallOutcome) -> dict[str, Any]:
    details = [
        f"attempt {a.attempt}: {a.error_kind}: {a.errors[0]}" if a.errors else f"attempt {a.attempt}: {a.error_kind}"
        for a in outcome.attempts
        if not a.ok
    ]
    if outcome.data is not None:
        status = "PASS"
    elif outcome.unavailable:
        status = "WARNING"
        details.append("claude_unavailable: every attempt returned is_error: true")
    else:
        status = "FAIL"
        if outcome.timed_out:
            details.append("timed_out")
    return {"name": name, "status": status, "details": details}


def _restore_paths(worktree: Path, rels: list[str]) -> None:
    """Discard uncommitted changes of rels in worktree (tracked files restored, new files removed)."""
    for rel in rels:
        try:
            run_git(worktree, "reset", "-q", "--", rel)
        except GitError:
            pass
        try:
            run_git(worktree, "cat-file", "-e", f"HEAD:{rel}")
            run_git(worktree, "checkout", "HEAD", "--", rel)
        except GitError:
            (worktree / rel).unlink(missing_ok=True)


def _write_and_commit(
    settings: Settings,
    worktree: Path,
    chapter_id: str,
    job_id: str,
    subject: str,
    review: dict[str, Any],
    meta: dict[str, Any] | None,
) -> str:
    """Write review.yaml (and chapter.yaml if meta is given) on the job branch and commit with NovUI-Job.

    Only review.yaml and chapter.yaml may change (§8.1). On any failure the files are restored.
    """
    review_rel = _chapter_rel(chapter_id, "review.yaml")
    chapter_rel = _chapter_rel(chapter_id, "chapter.yaml")
    validate_or_raise(review, "review")
    if meta is not None:
        validate_or_raise(meta, "chapter")
        sem_errs = check_chapter(meta)
        if sem_errs:
            raise SchemaError(f"Semantic validation failed for chapter: {sem_errs}", errors=sem_errs)

    written = [review_rel, chapter_rel]
    try:
        write_yaml_atomic(worktree / review_rel, review)
        if meta is not None:
            write_yaml_atomic(worktree / chapter_rel, meta)
        allowed = check_allowed_paths(get_changes(worktree), {review_rel, chapter_rel})
        if allowed.status != "PASS":
            raise ChapterError(f"Changes outside allowed paths: {list(allowed.details)}")
        commit = commit_all(
            worktree, subject, [("NovUI-Job", job_id)], name=settings.git_name, email=settings.git_email
        )
        if commit is None:
            raise ChapterError(f"No changes to commit on job branch for {chapter_id}")
        return commit
    except BaseException:
        _restore_paths(worktree, written)
        raise


def run_validate_job(
    settings: Settings,
    work: WorkInfo,
    chapter_id: str,
    *,
    job_id: str | None = None,
    claude_runner: ClaudeRunner = run_claude,
) -> dict[str, Any]:
    """Run a validate Job for a DRAFTED chapter on its job branch.

    COMPLETED (chapter AI_VALIDATED) only when all mechanical checks PASS, integrity_review is PASS
    and writing_review was obtained. WARNING / STOP / Claude unavailable -> WAITING_HUMAN (chapter stays
    DRAFTED). Draft inconsistency, invalid Claude output after retries, timeout -> FAILED.
    Returns the job record.
    """
    repo = work.path
    ensure_main_ready(repo)
    branch = find_chapter_branch(repo, chapter_id)
    meta = _read_branch_chapter_meta(repo, branch, chapter_id)
    if meta["state"] != ChapterState.DRAFTED.name:
        raise ChapterError(f"Chapter {chapter_id!r} must be DRAFTED on {branch} to validate, got: {meta['state']}")
    prev_review = _read_branch_yaml(repo, branch, _chapter_rel(chapter_id, "review.yaml"))
    if isinstance(prev_review, dict) and prev_review.get("decision") is None:
        raise ChapterError(
            f"review.yaml on {branch} by {prev_review.get('job_id')} is waiting for a Human decision; "
            "to run validate again, first CANCEL that validate Job (decide --action CANCEL)"
        )

    actual_job_id = job_id or next_job_id(settings, work.work_key)
    jdir = jobs_dir(settings, work.work_key)
    jdir.mkdir(parents=True, exist_ok=True)

    record = new_job_record(actual_job_id, "validate", chapter_id=chapter_id)
    record["branch"] = branch
    save_job_record(jdir, record)

    # Claude の Job は実行 lock の対象外（§18.1）
    record = apply_transition(record, JobEvent.LOCK_ACQUIRED)
    save_job_record(jdir, record)

    try:
        # 1. draft Job の成果物と Context の確認（§11.1）
        draft, details = _check_draft_consistency(settings, work, chapter_id, branch)
        if draft is not None:
            record["worktree"] = draft["worktree"]
            record["base_commit"] = draft["base_commit"]
        record["checks"].append({
            "name": "draft_consistency",
            "status": "FAIL" if details else "PASS",
            "details": details,
        })
        if details:
            record = apply_transition(record, JobEvent.CLI_EXITED)
            record = apply_transition(record, JobEvent.CHECK_FAILED, reason="draft_consistency check failed")
            save_job_record(jdir, record)
            return record
        assert draft is not None
        worktree = Path(draft["worktree"])
        save_job_record(jdir, record)

        # 2. 機械検査（§11.3、§8.3 の 6）。validate の時点の作業ブランチの内容で行う
        plan = read_plan(worktree, chapter_id)
        text = (worktree / _chapter_rel(chapter_id, "draft.md")).read_text(encoding="utf-8")
        mech: list[CheckResult] = [check_char_range(text, plan["target_chars"]["min"], plan["target_chars"]["max"])]
        mech += run_draft_mechanical_checks(text, plan, load_mechanical_inputs(worktree))
        mechanical = [_check_dict(c) for c in mech]

        # 3. Claude（AGY に渡したのと同じ版の Context と本文）
        ctx_paths = [e["path"] for e in draft["context"]]
        draft_rel = _chapter_rel(chapter_id, "draft.md")
        if draft_rel not in ctx_paths:
            ctx_paths.append(draft_rel)
        log_dir = jdir / f"{actual_job_id}-logs"

        def call(expected_type: str) -> ClaudeCallOutcome:
            nonlocal record
            built = build_claude_prompt(
                worktree, ctx_paths, load_prompt_template(expected_type, chapter_id=chapter_id)
            )
            record["context"] = [{"path": e.path, "sha256": e.sha256} for e in built.context]
            outcome = run_claude_call(
                settings,
                job_id=actual_job_id,
                prompt_text=built.text,
                expected_type=expected_type,
                model=settings.claude_model,
                log_dir=log_dir,
                log_name=f"{actual_job_id}-{expected_type}",
                claude_runner=claude_runner,
            )
            record["cli"] = {
                "name": "claude",
                "version": claude_version(),
                "model": settings.claude_model,
                "actual_model": outcome.actual_model
                or ((record.get("cli") or {}).get("actual_model")),
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
            record["checks"].append(_claude_check(f"claude_{expected_type}", outcome))
            save_job_record(jdir, record)
            return outcome

        integ = call("integrity_review")
        writ: ClaudeCallOutcome | None = None
        if integ.data is not None:
            writ = call("writing_review")

        for outcome in (integ, writ):
            if outcome is not None and outcome.timed_out:
                record = apply_transition(record, JobEvent.TIMED_OUT, reason="claude timed out")
                save_job_record(jdir, record)
                return record

        record = apply_transition(record, JobEvent.CLI_EXITED)
        save_job_record(jdir, record)

        for outcome in (integ, writ):
            if outcome is not None and outcome.data is None and not outcome.unavailable:
                record = apply_transition(record, JobEvent.CHECK_FAILED, reason="claude output retry exhausted")
                save_job_record(jdir, record)
                return record

        ref_details = [
            f"{d['type']} chapter_id {d['chapter_id']!r} does not match {chapter_id!r}"
            for d in (integ.data, writ.data if writ else None)
            if d is not None and d["chapter_id"] != chapter_id
        ]
        record["checks"].append({
            "name": "review_refs",
            "status": "FAIL" if ref_details else "PASS",
            "details": ref_details,
        })
        if ref_details:
            record = apply_transition(record, JobEvent.CHECK_FAILED, reason="review_refs check failed")
            save_job_record(jdir, record)
            return record

        # 4. review.yaml の組み立てと判定（§11.4）
        unavailable = integ.unavailable or (writ is not None and writ.unavailable)
        review = {
            "chapter_id": chapter_id,
            "job_id": actual_job_id,
            "base_commit": draft["base_commit"],
            "mechanical": mechanical,
            "integrity": integ.data,
            "writing": writ.data if writ is not None else None,
            "decision": None,
        }
        reasons: list[str] = []
        if unavailable:
            reasons.append("claude_unavailable")
        mech_warn = [c["name"] for c in mechanical if c["status"] != "PASS"]
        if mech_warn:
            reasons.append("mechanical: " + ", ".join(mech_warn))
        if integ.data is not None and integ.data["result"] != "PASS":
            reasons.append(f"integrity: {integ.data['result']}")
        # writing_review の指摘だけでは止めない（§11.2）
        auto_accept = not reasons

        new_meta: dict[str, Any] | None = None
        if auto_accept:
            new_meta = dict(meta)
            new_meta["state"] = next_chapter_state(
                ChapterState.DRAFTED, ChapterEvent.VALIDATION_ACCEPTED, review_required=bool(meta["review_required"])
            ).name
            new_meta["validation_skipped"] = False
            new_meta["last_job"] = actual_job_id

        _write_and_commit(
            settings, worktree, chapter_id, actual_job_id,
            f"validate {chapter_id} by {actual_job_id}", review, new_meta,
        )

        record = apply_transition(record, JobEvent.CHECK_PASSED, needs_validation=True)
        if auto_accept:
            record = apply_transition(record, JobEvent.VALIDATION_PASSED)
        else:
            record = apply_transition(record, JobEvent.VALIDATION_NEEDS_HUMAN, reason="; ".join(reasons))
        save_job_record(jdir, record)
        return record

    except BaseException:
        cur = record.get("state")
        try:
            if cur == JobState.RUNNING.name:
                record = apply_transition(record, JobEvent.CLI_EXITED)
                cur = JobState.CHECKING.name
            if cur == JobState.CHECKING.name:
                record = apply_transition(record, JobEvent.CHECK_FAILED)
                save_job_record(jdir, record)
        except Exception:
            pass
        raise


def _load_waiting_review(
    settings: Settings,
    work: WorkInfo,
    chapter_id: str,
) -> tuple[dict[str, Any], dict[str, Any], Path, dict[str, Any]]:
    """Return (review, validate_record, worktree, chapter_meta) for a validate Job waiting for a Human decision."""
    repo = work.path
    ensure_main_ready(repo)
    branch = find_chapter_branch(repo, chapter_id)
    review = _read_branch_yaml(repo, branch, _chapter_rel(chapter_id, "review.yaml"))
    if not isinstance(review, dict):
        raise ChapterError(f"review.yaml not found on {branch}")
    validate_or_raise(review, "review")
    if review["decision"] is not None:
        raise ChapterError(f"review.yaml on {branch} is already decided: {review['decision']['action']}")

    rec_path = jobs_dir(settings, work.work_key) / f"{review['job_id']}.yaml"
    if not rec_path.is_file():
        raise ChapterError(f"validate job record not found: {review['job_id']}")
    record = load_job_record(rec_path)
    if record["job_type"] != "validate" or record["chapter_id"] != chapter_id:
        raise ChapterError(f"job {review['job_id']} is not a validate job of {chapter_id}")
    if record["state"] != JobState.WAITING_HUMAN.name:
        raise ChapterError(f"validate job {review['job_id']} is {record['state']}, not WAITING_HUMAN")

    worktree = Path(record["worktree"]) if record["worktree"] else None
    if worktree is None or not worktree.is_dir():
        raise ChapterError(f"worktree for {branch} not found")
    if run_git(worktree, "branch", "--show-current").decode("utf-8").strip() != branch:
        raise ChapterError(f"worktree {worktree} is not on {branch}")
    if get_changes(worktree):
        raise ChapterError(f"worktree {worktree} has uncommitted changes")
    meta = load_yaml(worktree / _chapter_rel(chapter_id, "chapter.yaml"))
    validate_or_raise(meta, "chapter")
    if meta["state"] != ChapterState.DRAFTED.name:
        raise ChapterError(f"Chapter {chapter_id!r} must be DRAFTED on {branch}, got: {meta['state']}")
    return review, record, worktree, meta


def _review_unavailable(review: dict[str, Any]) -> bool:
    return review["integrity"] is None or review["writing"] is None


def _is_stop(review: dict[str, Any]) -> bool:
    return review["integrity"] is not None and review["integrity"]["result"] == "STOP"


def _apply_decision(
    settings: Settings,
    work: WorkInfo,
    chapter_id: str,
    action: str,
    reason: str | None,
    *,
    skip: bool,
) -> dict[str, Any]:
    if action not in DECISION_ACTIONS:
        raise ValueError(f"Invalid action {action!r}; must be one of {list(DECISION_ACTIONS)}")
    reason = reason.strip() if reason is not None and reason.strip() else None
    if action in ("OVERRIDE", "REQUEST_FIX") and reason is None:
        raise ValueError(f"reason is required for {action}")

    review, record, worktree, meta = _load_waiting_review(settings, work, chapter_id)
    job_id = record["job_id"]

    if action in ("CONTINUE", "OVERRIDE") and _is_stop(review):
        raise ChapterError("integrity_review is STOP; only REQUEST_FIX or CANCEL is allowed")
    if skip and not _review_unavailable(review):
        raise ChapterError("validation can be skipped only when Claude review could not be obtained (§11.6)")
    if not skip and action in ("CONTINUE", "OVERRIDE") and _review_unavailable(review):
        raise ChapterError(
            f"Claude review was not obtained; {action} is not allowed. "
            "To retry, first CANCEL this validate Job (decide --action CANCEL), "
            "then run validate again. To skip validation, use skip-validation --reason <text>"
        )

    if skip:
        reason = SKIP_REASON_PREFIX + (reason or "")
    review = dict(review)
    review["decision"] = {"action": action, "reason": reason, "decided_at": now_iso()}

    new_meta: dict[str, Any] | None = None
    if action in ("CONTINUE", "OVERRIDE"):
        event = ChapterEvent.VALIDATION_SKIPPED if skip else ChapterEvent.VALIDATION_ACCEPTED
        new_meta = dict(meta)
        new_meta["state"] = next_chapter_state(
            ChapterState.DRAFTED, event, review_required=bool(meta["review_required"])
        ).name
        new_meta["validation_skipped"] = skip
        new_meta["last_job"] = job_id

    _write_and_commit(
        settings, worktree, chapter_id, job_id,
        f"validation {action.lower()} {chapter_id} by {job_id}", review, new_meta,
    )

    record = apply_transition(record, _DECISION_EVENTS[action], reason=reason)
    save_job_record(jobs_dir(settings, work.work_key), record)
    return record


def decide_validation(
    settings: Settings,
    work: WorkInfo,
    chapter_id: str,
    action: str,
    reason: str | None = None,
) -> dict[str, Any]:
    """Apply a Human decision (§11.4) to the validate Job waiting on the chapter.

    CONTINUE / OVERRIDE -> chapter AI_VALIDATED (not allowed for STOP or when Claude review is missing).
    REQUEST_FIX -> decision recorded, chapter stays DRAFTED (no fix Job is created).
    CANCEL -> Job CANCELLED, chapter stays DRAFTED. OVERRIDE and REQUEST_FIX require a reason.
    """
    return _apply_decision(settings, work, chapter_id, action, reason, skip=False)


def skip_validation(settings: Settings, work: WorkInfo, chapter_id: str, reason: str) -> dict[str, Any]:
    """Skip validation when Claude review could not be obtained (§11.6).

    Recorded as decision OVERRIDE with reason "validation skipped: <reason>" and
    chapter.yaml validation_skipped: true (AI_VALIDATED).
    """
    return _apply_decision(settings, work, chapter_id, "OVERRIDE", reason, skip=True)
