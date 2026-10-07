"""Tests for novui.proposals (Controller-side proposal store; schema proposal)."""

import copy
from pathlib import Path

import pytest

from novui.config import Settings
from novui.proposals import (
    ProposalError,
    check_proposal,
    find_proposals,
    load_proposal,
    new_proposal,
    pending_proposal,
    proposals_dir,
    save_proposal,
    with_status,
)
from novui.schema import SchemaError, validate
from novui.statepatch import patch_set_sha256, patch_sha256
from novui.yamlio import load_yaml

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "valid"
BASE_HASH = "sha256:" + "0" * 64
HEAD = "a" * 40
WORK = "demo"


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        data_dir=tmp_path / "data",
        worktree_root=tmp_path / "data" / "worktrees",
        jobhome_root=tmp_path / "data" / "jobhomes",
        agy_image="localhost/novui-agy:test",
        agy_token_path=tmp_path / "token",
        timeouts={"agy_draft": 10, "agy_range_edit": 10, "claude": 10},
    )


def _patch(target: str, fact: str = "事実") -> dict:
    return {
        "type": "state_patch",
        "target": target,
        "base_hash": BASE_HASH,
        "operations": [
            {"op": "test", "path": "/id", "value": "C001"},
            {"op": "add", "path": "/knowledge/-", "value": {"id": "K015", "fact": fact, "source_chapter": "ch-001"}},
        ],
        "reason": "理由",
    }


def _proposal(job_id: str = "job-5", patches: list | None = None, chapter: str = "ch-001") -> dict:
    if patches is None:
        patches = [_patch("foreshadowing/registry.yaml"), _patch("characters/C001.yaml")]
    summary = load_yaml(FIXTURES_DIR / "summary.yaml")
    summary["chapter_id"] = chapter
    return new_proposal(
        job_id=job_id,
        chapter_id=chapter,
        branch=f"ai/{chapter}/job-3",
        branch_head=HEAD,
        summary=summary,
        patches=patches,
    )


def test_new_proposal_hashes_and_order() -> None:
    p = _proposal()
    assert validate(p, "proposal") == []
    assert check_proposal(p) == []
    assert p["type"] == "state_proposal"
    assert p["status"] == "pending"
    assert p["status_reason"] is None and p["decided_at"] is None and p["approval_id"] is None
    assert [e["patch"]["target"] for e in p["patches"]] == ["characters/C001.yaml", "foreshadowing/registry.yaml"]
    for e in p["patches"]:
        assert e["patch_sha256"] == patch_sha256(e["patch"])
    assert p["patch_set_sha256"] == patch_set_sha256([e["patch"] for e in p["patches"]])


def test_new_proposal_without_patches() -> None:
    p = _proposal(patches=[])
    assert p["patches"] == [] and p["patch_set_sha256"] is None
    assert validate(p, "proposal") == [] and check_proposal(p) == []


def test_new_proposal_does_not_alias_inputs() -> None:
    patch = _patch("characters/C001.yaml")
    p = new_proposal(job_id="job-1", chapter_id="ch-001", branch="ai/ch-001/job-1", branch_head=HEAD,
                     summary=load_yaml(FIXTURES_DIR / "summary.yaml"), patches=[patch])
    patch["reason"] = "変更後"
    assert p["patches"][0]["patch"]["reason"] == "理由"


