"""Podman container management and execution for isolated CLI runs."""

from dataclasses import dataclass
import json
from pathlib import Path
import re
import subprocess
from typing import Sequence

from novui.paths import PathError, ensure_within
from novui.procrun import ProcResult, run_process

_CONTAINER_NAME_RE = re.compile(r"^novui-[a-z0-9-]+$")

MAX_PROMPT_BYTES: int = 120_000


class ContainerError(Exception):
    """Raised when container configuration, execution, or removal fails."""


class PromptTooLarge(ValueError):
    """Raised when prompt UTF-8 byte size exceeds MAX_PROMPT_BYTES."""

    def __init__(self, message: str, *, size: int, limit: int) -> None:
        super().__init__(message)
        self.size = size
        self.limit = limit


@dataclass(frozen=True)
class Mount:
    source: Path
    target: str
    mode: str


def build_agy_mounts(*, job_home: Path, jobhome_root: Path) -> list[Mount]:
    """Build the volume mount for AGY container.

    Returns [Mount(source=job_home, target="/home/agy", mode="rw")].
    Raises ContainerError if job_home is not within jobhome_root,
    is jobhome_root itself, or contains comma or colon.
    """
    try:
        resolved_home = ensure_within(jobhome_root, job_home)
    except (PathError, ValueError) as exc:
        raise ContainerError(f"Invalid job_home path: {exc}") from exc

    if resolved_home == jobhome_root.resolve(strict=False):
        raise ContainerError("job_home cannot be the jobhome_root itself")

    p_str = str(job_home)
    if "," in p_str or ":" in p_str:
        raise ContainerError(f"job_home path contains invalid characters (comma or colon): {p_str}")

    return [Mount(source=job_home, target="/home/agy", mode="rw")]


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
    if "\x00" in prompt:
        raise ValueError("prompt cannot contain NUL bytes")

    prompt_bytes = prompt.encode("utf-8")
    size = len(prompt_bytes)
    if size > MAX_PROMPT_BYTES:
        raise PromptTooLarge(
            f"Prompt size ({size} bytes) exceeds limit ({MAX_PROMPT_BYTES} bytes)",
            size=size,
            limit=MAX_PROMPT_BYTES,
        )

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

    Calls remove_container(name) upon completion or exception.
    If run_process returns normally and remove_container fails, raises ContainerError.
    If run_process raises an exception, calls remove_container and re-raises the original exception.
    """
    args = build_run_args(name=name, image=image, mounts=mounts, command=command)
    stdout_path = log_dir / f"{name}.stdout.log"
    stderr_path = log_dir / f"{name}.stderr.log"

    try:
        res = run_process(
            args,
            cwd=log_dir,
            timeout_seconds=timeout_seconds,
            kill_grace_seconds=kill_grace_seconds,
            stdout_path=stdout_path,
            stderr_path=stderr_path,
        )
    except BaseException:
        try:
            remove_container(name)
        except Exception:
            pass
        raise

    remove_container(name)
    return res


def image_label(image: str, key: str) -> str | None:
    """Inspect and return the value of a specific label on an image.

    Returns None if inspect fails, Labels is null/empty, or key is not found.
    """
    cmd = ["podman", "image", "inspect", "--format", "{{json .Labels}}", image]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        return None
    try:
        labels = json.loads(proc.stdout)
    except (ValueError, TypeError):
        return None
    if not isinstance(labels, dict):
        return None
    val = labels.get(key)
    return str(val) if val is not None else None

