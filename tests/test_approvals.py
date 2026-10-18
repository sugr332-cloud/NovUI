"""Tests for novui.approvals (numbering, records, applicability, use checks)."""

import copy
import subprocess
import threading
from pathlib import Path

import pytest

from novui.approvals import (
    APPROVALS_DIR,
    ApprovalError,
    approval_rel,
    build_approval,
    check_approval_applicable,
    is_approval_used,
    reserve_approval_id,
)
from novui.config import Settings
from novui.schema import SchemaError, validate_or_raise
from novui.semantics import check_approval
from novui.works import WorkInfo

BASE_HASH = "sha256:" + "0" * 64


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        data_dir=tmp_path / "data",
        worktree_root=tmp_path / "data" / "worktrees",
        jobhome_root=tmp_path / "data" / "jobhomes",
        agy_image="localhost/novui-agy:test",
        agy_token_path=tmp_path / "token",
        timeouts={"agy_draft": 10, "agy_range_edit": 10, "claude": 10},
    )


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def _repo(tmp_path: Path) -> WorkInfo:
    repo = tmp_path / "work"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    (repo / "README.md").write_text("x\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "init")
    return WorkInfo(work_key="demo", path=repo, format_version=1, state="active")


def _commit_approval_on_main(work: WorkInfo, number: int) -> None:
    rel = approval_rel(f"A-{number:04d}")
    p = work.path / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("approval_id: x\n", encoding="utf-8")
    _git(work.path, "add", "-A")
    _git(work.path, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", f"approval {number}")


def _patch(target: str, op_value: str = "C001") -> dict:
    return {
        "type": "state_patch",
        "target": target,
        "base_hash": BASE_HASH,
        "operations": [{"op": "test", "path": "/id", "value": op_value}],
        "reason": "理由",
    }


def test_approval_rel() -> None:
    assert approval_rel("A-0001") == f"{APPROVALS_DIR}/A-0001.yaml"
    assert approval_rel("A-12345") == ".novui/approvals/A-12345.yaml"
    for bad in ("A-1", "B-0001", "A-0001/../x", "", "A-00a1"):
        with pytest.raises(ValueError):
            approval_rel(bad)


def test_reserve_sequential_and_never_reused(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    work = _repo(tmp_path)
    assert reserve_approval_id(settings, work) == "A-0001"
    assert reserve_approval_id(settings, work) == "A-0002"
    assert reserve_approval_id(settings, work) == "A-0003"
    counter = settings.data_dir / "works" / "demo" / "approvals.next"
    assert counter.read_text(encoding="utf-8").strip() == "4"


def test_reserve_skips_numbers_on_main_and_extra_dirs(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    work = _repo(tmp_path)
    _commit_approval_on_main(work, 5)
    assert reserve_approval_id(settings, work) == "A-0006"

    extra = tmp_path / "wt" / ".novui" / "approvals"
    extra.mkdir(parents=True)
    (extra / "A-0009.yaml").write_text("x\n", encoding="utf-8")
    (extra / "notes.txt").write_text("x\n", encoding="utf-8")
    assert reserve_approval_id(settings, work, extra_dirs=[extra]) == "A-0010"
    # missing extra dir is fine
    assert reserve_approval_id(settings, work, extra_dirs=[tmp_path / "nope"]) == "A-0011"


def test_reserve_ignores_uncommitted_files_on_main_checkout(tmp_path: Path) -> None:
    # main numbers are read from git, not from the checkout
    settings = _settings(tmp_path)
    work = _repo(tmp_path)
    stray = work.path / ".novui" / "approvals"
    stray.mkdir(parents=True)
    (stray / "A-0050.yaml").write_text("x\n", encoding="utf-8")
    assert reserve_approval_id(settings, work) == "A-0001"


def test_reserve_rejects_corrupt_counter(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    work = _repo(tmp_path)
    counter = settings.data_dir / "works" / "demo" / "approvals.next"
    counter.parent.mkdir(parents=True)
    counter.write_text("abc\n", encoding="utf-8")
    with pytest.raises(ApprovalError):
        reserve_approval_id(settings, work)


def test_reserve_is_exclusive_across_threads(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    work = _repo(tmp_path)
    ids: list[str] = []
    lock = threading.Lock()

    def worker() -> None:
        got = reserve_approval_id(settings, work)
        with lock:
            ids.append(got)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(set(ids)) == 8
    assert sorted(ids) == [f"A-{n:04d}" for n in range(1, 9)]


def test_build_approval_valid() -> None:
    patches = [_patch("foreshadowing/registry.yaml"), _patch("characters/C001.yaml")]
    doc = build_approval("A-0001", job_id="job-12", patches=patches, approved_at="2026-10-07T12:00:00+09:00")
    validate_or_raise(doc, "approval")
    assert check_approval(doc) == []
    assert doc["source"] == "proposal"
    assert doc["instruction_id"] is None
    assert doc["job_id"] == "job-12"
    assert doc["targets"] == ["characters/C001.yaml", "foreshadowing/registry.yaml"]
    assert doc["approved_by"] == "human"
    assert doc["approved_at"] == "2026-10-07T12:00:00+09:00"
    # default approved_at is a valid datetime
    doc2 = build_approval("A-0002", job_id="job-12", patches=patches)
    validate_or_raise(doc2, "approval")


def test_build_approval_errors() -> None:
    with pytest.raises(ValueError):
        build_approval("A-0001", job_id="job-1", patches=[])
    with pytest.raises(ValueError):
        build_approval("bad", job_id="job-1", patches=[_patch("characters/C001.yaml")])
    with pytest.raises(SchemaError):
        build_approval("A-0001", job_id="bad-job", patches=[_patch("characters/C001.yaml")])


def test_check_approval_applicable() -> None:
    patches = [_patch("characters/C001.yaml"), _patch("foreshadowing/registry.yaml")]
    approval = build_approval("A-0001", job_id="job-1", patches=patches)
    check_approval_applicable(approval, patches)
    check_approval_applicable(approval, list(reversed(patches)))

    tampered = copy.deepcopy(patches)
    tampered[0]["operations"][0]["value"] = "C002"
    with pytest.raises(ApprovalError, match="patch_sha256 mismatch"):
        check_approval_applicable(approval, tampered)

    # hash matches but a target is not covered
    narrowed = dict(approval)
    narrowed["targets"] = ["characters/C001.yaml"]
    with pytest.raises(ApprovalError, match="not covered"):
        check_approval_applicable(narrowed, patches)

    with pytest.raises(ApprovalError):
        check_approval_applicable(approval, [])


def test_is_approval_used(tmp_path: Path) -> None:
    work = _repo(tmp_path)
    assert is_approval_used(work.path, "A-0001") is False
    _commit_approval_on_main(work, 1)
    assert is_approval_used(work.path, "A-0001") is True
    assert is_approval_used(work.path, "A-0002") is False

    _git(work.path, "checkout", "-q", "-b", "ai/ch-001/job-3")
    _commit_approval_on_main(work, 2)  # committed on the branch
    _git(work.path, "checkout", "-q", "main")
    assert is_approval_used(work.path, "A-0002") is False
    assert is_approval_used(work.path, "A-0002", branch="ai/ch-001/job-3") is True
    assert is_approval_used(work.path, "A-0003", branch="ai/ch-001/job-3") is False
    with pytest.raises(ValueError):
        is_approval_used(work.path, "bad")