def test_save_and_load_round_trip(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    p = _proposal()
    path = save_proposal(settings, WORK, p)
    assert path == proposals_dir(settings, WORK) / "ch-001" / "job-5.yaml"
    assert path == settings.data_dir / "works" / WORK / "proposals" / "ch-001" / "job-5.yaml"
    assert load_proposal(path) == p


def test_save_rejects_invalid_proposals_without_writing(tmp_path: Path) -> None:
    settings = _settings(tmp_path)

    bad_schema = _proposal()
    bad_schema["status"] = "weird"
    with pytest.raises(SchemaError):
        save_proposal(settings, WORK, bad_schema)

    bad_hash = _proposal()
    bad_hash["patches"][0]["patch_sha256"] = "sha256:" + "1" * 64
    with pytest.raises(SchemaError):
        save_proposal(settings, WORK, bad_hash)

    bad_set = _proposal()
    bad_set["patch_set_sha256"] = "sha256:" + "1" * 64
    with pytest.raises(SchemaError):
        save_proposal(settings, WORK, bad_set)

    unsorted = _proposal()
    unsorted["patches"].reverse()
    with pytest.raises(SchemaError):
        save_proposal(settings, WORK, unsorted)

    dup = _proposal()
    dup["patches"].append(copy.deepcopy(dup["patches"][0]))
    with pytest.raises(SchemaError):
        save_proposal(settings, WORK, dup)

    wrong_branch = _proposal()
    wrong_branch["branch"] = "ai/ch-002/job-3"
    with pytest.raises(SchemaError):
        save_proposal(settings, WORK, wrong_branch)

    pending_with_decision = _proposal()
    pending_with_decision["decided_at"] = "2026-10-07T12:00:00+09:00"
    with pytest.raises(SchemaError):
        save_proposal(settings, WORK, pending_with_decision)

    extra = _proposal()
    extra["unexpected"] = 1
    with pytest.raises(SchemaError):
        save_proposal(settings, WORK, extra)

    assert not proposals_dir(settings, WORK).exists()


def test_load_rejects_tampered_file(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    path = save_proposal(settings, WORK, _proposal())
    text = path.read_text(encoding="utf-8").replace("理由", "改ざん", 1)
    path.write_text(text, encoding="utf-8")
    with pytest.raises(SchemaError):
        load_proposal(path)


def test_find_proposals_order_and_pending(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    assert find_proposals(settings, WORK, "ch-001") == []
    assert pending_proposal(settings, WORK, "ch-001") is None

    old = with_status(_proposal("job-9"), "rejected", reason="却下")
    old["created_at"] = "2026-10-01T10:00:00+09:00"
    new = _proposal("job-10")
    new["created_at"] = "2026-10-02T10:00:00+09:00"
    same_time_a = with_status(_proposal("job-2"), "superseded", reason="x")
    same_time_a["created_at"] = "2026-10-03T10:00:00+09:00"
    same_time_b = with_status(_proposal("job-11"), "invalid", reason="x")
    same_time_b["created_at"] = "2026-10-03T10:00:00+09:00"
    other_chapter = _proposal("job-20", chapter="ch-002")
    for p in (new, old, same_time_b, same_time_a, other_chapter):
        save_proposal(settings, WORK, p)

    found = find_proposals(settings, WORK, "ch-001")
    assert [d["job_id"] for d in found] == ["job-9", "job-10", "job-2", "job-11"]
    assert pending_proposal(settings, WORK, "ch-001")["job_id"] == "job-10"
    assert pending_proposal(settings, WORK, "ch-002")["job_id"] == "job-20"
    assert find_proposals(settings, WORK, "ch-003") == []


def test_two_pending_proposals_is_an_error(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    save_proposal(settings, WORK, _proposal("job-1"))
    save_proposal(settings, WORK, _proposal("job-2"))
    with pytest.raises(ProposalError):
        pending_proposal(settings, WORK, "ch-001")


def test_with_status_transitions() -> None:
    p = _proposal()
    original = copy.deepcopy(p)

    approved = with_status(p, "approved", approval_id="A-0001", decided_at="2026-10-07T12:00:00+09:00")
    assert p == original  # input not modified
    assert approved["status"] == "approved"
    assert approved["approval_id"] == "A-0001"
    assert approved["decided_at"] == "2026-10-07T12:00:00+09:00"
    assert validate(approved, "proposal") == [] and check_proposal(approved) == []

    rejected = with_status(p, "rejected", reason="気に入らない")
    assert rejected["status_reason"] == "気に入らない" and rejected["decided_at"] is not None
    assert check_proposal(rejected) == []

    for st in ("superseded", "invalid"):
        assert check_proposal(with_status(p, st, reason="r")) == []

    # finished proposals cannot change
    for finished in (approved, rejected):
        for st in ("approved", "rejected", "superseded", "invalid"):
            with pytest.raises(ProposalError):
                with_status(finished, st)
    # pending -> pending and unknown statuses are refused
    with pytest.raises(ProposalError):
        with_status(p, "pending")
    with pytest.raises(ProposalError):
        with_status(p, "weird")


def test_approved_without_patches_needs_no_approval_id_but_with_patches_does() -> None:
    no_patch = with_status(_proposal(patches=[]), "approved")
    assert check_proposal(no_patch) == []

    with_patches = with_status(_proposal(), "approved")  # approval_id missing
    errors = check_proposal(with_patches)
    assert any("approval_id is required" in e for e in errors)

    not_approved = with_status(_proposal(), "rejected", approval_id="A-0001")
    assert any("approval_id must be null" in e for e in check_proposal(not_approved))
