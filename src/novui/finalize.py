"""Final approval (HUMAN_APPROVED -> FINAL by merge into main), protected change audit and discard (Phase 2-C3).

final_approve writes main, so every audit and guard runs before the merge starts. merge_job_branch aborts the merge
on any failure, which leaves main untouched (phase2c-design §4.3, §6).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from novui.approvals import approval_rel, is_approval_used
from novui.chapters import ChapterError, ensure_main_ready, read_chapter_meta
from novui.config import Settings
from novui.gitinspect import GitError, get_changes, run_git
from novui.jobrecord import apply_transition, load_job_record, save_job_record
from novui.jobrunner import jobs_dir
from novui.paths import is_safe_relpath
from novui.proposals import find_proposals, save_proposal, with_status
from novui.protected import is_protected
from novui.schema import SchemaError, validate_or_raise
from novui.semantics import check_approval, check_chapter
from novui.states import ChapterEvent, ChapterState, JobEvent, JobState, next_chapter_state
from novui.validatejob import _read_branch_chapter_meta, find_chapter_branch
from novui.workrepo import JobWorktree, head_commit, merge_job_branch, parse_trailers, remove_job_worktree
from novui.works import WorkInfo
from novui.yamlio import dumps_yaml, loads_yaml

_APPROVALS_PREFIX = ".novui/approvals/"
_COMMIT_RE_LEN = 40


class FinalizeError(Exception):
    """Raised when final approval is refused (audit violation, failed cleanup)."""


@dataclass(frozen=True)
class CycleInfo:
    """The job branch of a chapter, verified against the draft Job record that created it."""

    branch: str
    draft_job_id: str
    jw: JobWorktree
    draft_record: dict[str, Any]


def cycle_info(
    settings: Settings,
    work: WorkInfo,
    chapter_id: str,
    *,
    require_worktree: bool,
) -> CycleInfo:
    """Identify the chapter's job branch and its worktree from the branch name, verified with the Job record.

    The draft Job ID is derived from the branch name (ai/<chapter>/<job_id>). The draft Job record must exist and its
    job_type, chapter_id, branch and worktree must match; otherwise ChapterError (nothing is deleted or merged).
    With require_worktree the worktree must exist, be on the branch and have no uncommitted changes.
    """
    repo = work.path
    branch = find_chapter_branch(repo, chapter_id)
    draft_job_id = branch.rsplit("/", 1)[-1]
    rec_path = jobs_dir(settings, work.work_key) / f"{draft_job_id}.yaml"
    if not rec_path.is_file():
        raise ChapterError(f"draft job record not found for {branch}: {draft_job_id}")
    record = load_job_record(rec_path)

    problems: list[str] = []
    if record["job_type"] != "draft":
        problems.append(f"job {draft_job_id} is not a draft job: {record['job_type']}")
    if record["chapter_id"] != chapter_id:
        problems.append(f"job {draft_job_id} chapter_id {record['chapter_id']!r} does not match {chapter_id!r}")
    if record["branch"] != branch:
        problems.append(f"job {draft_job_id} branch {record['branch']!r} does not match {branch!r}")
    expected_wt = settings.worktree_root / work.work_key / draft_job_id
    if not record["worktree"] or Path(record["worktree"]) != expected_wt:
        problems.append(f"job {draft_job_id} worktree {record['worktree']!r} does not match {str(expected_wt)!r}")
    base = record["base_commit"]
    if not base or len(base) != _COMMIT_RE_LEN:
        problems.append(f"job {draft_job_id} has no valid base_commit")
    if problems:
        raise ChapterError("job branch does not match its draft job record: " + "; ".join(problems))
    try:
        run_git(repo, "merge-base", "--is-ancestor", base, branch)
    except GitError:
        raise ChapterError(f"base_commit {base} is not an ancestor of {branch}") from None

    if require_worktree:
        if not expected_wt.is_dir():
            raise ChapterError(f"worktree not found: {expected_wt}")
        try:
            current = run_git(expected_wt, "branch", "--show-current").decode("utf-8").strip()
        except GitError as exc:
            raise ChapterError(f"worktree {expected_wt} is not a git worktree: {exc}") from exc
        if current != branch:
            raise ChapterError(f"worktree {expected_wt} is on {current!r}, not {branch!r}")
        if get_changes(expected_wt):
            raise ChapterError(f"worktree {expected_wt} has uncommitted changes")

    return CycleInfo(branch=branch, draft_job_id=draft_job_id, jw=JobWorktree(branch, expected_wt, base), draft_record=record)


def waiting_jobs_on_branch(settings: Settings, work: WorkInfo, branch: str) -> list[dict[str, Any]]:
    """Job records bound to branch that are WAITING_HUMAN. An unreadable record is an error (fail closed)."""
    jdir = jobs_dir(settings, work.work_key)
    if not jdir.is_dir():
        return []
    found = []
    for path in sorted(jdir.glob("job-*.yaml")):
        record = load_job_record(path)
        if record["branch"] == branch and record["state"] == JobState.WAITING_HUMAN.name:
            found.append(record)
    return found


def remove_worktree_and_branch(repo: Path, jw: JobWorktree) -> None:
    """Remove the worktree (it may already be missing) and delete the branch."""
    try:
        remove_job_worktree(repo, jw, delete_branch=True)
        return
    except GitError:
        pass
    # the worktree directory may be gone or only half removed: prune, then delete the branch
    run_git(repo, "worktree", "prune")
    try:
        run_git(repo, "rev-parse", "--verify", f"refs/heads/{jw.branch}")
    except GitError:
        return
    run_git(repo, "branch", "-D", jw.branch)


def remove_cycle(
    settings: Settings,
    work: WorkInfo,
    chapter_id: str,
    *,
    cancel_reason: str,
    info: CycleInfo | None = None,
) -> None:
    """Remove the job branch and worktree, cancel WAITING_HUMAN Jobs bound to the branch and reject pending proposals.

    FAILED and STOPPED Jobs are kept as history. main is not changed.
    """
    repo = work.path
    if info is None:
        info = cycle_info(settings, work, chapter_id, require_worktree=False)
    waiting = waiting_jobs_on_branch(settings, work, info.branch)
    remove_worktree_and_branch(repo, info.jw)

    jdir = jobs_dir(settings, work.work_key)
    for record in waiting:
        save_job_record(jdir, apply_transition(record, JobEvent.HUMAN_CANCEL, reason=cancel_reason))
    for proposal in find_proposals(settings, work.work_key, chapter_id):
        if proposal["status"] == "pending" and proposal["branch"] == info.branch:
            save_proposal(settings, work.work_key, with_status(proposal, "rejected", reason=cancel_reason))


def discard_cycle(settings: Settings, work: WorkInfo, chapter_id: str, *, force: bool = False) -> None:
    """Discard the chapter's job branch (Human operation). main is not changed.

    A HUMAN_APPROVED branch carries approved setting changes and needs force.
    """
    ensure_main_ready(work.path)
    info = cycle_info(settings, work, chapter_id, require_worktree=False)
    meta = _read_branch_chapter_meta(work.path, info.branch, chapter_id)
    if meta["state"] == ChapterState.HUMAN_APPROVED.name and not force:
        raise ChapterError(
            f"{info.branch} is HUMAN_APPROVED: discarding drops the approved state update; use force to discard it"
        )
    remove_cycle(settings, work, chapter_id, cancel_reason="discarded", info=info)


def _diff_tree(repo: Path, commit: str) -> list[tuple[str, str]]:
    raw = run_git(repo, "diff-tree", "--no-commit-id", "--name-status", "-r", "-z", "--no-renames", commit)
    parts = raw.decode("utf-8", errors="surrogateescape").split("\0")
    if parts and parts[-1] == "":
        parts.pop()
    return [(parts[i], parts[i + 1]) for i in range(0, len(parts) - 1, 2)]


def audit_protected_changes(repo: Path, jw: JobWorktree) -> list[str]:
    """Audit every commit of base_commit..branch for protected path changes (phase2c-design §4.3).

    Returns violations as readable strings; an empty list means the branch is clean.
    """
    violations: list[str] = []
    if run_git(repo, "rev-list", "--merges", f"{jw.base_commit}..{jw.branch}").decode("utf-8").strip():
        violations.append("the job branch contains a merge commit")
    commits = run_git(repo, "rev-list", "--reverse", f"{jw.base_commit}..{jw.branch}").decode("utf-8").split()

    for commit in commits:
        label = commit[:10]
        changes = _diff_tree(repo, commit)
        protected: list[tuple[str, str]] = []
        for status, path in changes:
            if not is_safe_relpath(path):
                violations.append(f"{label}: unsafe path {path!r}")
            elif is_protected(path):
                protected.append((status, path))
        if not protected:
            continue

        message = run_git(repo, "log", "-1", "--format=%B", commit).decode("utf-8")
        trailers = parse_trailers(message)
        approvals = trailers.get("Approval-Id", [])
        paths = [p for _, p in protected]
        if not approvals:
            violations.append(
                f"{label}: protected paths changed without an Approval-Id trailer "
                f"(NovUI-Edit is not accepted on a job branch): {paths}"
            )
            continue

        added = {p for s, p in changes if s == "A"}
        targets: set[str] = set()
        records: set[str] = set()
        for approval_id in approvals:
            try:
                rel = approval_rel(approval_id)
            except ValueError:
                violations.append(f"{label}: invalid Approval-Id trailer {approval_id!r}")
                continue
            if rel not in added:
                violations.append(f"{label}: approval record {rel} was not added in the same commit")
                continue
            try:
                doc = loads_yaml(run_git(repo, "show", f"{commit}:{rel}").decode("utf-8"))
                validate_or_raise(doc, "approval")
            except (SchemaError, GitError, ValueError) as exc:
                violations.append(f"{label}: approval record {rel} is invalid: {exc}")
                continue
            sem = check_approval(doc)
            if sem:
                violations.append(f"{label}: approval record {rel} is invalid: {sem}")
                continue
            if doc["approval_id"] != approval_id:
                violations.append(f"{label}: approval record {rel} has approval_id {doc['approval_id']!r}")
                continue
            if doc["job_id"] not in trailers.get("NovUI-Job", []):
                violations.append(f"{label}: approval record {rel} job_id {doc['job_id']!r} is not a NovUI-Job trailer")
            if is_approval_used(repo, approval_id):
                violations.append(f"{label}: approval {approval_id} already exists on main (used)")
            records.add(rel)
            targets.update(doc["targets"])

        for status, path in protected:
            if path.startswith(_APPROVALS_PREFIX):
                if status != "A" or path not in records:
                    violations.append(f"{label}: {path} ({status}) is not an approval record of this commit")
            elif status == "D":
                violations.append(f"{label}: protected path deleted: {path}")
            elif path not in targets:
                violations.append(f"{label}: {path} is not in the targets of the approval records {sorted(records)}")
    return violations


def _collect_trailers(repo: Path, jw: JobWorktree) -> list[tuple[str, str]]:
    """NovUI-Job values then Approval-Id values of base_commit..branch, oldest first, without duplicates."""
    messages = run_git(repo, "log", "--reverse", "--format=%B%x00", f"{jw.base_commit}..{jw.branch}")
    jobs: list[str] = []
    approvals: list[str] = []
    for message in messages.decode("utf-8").split("\0"):
        trailers = parse_trailers(message)
        for v in trailers.get("NovUI-Job", []):
            if v not in jobs:
                jobs.append(v)
        for v in trailers.get("Approval-Id", []):
            if v not in approvals:
                approvals.append(v)
    return [("NovUI-Job", v) for v in jobs] + [("Approval-Id", v) for v in approvals]


def final_approve(settings: Settings, work: WorkInfo, chapter_id: str) -> str:
    """Final approval: merge the chapter's job branch into main with chapter.yaml FINAL; returns the merge commit.

    All checks run before the merge. A failure leaves main unchanged (merge_job_branch aborts the merge): overlap and
    conflict raise MergeConflict, a commit guard failure raises CommitGuardError. After a successful merge the job
    branch and worktree are removed; if that fails FinalizeError is raised and running final_approve again repeats
    only the cleanup.
    """
    repo = work.path
    ensure_main_ready(repo)

    # a merge that succeeded but whose cleanup failed: repeat the cleanup only
    leftovers = run_git(repo, "for-each-ref", "--format=%(refname:short)", f"refs/heads/ai/{chapter_id}/")
    if leftovers.decode("utf-8").split():
        info_left = cycle_info(settings, work, chapter_id, require_worktree=False)
        main_meta = read_chapter_meta(repo, chapter_id)
        merged = True
        try:
            run_git(repo, "merge-base", "--is-ancestor", info_left.branch, "main")
        except GitError:
            merged = False
        if merged and main_meta is not None and main_meta["state"] == ChapterState.FINAL.name:
            try:
                remove_worktree_and_branch(repo, info_left.jw)
            except GitError as exc:
                raise FinalizeError(f"cleanup of the merged job branch failed: {exc}") from exc
            return head_commit(repo, "main")

    info = cycle_info(settings, work, chapter_id, require_worktree=True)
    branch_meta = _read_branch_chapter_meta(repo, info.branch, chapter_id)
    if branch_meta["state"] != ChapterState.HUMAN_APPROVED.name:
        raise ChapterError(
            f"Chapter {chapter_id!r} must be HUMAN_APPROVED on {info.branch} for final approval, "
            f"got: {branch_meta['state']}"
        )
    main_meta = read_chapter_meta(repo, chapter_id)
    if main_meta is None:
        raise ChapterError(f"chapter.yaml of {chapter_id!r} not found on main")
    review_required = bool(main_meta["review_required"]) or bool(branch_meta["review_required"])
    final_state = next_chapter_state(  # raises InvalidTransition while review_required is true
        ChapterState.HUMAN_APPROVED, ChapterEvent.FINAL_APPROVED, review_required=review_required
    )

    waiting = waiting_jobs_on_branch(settings, work, info.branch)
    if waiting:
        raise ChapterError(
            f"{info.branch} still has Jobs waiting for a Human decision: {[r['job_id'] for r in waiting]}"
        )

    violations = audit_protected_changes(repo, info.jw)
    if violations:
        raise FinalizeError("protected change audit failed:\n" + "\n".join(violations))

    trailers = _collect_trailers(repo, info.jw)
    final_meta = dict(branch_meta)
    final_meta["state"] = final_state.name
    validate_or_raise(final_meta, "chapter")
    sem_errs = check_chapter(final_meta)
    if sem_errs:
        raise SchemaError(f"Semantic validation failed for chapter: {sem_errs}", errors=sem_errs)

    merge_commit = merge_job_branch(
        repo,
        info.jw,
        f"final approve {chapter_id}",
        trailers,
        name=settings.git_name,
        email=settings.git_email,
        extra_writes={f"chapters/{chapter_id}/chapter.yaml": dumps_yaml(final_meta).encode("utf-8")},
    )

    try:
        remove_worktree_and_branch(repo, info.jw)
    except GitError as exc:
        raise FinalizeError(
            f"merged as {merge_commit}, but removing the job branch and worktree failed: {exc}; "
            "run final-approve again to repeat the cleanup"
        ) from exc
    return merge_commit
