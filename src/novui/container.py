"""Podman container management and execution for isolated CLI runs."""

from dataclasses import dataclass
from pathlib import Path
import re
import subprocess
from typing import Sequence

from novui.paths import PathError, ensure_within
from novui.procrun import ProcResult, run_process

PROTECTED_PATHS: tuple[str, ...] = (
    "project.yaml",
    "chapters-order.yaml",
    "world",
    "characters",
    "plot",
    "foreshadowing/registry.yaml",
    "rules",
    ".novui",
)

_CONTAINER_NAME_RE = re.compile(r"^novui-[a-z0-9-]+$")


class ContainerError(Exception):
    """Raised when container configuration, execution, or removal fails."""


@dataclass(frozen=True)
class Mount:
    source: Path
    target: str
    mode: str


def build_mounts(
    *,
    worktree: Path,
    job_home: Path,
    worktree_root: Path,
    jobhome_root: Path,
) -> list[Mount]:
    """Build the ordered list of volume mounts for the container.

    1. worktree -> /workspace (rw)
    2. Existing protected paths in worktree -> /workspace/<path> (ro)
    3. worktree/.git -> /workspace/.git (ro)
    4. job_home -> /home/agy (rw)
    """
    try:
        resolved_wt = ensure_within(worktree_root, worktree)
    except (PathError, ValueError) as exc:
        raise ContainerError(f"Invalid worktree path: {exc}") from exc

    if resolved_wt == worktree_root.resolve(strict=False):
        raise ContainerError("worktree cannot be the worktree_root itself")

    try:
        resolved_home = ensure_within(jobhome_root, job_home)
    except (PathError, ValueError) as exc:
        raise ContainerError(f"Invalid job_home path: {exc}") from exc

    if resolved_home == jobhome_root.resolve(strict=False):
        raise ContainerError("job_home cannot be the jobhome_root itself")

    dot_git = worktree / ".git"
    if not dot_git.is_file():
        raise ContainerError(f"worktree .git must be a file (linked worktree): {dot_git}")

    # Check for invalid characters in source paths
    for p in (worktree, job_home, dot_git):
        p_str = str(p)
        if "," in p_str or ":" in p_str:
            raise ContainerError(f"Source path contains invalid characters (comma or colon): {p_str}")

    mounts: list[Mount] = [Mount(source=worktree, target="/workspace", mode="rw")]

    for rel in PROTECTED_PATHS:
        candidate = worktree / rel
        if candidate.exists():
            c_str = str(candidate)
            if "," in c_str or ":" in c_str:
                raise ContainerError(f"Source path contains invalid characters (comma or colon): {c_str}")
            mounts.append(Mount(source=candidate, target=f"/workspace/{rel}", mode="ro"))

    mounts.append(Mount(source=dot_git, target="/workspace/.git", mode="ro"))
    mounts.append(Mount(source=job_home, target="/home/agy", mode="rw"))

    return mounts


def mount_arg(m: Mount) -> str:
    """Format mount specification for podman -v argument."""
    return f"{m.source}:{m.target}:{m.mode},Z"


def build_run_args(
    *,
    name: str,
    image: str,
    mounts: Sequence[Mount],
    command: Sequence[str],
) -> list[str]:
    """Assemble podman run command arguments with full isolation options."""
    if not _CONTAINER_NAME_RE.match(name):
        raise ValueError(f"Container name must match ^novui-[a-z0-9-]+$, got: {name!r}")

    args = [
        "podman",
        "run",
        "--rm",
        "--name",
        name,
        "--pull=never",
        "--userns=keep-id",
        "--cap-drop=all",
        "--security-opt=no-new-privileges",
        "-e",
        "HOME=/home/agy",
        "-w",
        "/workspace",
    ]

    for m in mounts:
        args.extend(["-v", mount_arg(m)])

    args.append(image)
    args.extend(command)
    return args


def agy_command(*, model: str, prompt: str) -> list[str]:
    """Build agy CLI command arguments."""
    if not model or not model.strip():
        raise ValueError("model cannot be empty")
    if not prompt or not prompt.strip():
        raise ValueError("prompt cannot be empty")

    return ["agy", "--mode", "accept-edits", "--model", model, f"--print={prompt}"]


def remove_container(name: str) -> None:
    """Force remove container with podman rm -f --ignore."""
    cmd = ["podman", "rm", "-f", "--ignore", "--time", "0", name]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise ContainerError(f"Failed to remove container {name}: {proc.stderr}")


def image_id(image: str) -> str:
    """Inspect and return the image ID for image."""
    cmd = ["podman", "image", "inspect", "--format", "{{.Id}}", image]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise ContainerError(f"Failed to inspect image {image}: {proc.stderr}")
    return proc.stdout.strip()


def run_container(
    *,
    name: str,
    image: str,
    mounts: Sequence[Mount],
    command: Sequence[str],
    timeout_seconds: float,
    log_dir: Path,
    kill_grace_seconds: float = 10.0,
) -> ProcResult:
    """Run containerized command via procrun.run_process and ensure cleanup.

    Always calls remove_container(name) upon completion or exception.
    """
    args = build_run_args(name=name, image=image, mounts=mounts, command=command)
    stdout_path = log_dir / f"{name}.stdout.log"
    stderr_path = log_dir / f"{name}.stderr.log"

    try:
        return run_process(
            args,
            cwd=log_dir,
            timeout_seconds=timeout_seconds,
            kill_grace_seconds=kill_grace_seconds,
            stdout_path=stdout_path,
            stderr_path=stderr_path,
        )
    finally:
        try:
            remove_container(name)
        except Exception:
            pass
