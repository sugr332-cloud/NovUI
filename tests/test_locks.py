"""Tests for novui.locks module."""

import concurrent.futures
from pathlib import Path
import pytest
import subprocess

from novui.locks import LockError, RunLock, can_accept_write_job


def test_run_lock_acquire_and_release() -> None:
    lock = RunLock()
    assert lock.holder("novel-a") is None

    # Acquire lock
    assert lock.acquire("novel-a", "job-1") is True
    assert lock.holder("novel-a") == "job-1"

    # Same job re-acquires successfully
    assert lock.acquire("novel-a", "job-1") is True

    # Other job fails to acquire
    assert lock.acquire("novel-a", "job-2") is False
    assert lock.holder("novel-a") == "job-1"

    # Non-holder cannot release
    with pytest.raises(LockError):
        lock.release("novel-a", "job-2")

    # Holder releases
    lock.release("novel-a", "job-1")
    assert lock.holder("novel-a") is None

    # Releasing when unlocked raises LockError
    with pytest.raises(LockError):
        lock.release("novel-a", "job-1")

    # Another job can now acquire
    assert lock.acquire("novel-a", "job-2") is True
    assert lock.holder("novel-a") == "job-2"


def test_run_lock_different_works_independent() -> None:
    lock = RunLock()
    assert lock.acquire("novel-a", "job-1") is True
    assert lock.acquire("novel-b", "job-2") is True
    assert lock.holder("novel-a") == "job-1"
    assert lock.holder("novel-b") == "job-2"

    lock.release("novel-a", "job-1")
    assert lock.holder("novel-a") is None
    assert lock.holder("novel-b") == "job-2"


def test_run_lock_concurrent_acquire() -> None:
    lock = RunLock()
    work_key = "novel-concurrent"
    results: list[bool] = []

    def try_acquire(job_id: str) -> bool:
        return lock.acquire(work_key, job_id)

    num_threads = 10
    with concurrent.futures.ThreadPoolExecutor(max_workers=num_threads) as executor:
        futures = [executor.submit(try_acquire, f"job-{i}") for i in range(num_threads)]
        for f in concurrent.futures.as_completed(futures):
            results.append(f.result())

    # Exactly one thread succeeds
    assert results.count(True) == 1
    assert results.count(False) == num_threads - 1


def test_can_accept_write_job(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init", "-b", "main"], check=True, capture_output=True)
    (repo / "file.txt").write_text("initial", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=test", "-c", "user.email=test@example.invalid", "commit", "-m", "initial"],
        check=True,
        capture_output=True,
    )

    # No branch for ch-001 -> True
    assert can_accept_write_job(repo, "ch-001") is True

    # Create ai/ch-001/job-1 branch -> False
    subprocess.run(["git", "-C", str(repo), "branch", "ai/ch-001/job-1"], check=True, capture_output=True)
    assert can_accept_write_job(repo, "ch-001") is False

    # ch-002 is still unaffected -> True
    assert can_accept_write_job(repo, "ch-002") is True

    # Delete ai/ch-001/job-1 branch -> True again
    subprocess.run(["git", "-C", str(repo), "branch", "-D", "ai/ch-001/job-1"], check=True, capture_output=True)
    assert can_accept_write_job(repo, "ch-001") is True
