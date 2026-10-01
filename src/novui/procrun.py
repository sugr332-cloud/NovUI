"""Subprocess execution with process groups, timeouts, and signal handling."""

from dataclasses import dataclass
import os
from pathlib import Path
import signal
import subprocess
import time
from typing import Mapping, Sequence


@dataclass(frozen=True)
class ProcResult:
    exit_code: int | None
    elapsed_seconds: float
    timed_out: bool
    signal_sent: str | None
    group_remaining: bool
    stdout: bytes
    stderr: bytes


def run_process(
    argv: Sequence[str],
    *,
    cwd: Path,
    env: Mapping[str, str] | None = None,
    stdin_data: bytes | None = None,
    timeout_seconds: float,
    kill_grace_seconds: float = 10.0,
    stdout_path: Path,
    stderr_path: Path,
) -> ProcResult:
    """Run an external command in a new process group with strict timeouts and group cleanup.

    Directs stdout and stderr to specified files and reads them back.
    Raises ValueError if argv is empty, timeout_seconds <= 0, or cwd is not absolute.
    Guarantees SIGKILL is sent to the process group on timeout or any unhandled exception.
    """
    if not argv:
        raise ValueError("argv cannot be empty")
    if timeout_seconds <= 0:
        raise ValueError(f"timeout_seconds must be > 0, got {timeout_seconds}")
    if not cwd.is_absolute():
        raise ValueError(f"cwd must be an absolute path, got {cwd}")

    stdout_path.parent.mkdir(parents=True, exist_ok=True)
    stderr_path.parent.mkdir(parents=True, exist_ok=True)

    start_time = time.monotonic()
    timed_out = False
    signal_sent: str | None = None
    group_remaining = False

    with open(stdout_path, "wb") as out_f, open(stderr_path, "wb") as err_f:
        stdin_setting = subprocess.PIPE if stdin_data is not None else subprocess.DEVNULL
        proc = subprocess.Popen(
            argv,
            cwd=str(cwd),
            env=dict(env) if env is not None else None,
            start_new_session=True,
            stdin=stdin_setting,
            stdout=out_f,
            stderr=err_f,
        )

        pgid = proc.pid

        try:
            if stdin_data is not None and proc.stdin is not None:
                try:
                    proc.stdin.write(stdin_data)
                    proc.stdin.flush()
                except (BrokenPipeError, OSError):
                    pass
                finally:
                    proc.stdin.close()

            try:
                proc.wait(timeout=timeout_seconds)
            except subprocess.TimeoutExpired:
                timed_out = True
                signal_sent = "SIGTERM"
                try:
                    os.killpg(pgid, signal.SIGTERM)
                except ProcessLookupError:
                    pass

                try:
                    proc.wait(timeout=kill_grace_seconds)
                except subprocess.TimeoutExpired:
                    signal_sent = "SIGKILL"
                    try:
                        os.killpg(pgid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    try:
                        proc.wait(timeout=5.0)
                    except subprocess.TimeoutExpired:
                        pass

        except BaseException:
            # Guarantee cleanup on any exception including KeyboardInterrupt
            try:
                os.killpg(pgid, signal.SIGKILL)
            except Exception:
                pass
            try:
                proc.wait(timeout=2.0)
            except Exception:
                pass
            raise

    elapsed = time.monotonic() - start_time

    # Check if any remaining processes in the group survived
    try:
        os.killpg(pgid, 0)
        # Process group still exists (e.g. background grandchildren)
        group_remaining = True
        try:
            os.killpg(pgid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    except ProcessLookupError:
        group_remaining = False
    except PermissionError:
        group_remaining = True

    stdout = stdout_path.read_bytes() if stdout_path.is_file() else b""
    stderr = stderr_path.read_bytes() if stderr_path.is_file() else b""

    return ProcResult(
        exit_code=proc.returncode,
        elapsed_seconds=elapsed,
        timed_out=timed_out,
        signal_sent=signal_sent,
        group_remaining=group_remaining,
        stdout=stdout,
        stderr=stderr,
    )
