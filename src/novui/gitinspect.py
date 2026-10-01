"""Git inspection and protection helpers."""

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import subprocess
from typing import Mapping


class GitError(Exception):
    """Raised when a git command fails or returns unexpected data."""


@dataclass(frozen=True)
class PathChange:
    status: str
    path: str
    orig_path: str | None = None


@dataclass(frozen=True)
class GitDirs:
    git_dir: Path
    common_dir: Path


def run_git(repo: Path, *args: str) -> bytes:
    """Run a git command in the context of repo.

    Executes git -C <repo_absolute_path> with GIT_OPTIONAL_LOCKS=0 and LC_ALL=C.
    Raises ValueError if repo is not absolute.
    Raises GitError if exit code is non-zero.
    """
    if not repo.is_absolute():
        raise ValueError(f"Repository path must be absolute: {repo}")

    env = os.environ.copy()
    env["GIT_OPTIONAL_LOCKS"] = "0"
    env["LC_ALL"] = "C"

    cmd = ["git", "-C", str(repo), *args]
    proc = subprocess.run(
        cmd,
        capture_output=True,
        env=env,
        shell=False,
    )
    if proc.returncode != 0:
        stderr_msg = proc.stderr.decode("utf-8", errors="replace")
        raise GitError(f"Git command {cmd} failed (exit {proc.returncode}): {stderr_msg}")
    return proc.stdout


def parse_porcelain_z(data: bytes) -> list[PathChange]:
    """Parse output of git status --porcelain=v1 -z.

    Each entry is formatted as XY<space><path>\\0.
    For renames/copies (X or Y is 'R' or 'C'), the original path follows as the next \\0 field.
    Raises ValueError if data is malformed.
    """
    if not data:
        return []

    raw_parts = data.split(b"\0")
    if raw_parts and raw_parts[-1] == b"":
        raw_parts.pop()

    changes: list[PathChange] = []
    idx = 0
    while idx < len(raw_parts):
        chunk = raw_parts[idx]
        if len(chunk) < 4:
            raise ValueError(f"Malformed porcelain-z entry: {chunk!r}")
        try:
            status = chunk[:2].decode("ascii", errors="strict")
        except UnicodeDecodeError as exc:
            raise ValueError(f"Invalid status characters in entry: {chunk!r}") from exc

        if chunk[2:3] != b" ":
            raise ValueError(f"Expected space separator at byte 2: {chunk!r}")

        path = chunk[3:].decode("utf-8", errors="surrogateescape")
        orig_path: str | None = None

        if status[0] in ("R", "C") or status[1] in ("R", "C"):
            idx += 1
            if idx >= len(raw_parts):
                raise ValueError("Missing original path for rename/copy in porcelain-z output")
            orig_path = raw_parts[idx].decode("utf-8", errors="surrogateescape")

        changes.append(PathChange(status=status, path=path, orig_path=orig_path))
        idx += 1

    return changes


def get_changes(worktree: Path) -> list[PathChange]:
    """Get working tree changes excluding ignored files (status '!!')."""
    data = run_git(worktree, "status", "--porcelain=v1", "-z", "--untracked-files=all")
    return [c for c in parse_porcelain_z(data) if c.status != "!!"]


def get_ignored(worktree: Path) -> set[str]:
    """Get all ignored files using git ls-files.

    Returns individual file paths instead of collapsed directory names.
    """
    data = run_git(worktree, "ls-files", "-z", "--others", "--ignored", "--exclude-standard")
    if not data:
        return set()
    parts = data.split(b"\0")
    if parts and parts[-1] == b"":
        parts.pop()
    return {p.decode("utf-8", errors="surrogateescape") for p in parts if p}


def resolve_git_dirs(worktree: Path) -> GitDirs:
    """Resolve absolute paths for .git directory and common directory."""
    raw = run_git(worktree, "rev-parse", "--path-format=absolute", "--git-dir", "--git-common-dir")
    lines = raw.decode("utf-8", errors="replace").splitlines()
    if len(lines) < 2:
        raise GitError(f"Expected 2 lines from rev-parse, got: {lines}")
    git_dir = Path(lines[0])
    common_dir = Path(lines[1])
    if not git_dir.is_absolute() or not common_dir.is_absolute():
        raise GitError(f"Resolved git dirs must be absolute: git_dir={git_dir}, common_dir={common_dir}")
    return GitDirs(git_dir=git_dir, common_dir=common_dir)


def git_protection_targets(worktree: Path) -> dict[str, Path]:
    """List protection targets for a linked worktree."""
    dot_git = worktree / ".git"
    if not dot_git.is_file():
        raise ValueError(f"Worktree .git is not a file (not a linked worktree): {dot_git}")

    dirs = resolve_git_dirs(worktree)
    targets: dict[str, Path] = {
        "worktree:.git": dot_git.resolve(strict=False),
        "common:config": (dirs.common_dir / "config").resolve(strict=False),
    }

    hooks_dir = dirs.common_dir / "hooks"
    if hooks_dir.is_dir():
        for p in sorted(hooks_dir.rglob("*")):
            if p.is_file():
                rel = p.relative_to(hooks_dir).as_posix()
                targets[f"common:hooks/{rel}"] = p.resolve(strict=False)

    targets["gitdir:gitdir"] = (dirs.git_dir / "gitdir").resolve(strict=False)
    targets["gitdir:commondir"] = (dirs.git_dir / "commondir").resolve(strict=False)
    targets["gitdir:HEAD"] = (dirs.git_dir / "HEAD").resolve(strict=False)

    return targets


def hash_targets(targets: Mapping[str, Path]) -> dict[str, str]:
    """Calculate SHA-256 for all target paths without invoking git."""
    res: dict[str, str] = {}
    for label, p in targets.items():
        if not p.is_file():
            res[label] = "MISSING"
        else:
            h = hashlib.sha256(p.read_bytes()).hexdigest().lower()
            res[label] = f"sha256:{h}"
    return res
