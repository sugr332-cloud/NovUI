"""Work repository git operations, worktrees, commit rules, and merge logic."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Iterable, Mapping, Sequence

from novui.checks import CheckResult
from novui.gitinspect import GitError, run_git
from novui.paths import ensure_within, is_safe_relpath
from novui.protected import is_protected


TRAILER_KEYS: frozenset[str] = frozenset({"NovUI-Job", "NovUI-Edit", "Approval-Id"})

_WORK_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
_CHAPTER_ID_RE = re.compile(r"^ch-[0-9]+$")
_JOB_ID_RE = re.compile(r"^job-[0-9]+$")
_APPROVAL_ID_RE = re.compile(r"^A-[0-9]{4,}$")


class WorkRepoError(Exception):
    """Base exception for work repository git operations."""


class MergeConflict(WorkRepoError):
    """Raised when merge cannot proceed due to overlap or merge conflicts."""

    def __init__(self, message: str, paths: tuple[str, ...] = ()) -> None:
        super().__init__(message)
        self.paths = paths


class CommitGuardError(WorkRepoError):
    """Raised when a commit contains protected path changes without proper trailers."""

    def __init__(self, message: str, paths: tuple[str, ...] = ()) -> None:
        super().__init__(message)
        self.paths = paths


@dataclass(frozen=True)
class JobWorktree:
    branch: str          # ai/<chapter_id>/<job_id>
    path: Path           # <worktree_root>/<work_key>/<job_id>
    base_commit: str     # 作成時の main の commit（40桁）


@dataclass(frozen=True)
class MergePlan:
    kind: str            # "fast_forward" | "no_overlap" | "overlap"
    base_commit: str
    main_head: str
    main_changed: frozenset[str]
    branch_changed: frozenset[str]
    overlap: frozenset[str]


def format_message(subject: str, trailers: Sequence[tuple[str, str]]) -> str:
    """Format commit message with subject and validated trailers."""
    if not subject or "\n" in subject or "\r" in subject:
        raise ValueError(f"Subject must be a non-empty single-line string: {subject!r}")

    lines = [subject]
    if trailers:
        lines.append("")
        for k, v in trailers:
            if k not in TRAILER_KEYS:
                raise ValueError(f"Unknown trailer key: {k!r}, allowed: {sorted(TRAILER_KEYS)}")
            if k == "NovUI-Job":
                if not _JOB_ID_RE.match(v):
                    raise ValueError(f"Invalid NovUI-Job trailer value: {v!r}")
            elif k == "NovUI-Edit":
                if v not in ("human-typo", "human-content"):
                    raise ValueError(f"Invalid NovUI-Edit trailer value: {v!r}")
            elif k == "Approval-Id":
                if not _APPROVAL_ID_RE.match(v):
                    raise ValueError(f"Invalid Approval-Id trailer value: {v!r}")
            lines.append(f"{k}: {v}")

    return "\n".join(lines)


def parse_trailers(message: str) -> dict[str, list[str]]:
    """Parse recognized trailers from the final paragraph of a commit message."""
    paragraphs = [p for p in message.strip().split("\n\n") if p]
    if not paragraphs:
        return {}
    last_paragraph = paragraphs[-1]
    result: dict[str, list[str]] = {}
    for line in last_paragraph.splitlines():
        line = line.strip()
        if ":" in line:
            k, v = line.split(":", 1)
            k = k.strip()
            v = v.strip()
            if k in TRAILER_KEYS:
                result.setdefault(k, []).append(v)
    return result


def check_commit_guard(paths: Iterable[str], trailers: Sequence[tuple[str, str]]) -> CheckResult:
    """Check if protected path changes have required trailers (NovUI-Edit or Approval-Id)."""
    protected_paths = tuple(sorted([p for p in paths if is_protected(p)]))
    has_edit = any(k == "NovUI-Edit" for k, _ in trailers)
    has_approval = any(k == "Approval-Id" for k, _ in trailers)

    if protected_paths and not (has_edit or has_approval):
        return CheckResult(
            name="commit_guard",
            status="FAIL",
            details=protected_paths,
        )
    return CheckResult(name="commit_guard", status="PASS", details=())


def head_commit(repo: Path, ref: str = "main") -> str:
    """Return the 40-character commit hash for ref in repo."""
    out = run_git(repo, "rev-parse", ref)
    return out.decode("utf-8").strip()


def create_job_worktree(
    repo: Path,
    worktree_root: Path,
    work_key: str,
    chapter_id: str,
    job_id: str,
) -> JobWorktree:
    """Create a new job worktree and dedicated branch branched from main."""
    if not _WORK_KEY_RE.match(work_key):
        raise ValueError(f"Invalid work_key: {work_key!r}")
    if not _CHAPTER_ID_RE.match(chapter_id):
        raise ValueError(f"Invalid chapter_id: {chapter_id!r}")
    if not _JOB_ID_RE.match(job_id):
        raise ValueError(f"Invalid job_id: {job_id!r}")

    cur_branch = run_git(repo, "branch", "--show-current").decode("utf-8").strip()
    if cur_branch != "main":
        raise WorkRepoError(f"Main repository must be on 'main' branch, got: {cur_branch!r}")

    branch = f"ai/{chapter_id}/{job_id}"
    worktree_path = worktree_root / work_key / job_id

    if worktree_path.exists():
        raise WorkRepoError(f"Worktree path already exists: {worktree_path}")

    # Check if branch exists
    try:
        run_git(repo, "rev-parse", "--verify", f"refs/heads/{branch}")
        branch_exists = True
    except GitError:
        branch_exists = False

    if branch_exists:
        raise WorkRepoError(f"Branch already exists: {branch}")

    base_commit = head_commit(repo, "main")
    worktree_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        run_git(repo, "worktree", "add", "-b", branch, str(worktree_path), "main")
    except GitError as e:
        raise WorkRepoError(f"Failed to create worktree: {e}") from e

    return JobWorktree(branch=branch, path=worktree_path, base_commit=base_commit)


def remove_job_worktree(repo: Path, jw: JobWorktree, *, delete_branch: bool) -> None:
    """Remove a job worktree and optionally delete its branch."""
    run_git(repo, "worktree", "remove", "--force", str(jw.path))
    if delete_branch:
        run_git(repo, "branch", "-D", jw.branch)


def chapter_branches(repo: Path, chapter_id: str) -> list[str]:
    """List existing branches for chapter_id (refs/heads/ai/<chapter_id>/) sorted ascending."""
    out = run_git(repo, "for-each-ref", "--format=%(refname:short)", f"refs/heads/ai/{chapter_id}/")
    lines = [line.strip() for line in out.decode("utf-8").splitlines() if line.strip()]
    return sorted(lines)


def commit_all(
    worktree: Path,
    subject: str,
    trailers: Sequence[tuple[str, str]],
    *,
    name: str,
    email: str,
) -> str | None:
    """Stage all changes, check commit guard, and commit. Returns new commit hash or None if clean."""
    run_git(worktree, "add", "-A")

    diff_bytes = run_git(worktree, "diff", "--cached", "--name-only", "-z", "--no-renames")
    changed = [p for p in diff_bytes.decode("utf-8").split("\0") if p]

    if not changed:
        return None

    guard = check_commit_guard(changed, trailers)
    if guard.status == "FAIL":
        run_git(worktree, "reset", "-q")
        raise CommitGuardError(f"Commit guard failed for paths: {guard.details}", paths=guard.details)

    msg = format_message(subject, trailers)
    run_git(worktree, "-c", f"user.name={name}", "-c", f"user.email={email}", "commit", "-m", msg)
    return head_commit(worktree, "HEAD")


def changed_paths(repo: Path, a: str, b: str) -> frozenset[str]:
    """Return set of file paths changed between commit a and commit b."""
    out = run_git(repo, "diff", "--name-only", "-z", "--no-renames", a, b)
    return frozenset(p for p in out.decode("utf-8").split("\0") if p)


def plan_merge(repo: Path, jw: JobWorktree) -> MergePlan:
    """Plan merge from job branch into main, checking fast_forward and overlaps."""
    main_head = head_commit(repo, "main")
    branch_changed = changed_paths(repo, jw.base_commit, jw.branch)

    if main_head == jw.base_commit:
        return MergePlan(
            kind="fast_forward",
            base_commit=jw.base_commit,
            main_head=main_head,
            main_changed=frozenset(),
            branch_changed=branch_changed,
            overlap=frozenset(),
        )

    main_changed = changed_paths(repo, jw.base_commit, main_head)
    overlap = main_changed & branch_changed
    kind = "overlap" if overlap else "no_overlap"
    return MergePlan(
        kind=kind,
        base_commit=jw.base_commit,
        main_head=main_head,
        main_changed=main_changed,
        branch_changed=branch_changed,
        overlap=overlap,
    )


def merge_job_branch(
    repo: Path,
    jw: JobWorktree,
    subject: str,
    trailers: Sequence[tuple[str, str]],
    *,
    name: str,
    email: str,
    extra_writes: Mapping[str, bytes] | None = None,
) -> str:
    """Merge job branch into main, apply extra_writes, enforce commit guard, and commit."""
    cur_branch = run_git(repo, "branch", "--show-current").decode("utf-8").strip()
    if cur_branch != "main":
        raise WorkRepoError(f"Main repository must be on 'main' branch, got: {cur_branch!r}")

    status_out = run_git(repo, "status", "--porcelain", "--untracked-files=all").decode("utf-8").strip()
    if status_out:
        raise WorkRepoError(f"Working tree is not clean:\n{status_out}")

    plan = plan_merge(repo, jw)
    if plan.kind == "overlap":
        sorted_overlap = tuple(sorted(plan.overlap))
        raise MergeConflict(f"Automatic merge disallowed due to overlapping changes: {sorted_overlap}", paths=sorted_overlap)

    merge_in_progress = False
    try:
        try:
            run_git(repo, "merge", "--no-ff", "--no-commit", jw.branch)
            merge_in_progress = True
        except GitError as e:
            # Merge conflict
            try:
                unmerged_out = run_git(repo, "diff", "--name-only", "--diff-filter=U").decode("utf-8")
                unmerged_paths = tuple(sorted(p for p in unmerged_out.splitlines() if p.strip()))
            except Exception:
                unmerged_paths = ()
            try:
                run_git(repo, "merge", "--abort")
            except Exception:
                pass
            raise MergeConflict(f"Merge conflict encountered: {unmerged_paths}", paths=unmerged_paths) from e

        if extra_writes:
            for rel_path, content in extra_writes.items():
                if not is_safe_relpath(rel_path):
                    raise ValueError(f"Invalid extra write path: {rel_path!r}")
                target_path = ensure_within(repo, repo / rel_path)
                target_path.parent.mkdir(parents=True, exist_ok=True)
                target_path.write_bytes(content)
                run_git(repo, "add", rel_path)

        cached_out = run_git(repo, "diff", "--cached", "--name-only", "-z", "--no-renames", "HEAD")
        all_changed = [p for p in cached_out.decode("utf-8").split("\0") if p]

        guard = check_commit_guard(all_changed, trailers)
        if guard.status == "FAIL":
            run_git(repo, "merge", "--abort")
            merge_in_progress = False
            raise CommitGuardError(f"Commit guard failed during merge: {guard.details}", paths=guard.details)

        msg = format_message(subject, trailers)
        run_git(repo, "-c", f"user.name={name}", "-c", f"user.email={email}", "commit", "-m", msg)
        merge_in_progress = False
        return head_commit(repo, "main")

    except Exception:
        if merge_in_progress:
            try:
                run_git(repo, "merge", "--abort")
            except Exception:
                pass
        raise
