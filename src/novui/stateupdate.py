"""state_update Job: Claude proposes a summary and state patches, Human approves or rejects (Phase 2-C2).

The proposal is kept in Controller data (proposals.py) and never written to the job branch before approval.
approve_state writes the approved patches, approval record, summary.yaml and chapter.yaml (HUMAN_APPROVED)
in a single commit with NovUI-Job and Approval-Id trailers (phase2c-design §3.3).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from novui.approvals import (
    ApprovalError,
    approval_rel,
    build_approval,
    check_approval_applicable,
    is_approval_used,
    reserve_approval_id,
)
from novui.chapters import ChapterError, ensure_main_ready, read_chapters_order
from novui.checks import CheckResult, check_allowed_paths
from novui.claude_cli import run_claude
from novui.claudejob import ClaudeCallOutcome, ClaudeRunner, claude_version, run_claude_call
from novui.config import Settings
from novui.draftjob import read_plan
from novui.foreshadowcheck import check_new_order_issues
from novui.gitinspect import get_changes, run_git
from novui.ids import next_job_id
from novui.jobrecord import apply_transition, load_job_record, new_job_record, save_job_record
from novui.jobrunner import jobs_dir
from novui.proposals import (
    ProposalError,
    find_proposals,
    new_proposal,
    pending_proposal,
    save_proposal,
    with_status,
)
from novui.prompt import build_claude_prompt, load_prompt_template
from novui.schema import SchemaError, validate_or_raise
from novui.semantics import check_chapter
from novui.statecheck import (
    REGISTRY_TARGET,
    check_patch_policy,
    check_patch_target,
    check_summary_patch_consistency,
    check_summary_refs,
    derive_patch_targets,
    dry_run_patch,
    next_knowledge_number,
    pointer_hints,
)
from novui.statepatch import apply_patch, file_sha256, is_noop_patch
from novui.states import ChapterEvent, ChapterState, JobEvent, JobState, next_chapter_state
from novui.validatejob import (
    _chapter_rel,
    _check_dict,
    _check_draft_consistency,
    _claude_check,
    _read_branch_chapter_meta,
    _restore_paths,
    find_chapter_branch,
)
from novui.workrepo import commit_all, head_commit, parse_trailers
from novui.works import WorkInfo
from novui.yamlio import YamlError, dumps_yaml, load_yaml, write_yaml_atomic

STATE_UPDATE_JOB_TYPE = "state_update"
AWAITING_REASON = "awaiting approval of state update proposal"

_CHAR_CTX_RE = re.compile(r"^characters/(C[0-9]{3,})\.yaml$")
_REGISTRY_REL = "foreshadowing/registry.yaml"


def _record_path(settings: Settings, work: WorkInfo, job_id: str) -> Path:
    return jobs_dir(settings, work.work_key) / f"{job_id}.yaml"


def _character_ids(ctx_paths: list[str]) -> list[str]:
    return [m.group(1) for p in ctx_paths if (m := _CHAR_CTX_RE.match(p))]


def _foreshadow_ids(worktree: Path) -> list[str]:
    path = worktree / _REGISTRY_REL
    if not path.is_file():
        return []
    doc = load_yaml(path)
    if not isinstance(doc, list):
        return []
    return [item["id"] for item in doc if isinstance(item, dict) and "id" in item]


def _knowledge_adds(patch: dict[str, Any]) -> int:
    return sum(1 for op in patch["operations"] if op.get("op") == "add" and op.get("path") == "/knowledge/-")


def run_state_update(
    settings: Settings,
    work: WorkInfo,
    chapter_id: str,
    *,
    job_id: str | None = None,
    replace: bool = False,
    claude_runner: ClaudeRunner = run_claude,
) -> dict[str, Any]:
    """Run a state_update Job for an AI_VALIDATED chapter and store the proposal (phase2c-design §2).

    Normal end: WAITING_HUMAN (awaiting approval). Claude output not obtained, a failed check, or a timeout: FAILED.
    The job branch is never written. With replace=True a pending proposal is superseded. Returns the job record.
    """
    repo = work.path
    ensure_main_ready(repo)
    branch = find_chapter_branch(repo, chapter_id)
    meta = _read_branch_chapter_meta(repo, branch, chapter_id)
    if meta["state"] != ChapterState.AI_VALIDATED.name:
        raise ChapterError(
            f"Chapter {chapter_id!r} must be AI_VALIDATED on {branch} to run state_update, got: {meta['state']}"
        )

    pending = pending_proposal(settings, work.work_key, chapter_id)
    old_record: dict[str, Any] | None = None
    if pending is not None:
        if not replace:
            raise ChapterError(
                f"Chapter {chapter_id!r} has a pending state update proposal by {pending['job_id']}; "
                "run reject-state first, or use --replace to supersede it"
            )
        old_path = _record_path(settings, work, pending["job_id"])
        if not old_path.is_file():
            raise ChapterError(f"job record of the pending proposal not found: {pending['job_id']}")
        old_record = load_job_record(old_path)
        if old_record["state"] != JobState.WAITING_HUMAN.name:
            raise ChapterError(f"job {pending['job_id']} is {old_record['state']}, not WAITING_HUMAN")

    actual_job_id = job_id or next_job_id(settings, work.work_key)
    jdir = jobs_dir(settings, work.work_key)
    jdir.mkdir(parents=True, exist_ok=True)

    record = new_job_record(actual_job_id, STATE_UPDATE_JOB_TYPE, chapter_id=chapter_id)
    record["branch"] = branch
    save_job_record(jdir, record)

    # Claude の Job は実行 lock の対象外（§18.1）
    record = apply_transition(record, JobEvent.LOCK_ACQUIRED)
    save_job_record(jdir, record)

    def fail(reason: str) -> dict[str, Any]:
        nonlocal record
        if record["state"] == JobState.RUNNING.name:
            record = apply_transition(record, JobEvent.CLI_EXITED)
        record = apply_transition(record, JobEvent.CHECK_FAILED, reason=reason)
        save_job_record(jdir, record)
        return record

    def add_check(c: CheckResult) -> bool:
        """Append a check result to the record. Returns True if it did not FAIL."""
        record["checks"].append(_check_dict(c))
        save_job_record(jdir, record)
        return c.status != "FAIL"

    try:
        # 1. draft Job の成果物と Context の確認（validate と同じ）
        draft, details = _check_draft_consistency(settings, work, chapter_id, branch)
        if draft is not None:
            record["worktree"] = draft["worktree"]
            record["base_commit"] = draft["base_commit"]
        record["checks"].append({
            "name": "draft_consistency",
            "status": "FAIL" if details else "PASS",
            "details": details,
        })
        save_job_record(jdir, record)
        if details:
            return fail("draft_consistency check failed")
        assert draft is not None
        worktree = Path(draft["worktree"])

        # 2. Context（validate と同じ版 ＋ draft.md）
        plan = read_plan(worktree, chapter_id)
        scene_ids = [s["id"] for s in plan["scenes"]]
        ctx_paths = [e["path"] for e in draft["context"]]
        draft_rel = _chapter_rel(chapter_id, "draft.md")
        if draft_rel not in ctx_paths:
            ctx_paths.append(draft_rel)
        log_dir = jdir / f"{actual_job_id}-logs"

        def call(expected_type: str, task_text: str, log_name: str, check_name: str) -> ClaudeCallOutcome:
            nonlocal record
            built = build_claude_prompt(worktree, ctx_paths, task_text)
            record["context"] = [{"path": e.path, "sha256": e.sha256} for e in built.context]
            outcome = run_claude_call(
                settings,
                job_id=actual_job_id,
                prompt_text=built.text,
                expected_type=expected_type,
                model=settings.claude_model,
                log_dir=log_dir,
                log_name=log_name,
                claude_runner=claude_runner,
            )
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
            record["checks"].append(_claude_check(check_name, outcome))
            save_job_record(jdir, record)
            return outcome

        def call_failed(outcome: ClaudeCallOutcome) -> dict[str, Any] | None:
            nonlocal record
            if outcome.timed_out:
                record = apply_transition(record, JobEvent.TIMED_OUT, reason="claude timed out")
                save_job_record(jdir, record)
                return record
            if outcome.data is None:
                return fail("claude unavailable" if outcome.unavailable else "claude output retry exhausted")
            return None

        # 3. summary
        task = load_prompt_template(
            "state_update",
            chapter_id=chapter_id,
            scene_ids=", ".join(scene_ids),
            character_ids=", ".join(_character_ids(ctx_paths)) or "なし",
            foreshadow_ids=", ".join(_foreshadow_ids(worktree)) or "なし",
        )
        outcome = call("summary", task, f"{actual_job_id}-summary", "claude_summary")
        failed = call_failed(outcome)
        if failed is not None:
            return failed
        summary = outcome.data
        assert summary is not None
        if not add_check(check_summary_refs(
            summary,
            chapter_id=chapter_id,
            character_ids=_character_ids(ctx_paths),
            foreshadow_ids=_foreshadow_ids(worktree),
        )):
            return fail("summary_refs check failed")

        # 4. state_patch（対象ファイルは Controller が summary から決める）
        char_docs = []
        for p in sorted((worktree / "characters").glob("*.yaml")) if (worktree / "characters").is_dir() else []:
            doc = load_yaml(p)
            if isinstance(doc, dict):
                char_docs.append(doc)
        knowledge_start = next_knowledge_number(char_docs)
        knowledge_used = 0
        accepted: list[dict[str, Any]] = []

        for n, target in enumerate(derive_patch_targets(summary), start=1):
            target_path = worktree / target
            if target not in ctx_paths or not target_path.is_file():
                add_check(CheckResult(
                    name="patch_target", status="FAIL",
                    details=(f"{target} is not in the context or does not exist",),
                ))
                return fail("patch_target check failed")
            doc_before = load_yaml(target_path)
            base_hash = file_sha256(target_path)
            task = load_prompt_template(
                "state_patch",
                chapter_id=chapter_id,
                target=target,
                scene_ids=", ".join(scene_ids),
                summary_yaml=dumps_yaml(summary),
                base_hash=base_hash,
                pointer_hints=pointer_hints(target, doc_before),
                next_knowledge_id=f"K{knowledge_start + knowledge_used:03d}",
            )
            outcome = call("state_patch", task, f"{actual_job_id}-patch-{n}", f"claude_state_patch_{n}")
            failed = call_failed(outcome)
            if failed is not None:
                return failed
            patch = dict(outcome.data or {})
            if patch["base_hash"] != base_hash:
                patch["base_hash"] = base_hash
                add_check(CheckResult(name="base_hash_corrected", status="WARNING", details=(target,)))

            ok = add_check(check_patch_target(patch, target))
            ok = ok and add_check(check_patch_policy(
                patch, chapter_id=chapter_id, scene_ids=scene_ids, doc_before=doc_before
            ))
            ok = ok and add_check(dry_run_patch(doc_before, patch))
            if not ok:
                return fail(f"patch checks failed for {target}")

            # 伏線の順序：Patch で新しく生じた問題だけを WARNING にする（phase2d-design 決定 4、§9）
            if target == REGISTRY_TARGET:
                try:
                    order = read_chapters_order(worktree)
                except (ChapterError, SchemaError, YamlError) as exc:
                    add_check(CheckResult(
                        name="foreshadow_order", status="WARNING",
                        details=(f"chapters-order.yaml could not be read: {exc}",),
                    ))
                else:
                    add_check(check_new_order_issues(order, doc_before, apply_patch(doc_before, patch)))

            if is_noop_patch(patch):
                add_check(CheckResult(name="patch_noop", status="PASS", details=(target,)))
                continue
            accepted.append(patch)
            knowledge_used += _knowledge_adds(patch)

        add_check(check_summary_patch_consistency(summary, accepted))

        # 5. 提案の保存。state_update は作業ブランチに何も書かない
        changes = get_changes(worktree)
        if changes:
            _restore_paths(worktree, sorted({c.path for c in changes}))
            add_check(CheckResult(
                name="worktree_clean", status="FAIL", details=tuple(sorted(c.path for c in changes)),
            ))
            return fail("worktree is not clean after state_update")

        proposal = new_proposal(
            job_id=actual_job_id,
            chapter_id=chapter_id,
            branch=branch,
            branch_head=head_commit(worktree, "HEAD"),
            summary=summary,
            patches=accepted,
        )
        if pending is not None and old_record is not None:
            superseded = with_status(pending, "superseded", reason=f"superseded by {actual_job_id}")
            save_proposal(settings, work.work_key, superseded)
            old_record = apply_transition(
                old_record, JobEvent.HUMAN_CANCEL, reason=f"superseded by {actual_job_id}"
            )
            save_job_record(jdir, old_record)
        save_proposal(settings, work.work_key, proposal)

        record = apply_transition(record, JobEvent.CLI_EXITED)
        record = apply_transition(record, JobEvent.NEEDS_HUMAN_INPUT, reason=AWAITING_REASON)
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


def _pending_with_record(
    settings: Settings, work: WorkInfo, chapter_id: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    proposal = pending_proposal(settings, work.work_key, chapter_id)
    if proposal is None:
        raise ChapterError(f"Chapter {chapter_id!r} has no pending state update proposal")
    path = _record_path(settings, work, proposal["job_id"])
    if not path.is_file():
        raise ChapterError(f"state_update job record not found: {proposal['job_id']}")
    record = load_job_record(path)
    if record["job_type"] != STATE_UPDATE_JOB_TYPE or record["chapter_id"] != chapter_id:
        raise ChapterError(f"job {proposal['job_id']} is not a state_update job of {chapter_id}")
    return proposal, record


def _finish_approval(
    settings: Settings,
    work: WorkInfo,
    proposal: dict[str, Any],
    record: dict[str, Any],
    approval_id: str | None,
) -> dict[str, Any]:
    """Record the approval in the job record and the proposal (steps after the commit)."""
    jdir = jobs_dir(settings, work.work_key)
    if record["state"] == JobState.WAITING_HUMAN.name:
        record = dict(record)
        record["approval_id"] = approval_id
        record = apply_transition(record, JobEvent.HUMAN_CONTINUE)
        save_job_record(jdir, record)
    save_proposal(settings, work.work_key, with_status(proposal, "approved", approval_id=approval_id))
    return record


def _invalidate(
    settings: Settings,
    work: WorkInfo,
    proposal: dict[str, Any],
    record: dict[str, Any],
    reason: str,
) -> None:
    save_proposal(settings, work.work_key, with_status(proposal, "invalid", reason=reason))
    record = apply_transition(record, JobEvent.HUMAN_CANCEL, reason=f"invalid: {reason}")
    save_job_record(jobs_dir(settings, work.work_key), record)
    raise ProposalError(f"proposal {proposal['job_id']} is invalid: {reason}; run state-update again")


def approve_state(settings: Settings, work: WorkInfo, chapter_id: str) -> dict[str, Any]:
    """Approve the pending proposal: apply patches and write approval record, summary.yaml and chapter.yaml.

    One commit on the job branch (NovUI-Job, Approval-Id when there are patches) moves the chapter to
    HUMAN_APPROVED. An invalid proposal is marked invalid, its job CANCELLED and ProposalError raised.
    Returns the updated job record.
    """
    repo = work.path
    ensure_main_ready(repo)
    branch = find_chapter_branch(repo, chapter_id)
    proposal, record = _pending_with_record(settings, work, chapter_id)
    job_id = proposal["job_id"]
    patches = [e["patch"] for e in proposal["patches"]]

    worktree = Path(record["worktree"]) if record["worktree"] else None
    if worktree is None or not worktree.is_dir():
        raise ChapterError(f"worktree for {branch} not found")
    if run_git(worktree, "branch", "--show-current").decode("utf-8").strip() != branch:
        raise ChapterError(f"worktree {worktree} is not on {branch}")
    if get_changes(worktree):
        raise ChapterError(f"worktree {worktree} has uncommitted changes")
    if proposal["branch"] != branch:
        raise ChapterError(f"proposal branch {proposal['branch']!r} does not match {branch!r}")

    meta = _read_branch_chapter_meta(repo, branch, chapter_id)
    head = head_commit(worktree, "HEAD")

    # 復旧：commit は済んでいるが、Job 記録・提案が更新されていない
    if meta["state"] == ChapterState.HUMAN_APPROVED.name and head != proposal["branch_head"]:
        message = run_git(worktree, "log", "-1", "--format=%B", "HEAD").decode("utf-8")
        trailers = parse_trailers(message)
        parent = run_git(worktree, "rev-parse", "HEAD^").decode("utf-8").strip()
        approvals = trailers.get("Approval-Id", [])
        if (
            trailers.get("NovUI-Job") == [job_id]
            and parent == proposal["branch_head"]
            and len(approvals) == (1 if patches else 0)
        ):
            return _finish_approval(settings, work, proposal, record, approvals[0] if approvals else None)

    if record["state"] != JobState.WAITING_HUMAN.name:
        raise ChapterError(f"state_update job {job_id} is {record['state']}, not WAITING_HUMAN")

    # 無効の検査（§2.7）
    if head != proposal["branch_head"]:
        _invalidate(settings, work, proposal, record,
                    f"branch head {head} differs from the proposal's branch_head {proposal['branch_head']}")
    if meta["state"] != ChapterState.AI_VALIDATED.name:
        _invalidate(settings, work, proposal, record, f"chapter is {meta['state']}, not AI_VALIDATED")
    plan = read_plan(worktree, chapter_id)
    scene_ids = [s["id"] for s in plan["scenes"]]
    for p in patches:
        target_path = worktree / p["target"]
        if not target_path.is_file():
            _invalidate(settings, work, proposal, record, f"target {p['target']} does not exist")
        if file_sha256(target_path) != p["base_hash"]:
            _invalidate(settings, work, proposal, record, f"base_hash mismatch for {p['target']}")
        doc_before = load_yaml(target_path)
        for result in (
            check_patch_policy(p, chapter_id=chapter_id, scene_ids=scene_ids, doc_before=doc_before),
            dry_run_patch(doc_before, p),
        ):
            if result.status == "FAIL":
                _invalidate(settings, work, proposal, record,
                            f"{result.name} failed for {p['target']}: {'; '.join(result.details)}")

    summary_rel = _chapter_rel(chapter_id, "summary.yaml")
    chapter_rel = _chapter_rel(chapter_id, "chapter.yaml")
    allowed = {p["target"] for p in patches} | {summary_rel, chapter_rel}

    approval_id: str | None = None
    approval: dict[str, Any] | None = None
    if patches:
        approval_id = reserve_approval_id(settings, work, extra_dirs=[worktree / ".novui" / "approvals"])
        approval = build_approval(approval_id, job_id=job_id, patches=patches)
        check_approval_applicable(approval, patches)
        if is_approval_used(repo, approval_id, branch=branch):
            raise ApprovalError(f"approval {approval_id} is already used")
        allowed.add(approval_rel(approval_id))

    try:
        for p in patches:
            after = apply_patch(load_yaml(worktree / p["target"]), p)
            write_yaml_atomic(worktree / p["target"], after)
        if approval is not None and approval_id is not None:
            write_yaml_atomic(worktree / approval_rel(approval_id), approval)
        validate_or_raise(proposal["summary"], "summary")
        write_yaml_atomic(worktree / summary_rel, proposal["summary"])

        new_meta = dict(meta)
        new_meta["state"] = next_chapter_state(
            ChapterState.AI_VALIDATED, ChapterEvent.STATE_PATCH_APPROVED,
            review_required=bool(meta["review_required"]),
        ).name
        new_meta["last_job"] = job_id
        validate_or_raise(new_meta, "chapter")
        sem_errs = check_chapter(new_meta)
        if sem_errs:
            raise SchemaError(f"Semantic validation failed for chapter: {sem_errs}", errors=sem_errs)
        write_yaml_atomic(worktree / chapter_rel, new_meta)

        allowed_check = check_allowed_paths(get_changes(worktree), allowed)
        if allowed_check.status != "PASS":
            raise ChapterError(f"Changes outside allowed paths: {list(allowed_check.details)}")

        trailers_out = [("NovUI-Job", job_id)]
        if approval_id is not None:
            trailers_out.append(("Approval-Id", approval_id))
        commit = commit_all(
            worktree,
            f"state update approved {chapter_id} by {job_id}",
            trailers_out,
            name=settings.git_name,
            email=settings.git_email,
        )
        if commit is None:
            raise ChapterError(f"No changes to commit on job branch for {chapter_id}")
    except BaseException:
        _restore_paths(worktree, sorted(allowed))
        raise

    return _finish_approval(settings, work, proposal, record, approval_id)


def reject_state(settings: Settings, work: WorkInfo, chapter_id: str, reason: str) -> dict[str, Any]:
    """Reject the pending proposal (proposal rejected, job CANCELLED). The job branch is not changed."""
    if reason is None or not reason.strip():
        raise ValueError("reason is required to reject a state update proposal")
    reason = reason.strip()
    proposal, record = _pending_with_record(settings, work, chapter_id)
    if record["state"] != JobState.WAITING_HUMAN.name:
        raise ChapterError(f"state_update job {proposal['job_id']} is {record['state']}, not WAITING_HUMAN")
    save_proposal(settings, work.work_key, with_status(proposal, "rejected", reason=reason))
    record = apply_transition(record, JobEvent.HUMAN_CANCEL, reason=reason)
    save_job_record(jobs_dir(settings, work.work_key), record)
    return record


def show_proposal(settings: Settings, work: WorkInfo, chapter_id: str) -> dict[str, Any] | None:
    """The latest proposal of the chapter (any status), or None."""
    proposals = find_proposals(settings, work.work_key, chapter_id)
    return proposals[-1] if proposals else None
