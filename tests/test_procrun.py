"""Tests for novui.procrun module."""

import os
from pathlib import Path
import time
import pytest

from novui.procrun import run_process


def test_normal_completion(tmp_path: Path) -> None:
    out = tmp_path / "out.log"
    err = tmp_path / "err.log"

    res = run_process(
        ["sh", "-c", "printf 'hello'; printf 'warning' >&2"],
        cwd=tmp_path,
        timeout_seconds=5.0,
        stdout_path=out,
        stderr_path=err,
    )
    assert res.exit_code == 0
    assert res.stdout == b"hello"
    assert res.stderr == b"warning"
    assert out.read_bytes() == b"hello"
    assert err.read_bytes() == b"warning"
    assert res.timed_out is False
    assert res.signal_sent is None
    assert res.group_remaining is False
    assert res.elapsed_seconds > 0


def test_non_zero_exit_code(tmp_path: Path) -> None:
    out = tmp_path / "out.log"
    err = tmp_path / "err.log"

    res = run_process(
        ["sh", "-c", "exit 3"],
        cwd=tmp_path,
        timeout_seconds=5.0,
        stdout_path=out,
        stderr_path=err,
    )
    assert res.exit_code == 3
    assert res.timed_out is False
    assert res.signal_sent is None


def test_stdin_data_passed_to_child(tmp_path: Path) -> None:
    out = tmp_path / "out.log"
    err = tmp_path / "err.log"

    res = run_process(
        ["sh", "-c", "cat"],
        cwd=tmp_path,
        stdin_data=b"input content\n",
        timeout_seconds=5.0,
        stdout_path=out,
        stderr_path=err,
    )
    assert res.exit_code == 0
    assert res.stdout == b"input content\n"


def test_timeout_sigterm(tmp_path: Path) -> None:
    out = tmp_path / "out.log"
    err = tmp_path / "err.log"

    res = run_process(
        ["sleep", "30"],
        cwd=tmp_path,
        timeout_seconds=0.5,
        kill_grace_seconds=2.0,
        stdout_path=out,
        stderr_path=err,
    )
    assert res.timed_out is True
    assert res.signal_sent == "SIGTERM"
    # SIGTERM により終了した場合 exit_code は負の値 (-15)
    assert res.exit_code is not None and res.exit_code < 0


def test_sigterm_ignored_fallback_to_sigkill(tmp_path: Path) -> None:
    out = tmp_path / "out.log"
    err = tmp_path / "err.log"

    # SIGTERM を trap して無視し sleep を継続
    res = run_process(
        ["sh", "-c", "trap '' TERM; sleep 30"],
        cwd=tmp_path,
        timeout_seconds=0.5,
        kill_grace_seconds=0.5,
        stdout_path=out,
        stderr_path=err,
    )
    assert res.timed_out is True
    assert res.signal_sent == "SIGKILL"
    assert res.exit_code is not None and res.exit_code < 0


def test_grandchildren_cleaned_up(tmp_path: Path) -> None:
    out = tmp_path / "out.log"
    err = tmp_path / "err.log"

    # バックグラウンドで sleep を起動し、その PID を出力してシェルは即終了
    res = run_process(
        ["sh", "-c", "sleep 30 & echo $!"],
        cwd=tmp_path,
        timeout_seconds=5.0,
        stdout_path=out,
        stderr_path=err,
    )
    assert res.exit_code == 0
    assert res.timed_out is False
    assert res.group_remaining is True

    child_pid_str = res.stdout.strip().decode()
    assert child_pid_str.isdigit()
    child_pid = int(child_pid_str)

    # sleep プロセスがプロセスグループ後始末によって確実に kill されていることを確認
    time.sleep(0.1)
    with pytest.raises(ProcessLookupError):
        os.kill(child_pid, 0)


def test_invalid_arguments_raise_value_error(tmp_path: Path) -> None:
    out = tmp_path / "out.log"
    err = tmp_path / "err.log"

    # 空の argv
    with pytest.raises(ValueError):
        run_process([], cwd=tmp_path, timeout_seconds=1.0, stdout_path=out, stderr_path=err)

    # timeout <= 0
    with pytest.raises(ValueError):
        run_process(["echo"], cwd=tmp_path, timeout_seconds=0, stdout_path=out, stderr_path=err)

    with pytest.raises(ValueError):
        run_process(["echo"], cwd=tmp_path, timeout_seconds=-1.0, stdout_path=out, stderr_path=err)

    # 相対 cwd
    with pytest.raises(ValueError):
        run_process(["echo"], cwd=Path("relative/path"), timeout_seconds=1.0, stdout_path=out, stderr_path=err)
