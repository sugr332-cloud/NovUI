"""Follow main branch for job branches (3-B3, phase3b-design §4).

Rebases an active chapter job branch onto main when main has advanced and the changes
do not overlap with the job branch changes.
"""

from __future__ import annotations

from typing import Sequence

from novui.chapters import ChapterError
from novui.config import Settings
from novui.finalize import cycle_info
from novui.gitinspect import GitError, get_changes, run_git
from novui.jobrecord import load_job_record
from novui.jobrunner import jobs_dir
from novui.proposals import pending_proposal
from novui.states import JobState
from novui.validatejob import find_chapter_branch
from novui.workrepo import head_commit
from novui.works import WorkInfo


class FollowConflict(ChapterError):
    """Raised when following main would conflict with changes on the job branch."""

    def __init__(self, paths: Sequence[str]) -> None:
        super().__init__(f"main changed paths that the job branch also changed: {', '.join(paths)}")
        self.paths = list(paths)


_ACTIVE_STATES = frozenset({
    JobState.RUNNING.name,
    JobState.CHECKING.name,
    JobState.VALIDATING.name,
    JobState.WAITING_HUMAN.name,
})


def follow_main(
    settings: Settings,
    work: WorkInfo,
    chapter_id: str,
    *,
    allow_pending: bool = False,
) -> str | None:
    """Rebase the chapter's job branch onto main if main has advanced.

    Returns the new branch HEAD if rebased, or None if no rebase was needed.
    Raises FollowConflict if changed paths overlap with main's changes.
    Raises ChapterError on active jobs, pending proposals, or rebase failures.
    """
    repo = work.path
    branch = find_chapter_branch(repo, chapter_id)
    mb = run_git(repo, "merge-base", "main", branch).decode("utf-8").strip()
    main_head = head_commit(repo, "main")

    # 1. If main hasn't advanced beyond merge-base, nothing to rebase
    if main_head == mb:
        return None

    info = cycle_info(settings, work, chapter_id, require_worktree=True)

    # Check pending state update proposal
    pending = pending_proposal(settings, work.work_key, chapter_id)
    if pending is not None and not allow_pending:
        raise ChapterError(f"chapter {chapter_id} has a pending state update proposal")
    pending_job_id = pending.get("job_id") if pending and allow_pending else None

    # 2. Check active jobs bound to the branch
    jdir = jobs_dir(settings, work.work_key)
    if jdir.is_dir():
        for p in sorted(jdir.glob("job-*.yaml")):
            try:
                rec = load_job_record(p)
            except ChapterError:
                raise
            except Exception as exc:
                raise ChapterError(f"job record {p} could not be read: {exc}") from exc

            if rec.get("job_id") == pending_job_id:
                continue
            if rec.get("branch") == info.branch and rec.get("state") in _ACTIVE_STATES:
                raise ChapterError(
                    f"job {rec.get('job_id')} on branch {info.branch} is in progress ({rec.get('state')})"
                )

    # 3. Path overlap check
    branch_paths = set(
        p for p in run_git(repo, "diff", "--name-only", mb, info.branch).decode("utf-8").splitlines() if p
    )
    main_paths = set(
        p for p in run_git(repo, "diff", "--name-only", mb, "main").decode("utf-8").splitlines() if p
    )
    overlap = sorted(branch_paths & main_paths)
    if overlap:
        raise FollowConflict(overlap)

    # 4. Rebase onto main
    wt = info.jw.path
    try:
        run_git(
            wt,
            "-c", f"user.name={settings.git_name}",
            "-c", f"user.email={settings.git_email}",
            "rebase", "main",
        )
    except GitError as exc:
        try:
            run_git(wt, "rebase", "--abort")
        except Exception:
            pass
        raise ChapterError(f"git rebase main failed on {info.branch}: {exc}") from exc

    if get_changes(wt):
        raise ChapterError(f"worktree {wt} has uncommitted changes after rebase")

    try:
        run_git(wt, "merge-base", "--is-ancestor", main_head, "HEAD")
    except GitError:
        raise ChapterError(f"main HEAD {main_head} is not an ancestor of rebased {info.branch}")

    # 5. Return new HEAD
    return head_commit(wt, "HEAD")
