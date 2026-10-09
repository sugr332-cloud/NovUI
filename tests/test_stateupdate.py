"""Tests for novui.stateupdate (fake AGY and fake Claude; no podman, agy or claude)."""

import copy
import re
import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_validatejob import (  # noqa: E402  (reuse the existing validate-job helpers)
    CH,
    GOOD_TEXT,
    _agy_runner,
    _branch,
    _branch_has,
    _branch_yaml,
    _checks,
    _main_snapshot,
    _saved,
    envelope,
    make_plan,
    ok_claude,
    setup_work,
)

from novui.approvals import ApprovalError, approval_rel
from novui.chapters import ChapterError, read_chapter_meta
from novui.config import Settings
from novui.draftjob import run_chapter_draft_job
from novui.gitinspect import get_changes, run_git
from novui.jobrecord import load_job_record
from novui.jobrunner import jobs_dir
from novui.procrun import ProcResult
from novui.proposals import ProposalError, find_proposals, load_proposal, pending_proposal, save_proposal, new_proposal
from novui.schema import validate_or_raise
from novui.semantics import check_approval
from novui.statepatch import file_sha256, patch_set_sha256
from novui.stateupdate import (
    AWAITING_REASON,
    approve_state,
    reject_state,
    run_state_update,
    show_proposal,
)
from novui.validatejob import run_validate_job
from novui.workrepo import CommitGuardError, commit_all, head_commit, parse_trailers
from novui.works import WorkInfo
from novui.yamlio import dumps_yaml, load_yaml, loads_yaml

CHAR = "characters/C001.yaml"
REG = "foreshadowing/registry.yaml"


@pytest.fixture(autouse=True)
def mock_container_meta(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("novui.jobrunner.image_label", lambda img, key: "1.2.14")
    monkeypatch.setattr(
        "novui.jobrunner.image_id",
        lambda img: "sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
    )


# ---------- 作品・draft・validate ----------

def rich_setup(tmp_path: Path, *, with_changes: bool = True) -> tuple[Settings, WorkInfo]:
    """setup_work + a richer C001 (relationships/address/knowledge), C002, and a plan with both characters."""
    settings, work = setup_work(tmp_path)
    repo = work.path
    rel: dict[str, Any] = {"with": "C002", "state": "仲間"}
    if with_changes:
        rel["changes"] = []
    c1 = {
        "id": "C001",
        "name": "カイ",
        "speech": {"first_person": "俺", "forbidden": ["僕"]},
        "relationships": [rel],
        "address": {"default": "お前", "C002": {"default": "美咲", "changes": []}, "C004": "先生"},
        "knowledge": [{"id": "K001", "fact": "既知の事実", "source_chapter": "ch-001"}],
    }
    (repo / "characters" / "C001.yaml").write_text(dumps_yaml(c1), encoding="utf-8")
    (repo / "characters" / "C002.yaml").write_text(
        dumps_yaml({"id": "C002", "name": "ミサキ", "speech": {"first_person": "私"}}), encoding="utf-8"
    )
    plan = make_plan()
    plan["scenes"][0]["characters"] = ["C001", "C002"]
    (repo / "chapters" / CH / "plan.yaml").write_text(dumps_yaml(plan), encoding="utf-8")
    commit_all(repo, "rich setup", [("NovUI-Edit", "human-content")], name="Tester", email="tester@test")
    return settings, work


def drafted_rich(tmp_path: Path, *, with_changes: bool = True) -> tuple[Settings, WorkInfo, dict[str, Any]]:
    settings, work = rich_setup(tmp_path, with_changes=with_changes)
    rec = run_chapter_draft_job(settings, work, CH, container_runner=_agy_runner(GOOD_TEXT))
    assert rec["state"] == "COMPLETED"
    return settings, work, rec


def validated(tmp_path: Path, *, with_changes: bool = True) -> tuple[Settings, WorkInfo, dict[str, Any]]:
    settings, work, draft = drafted_rich(tmp_path, with_changes=with_changes)
    rec = run_validate_job(settings, work, CH, claude_runner=ok_claude())
    assert rec["state"] == "COMPLETED"
    assert _branch_yaml(work.path, f"chapters/{CH}/chapter.yaml")["state"] == "AI_VALIDATED"
    return settings, work, draft


# ---------- Claude の出力 ----------

FACT = "門番が紋章に反応したことを知った"


def summary_out(**over: Any) -> dict[str, Any]:
    doc: dict[str, Any] = {
        "type": "summary",
        "chapter_id": CH,
        "events": ["S1 カイが港町ミナトに着く", "S2 門番が紋章に反応し、カイは門を通る"],
        "characters": [
            {
                "id": "C001", "location": "港の門の内側", "knowledge_added": [FACT],
                "items_gained": [], "items_lost": [], "condition": None,
                "relationship_changes": [{"with": "C002", "change": "距離が縮まった"}],
            },
            {
                "id": "C002", "location": None, "knowledge_added": [],
                "items_gained": [], "items_lost": [], "condition": None, "relationship_changes": [],
            },
        ],
        "world_impacts": [],
        "foreshadowing": [{"id": "F001", "status": "active", "note": "S2 門番が紋章に反応する"}],
        "next_start_state": "カイが港の門を通り抜けた直後",
    }
    doc.update(over)
    return doc


def _change(value: str = "親友", scene: str = "S2") -> dict[str, Any]:
    return {"value": value, "from": {"chapter": CH, "scene": scene}, "reason": "共に門を通った"}


def char_patch(ops: list[dict[str, Any]] | None = None, base_hash: str = "AUTO") -> dict[str, Any]:
    if ops is None:
        ops = [
            {"op": "test", "path": "/relationships/0/with", "value": "C002"},
            {"op": "add", "path": "/knowledge/-", "value": {"id": "K002", "fact": FACT, "source_chapter": CH}},
            {"op": "add", "path": "/relationships/0/changes/-", "value": _change()},
        ]
    return {"type": "state_patch", "target": CHAR, "base_hash": base_hash, "operations": ops, "reason": "第1章の出来事"}


def reg_patch(ops: list[dict[str, Any]] | None = None, base_hash: str = "AUTO") -> dict[str, Any]:
    if ops is None:
        ops = [
            {"op": "test", "path": "/0/id", "value": "F001"},
            {"op": "add", "path": "/0/hints/-", "value": {"chapter": CH, "scene": "S2"}},
            {"op": "replace", "path": "/0/status", "value": "active"},
        ]
    return {"type": "state_patch", "target": REG, "base_hash": base_hash, "operations": ops, "reason": "第1章の伏線"}


class FakeSU:
    """Fake Claude runner for state_update: routes by the output type and target found in the prompt."""

    def __init__(self, outputs: dict[str, list[Any]], *, timed_out: bool = False) -> None:
        self.outputs = {k: list(v) for k, v in outputs.items()}
        self.timed_out = timed_out
        self.calls: list[dict[str, Any]] = []

    def __call__(self, **kwargs: Any) -> ProcResult:
        assert list(kwargs["cwd"].iterdir()) == []
        prompt = kwargs["prompt"]
        if "type：`summary`" in prompt:
            kind = "summary"
        else:
            m = re.search(r"target：`([^`]+)`", prompt)
            assert m, "unexpected prompt"
            kind = f"patch:{m.group(1)}"
        self.calls.append({"kind": kind, "prompt": prompt})
        queue = self.outputs.get(kind, [])
        item = queue.pop(0) if queue else None
        if item is None:
            out = b""
        elif isinstance(item, bytes):
            out = item
        else:
            item = copy.deepcopy(item)
            if item.get("base_hash") == "AUTO":
                hm = re.search(r"base_hash：`(sha256:[0-9a-f]{64})`", prompt)
                assert hm
                item["base_hash"] = hm.group(1)
            out = envelope(item)
        return ProcResult(
            exit_code=0, elapsed_seconds=3.0, timed_out=self.timed_out, signal_sent=None,
            group_remaining=False, stdout=out, stderr=b"",
        )

    def kinds(self) -> list[str]:
        return [c["kind"] for c in self.calls]


def good_su(**over: Any) -> FakeSU:
    outputs: dict[str, list[Any]] = {
        "summary": [summary_out()],
        f"patch:{CHAR}": [char_patch()],
        f"patch:{REG}": [reg_patch()],
    }
    outputs.update(over)
    return FakeSU(outputs)


def run_su(settings: Settings, work: WorkInfo, fake: FakeSU, **kw: Any) -> dict[str, Any]:
    return run_state_update(settings, work, CH, claude_runner=fake, **kw)


def _wt(rec: dict[str, Any]) -> Path:
    return Path(rec["worktree"])


def _branch_head(work: WorkInfo) -> str:
    return head_commit(work.path, _branch(work.path))


def _changed(work: WorkInfo, before: str, after: str) -> list[str]:
    return sorted(run_git(work.path, "diff", "--name-only", before, after).decode().split())


# ---------- run_state_update：正常系 ----------

def test_run_state_update_success(tmp_path: Path) -> None:
    settings, work, draft = validated(tmp_path)
    repo = work.path
    main_before = _main_snapshot(repo)
    head_before = _branch_head(work)
    hash_char = file_sha256(Path(draft["worktree"]) / CHAR)

    fake = good_su()
    rec = run_su(settings, work, fake)

    assert rec["state"] == "WAITING_HUMAN"
    assert [h["state"] for h in rec["history"]] == ["QUEUED", "RUNNING", "CHECKING", "WAITING_HUMAN"]
    assert [h["event"] for h in rec["history"]] == [None, "LOCK_ACQUIRED", "CLI_EXITED", "NEEDS_HUMAN_INPUT"]
    assert rec["history"][-1]["reason"] == AWAITING_REASON
    assert rec["job_type"] == "state_update"
    assert rec["branch"] == draft["branch"] and rec["base_commit"] == draft["base_commit"]
    assert rec["cli"]["actual_model"] == "claude-opus-5-5" and rec["container"] is None
    assert fake.kinds() == ["summary", f"patch:{CHAR}", f"patch:{REG}"]
    assert _saved(settings, work, rec)["state"] == "WAITING_HUMAN"
    ctx = [c["path"] for c in rec["context"]]
    assert ctx == [c["path"] for c in draft["context"]] + [f"chapters/{CH}/draft.md"]

    names = [c["name"] for c in rec["checks"]]
    assert names == [
        "draft_consistency", "claude_summary", "summary_refs",
        "claude_state_patch_1", "patch_target", "patch_policy", "patch_apply",
        "claude_state_patch_2", "patch_target", "patch_policy", "patch_apply",
        "summary_patch_consistency",
    ]
    assert all(c["status"] == "PASS" for c in rec["checks"])

    # prompts: base_hash is the Controller's value, K numbering starts after the existing K001
    patch_prompt = fake.calls[1]["prompt"]
    assert f"`{hash_char}`" in patch_prompt
    assert "K002 から始め" in patch_prompt
    assert "関係：C002 → /relationships/0" in patch_prompt
    assert "foreshadowing/registry.yaml" in fake.calls[2]["prompt"] and "F001 → /0" in fake.calls[2]["prompt"]

    proposal = pending_proposal(settings, work.work_key, CH)
    assert proposal is not None and proposal["job_id"] == rec["job_id"]
    assert proposal["branch"] == draft["branch"]
    assert proposal["branch_head"] == head_before
    assert [e["patch"]["target"] for e in proposal["patches"]] == [CHAR, REG]
    assert proposal["patches"][0]["patch"]["base_hash"] == hash_char
    assert proposal["summary"] == summary_out()
    assert proposal["patch_set_sha256"] == patch_set_sha256([e["patch"] for e in proposal["patches"]])
    assert load_proposal(settings.data_dir / "works" / work.work_key / "proposals" / CH / f"{rec['job_id']}.yaml") == proposal

    # the job branch and main are untouched; nothing is written before approval
    assert _branch_head(work) == head_before
    assert get_changes(_wt(rec)) == []
    assert _main_snapshot(repo) == main_before
    assert not _branch_has(repo, f"chapters/{CH}/summary.yaml")
    assert not (settings.data_dir / "claude-cwd" / rec["job_id"]).exists()


def test_noop_patch_is_dropped(tmp_path: Path) -> None:
    settings, work, _ = validated(tmp_path)
    fake = good_su(**{f"patch:{REG}": [reg_patch([{"op": "test", "path": "/0/id", "value": "F001"}])]})
    rec = run_su(settings, work, fake)
    assert rec["state"] == "WAITING_HUMAN"
    proposal = pending_proposal(settings, work.work_key, CH)
    assert [e["patch"]["target"] for e in proposal["patches"]] == [CHAR]
    assert any(c["name"] == "patch_noop" and c["details"] == [REG] for c in rec["checks"])


def test_proposal_without_patches(tmp_path: Path) -> None:
    settings, work, _ = validated(tmp_path)
    s = summary_out(foreshadowing=[])
    s["characters"][0]["knowledge_added"] = []
    s["characters"][0]["relationship_changes"] = []
    fake = FakeSU({"summary": [s]})
    rec = run_su(settings, work, fake)
    assert rec["state"] == "WAITING_HUMAN"
    assert fake.kinds() == ["summary"]
    proposal = pending_proposal(settings, work.work_key, CH)
    assert proposal["patches"] == [] and proposal["patch_set_sha256"] is None


def test_claude_base_hash_is_replaced_and_warned(tmp_path: Path) -> None:
    settings, work, draft = validated(tmp_path)
    wrong = "sha256:" + "f" * 64
    fake = good_su(**{f"patch:{CHAR}": [char_patch(base_hash=wrong)]})
    rec = run_su(settings, work, fake)
    assert rec["state"] == "WAITING_HUMAN"
    warn = [c for c in rec["checks"] if c["name"] == "base_hash_corrected"]
    assert warn and warn[0]["status"] == "WARNING" and warn[0]["details"] == [CHAR]
    proposal = pending_proposal(settings, work.work_key, CH)
    assert proposal["patches"][0]["patch"]["base_hash"] == file_sha256(Path(draft["worktree"]) / CHAR)


def test_summary_patch_consistency_warning_does_not_stop(tmp_path: Path) -> None:
    settings, work, _ = validated(tmp_path)
    s = summary_out()
    s["characters"][0]["knowledge_added"] = [FACT, "別の事実"]
    rec = run_su(settings, work, good_su(summary=[s]))
    assert rec["state"] == "WAITING_HUMAN"
    chk = _checks(rec)["summary_patch_consistency"]
    assert chk["status"] == "WARNING" and chk["details"] == ["C001: 別の事実"]


# ---------- run_state_update：FAILED ----------

def _assert_failed_clean(settings: Settings, work: WorkInfo, rec: dict[str, Any], head_before: str) -> None:
    assert rec["state"] == "FAILED"
    assert pending_proposal(settings, work.work_key, CH) is None
    assert find_proposals(settings, work.work_key, CH) == []
    assert _branch_head(work) == head_before
    assert get_changes(_wt(rec)) == []
    assert _saved(settings, work, rec)["state"] == "FAILED"
    assert not (settings.data_dir / "claude-cwd" / rec["job_id"]).exists()


def test_failed_summary_refs(tmp_path: Path) -> None:
    settings, work, _ = validated(tmp_path)
    head = _branch_head(work)
    s = summary_out()
    s["characters"][1]["id"] = "C009"  # no characters/C009.yaml in the context
    fake = good_su(summary=[s])
    rec = run_su(settings, work, fake)
    _assert_failed_clean(settings, work, rec, head)
    assert _checks(rec)["summary_refs"]["status"] == "FAIL"
    assert fake.kinds() == ["summary"]


def test_failed_summary_schema_exhausted(tmp_path: Path) -> None:
    settings, work, _ = validated(tmp_path)
    head = _branch_head(work)
    bad = {"type": "summary", "chapter_id": CH}
    fake = FakeSU({"summary": [envelope(bad)] * 3})
    rec = run_su(settings, work, fake)
    _assert_failed_clean(settings, work, rec, head)
    chk = _checks(rec)["claude_summary"]
    assert chk["status"] == "FAIL" and len(chk["details"]) == 3
    assert fake.kinds() == ["summary"] * 3
    assert rec["history"][-1]["reason"] == "claude output retry exhausted"


def test_schema_retry_then_success(tmp_path: Path) -> None:
    settings, work, _ = validated(tmp_path)
    bad = {"type": "summary", "chapter_id": CH}
    fake = good_su(summary=[envelope(bad), summary_out()])
    rec = run_su(settings, work, fake)
    assert rec["state"] == "WAITING_HUMAN"
    assert fake.kinds()[:2] == ["summary", "summary"]
    chk = _checks(rec)["claude_summary"]
    assert chk["status"] == "PASS" and chk["details"] and "schema" in chk["details"][0]


@pytest.mark.parametrize("label,ops", [
    ("remove", [{"op": "remove", "path": "/knowledge/0"}]),
    ("personality", [{"op": "replace", "path": "/speech/first_person", "value": "僕"}]),
    ("source chapter", [{"op": "add", "path": "/knowledge/-",
                         "value": {"id": "K002", "fact": FACT, "source_chapter": "ch-009"}}]),
])
def test_failed_policy_violation(tmp_path: Path, label: str, ops: list[dict[str, Any]]) -> None:
    settings, work, _ = validated(tmp_path)
    head = _branch_head(work)
    rec = run_su(settings, work, good_su(**{f"patch:{CHAR}": [char_patch(ops)]}))
    _assert_failed_clean(settings, work, rec, head)
    assert _checks(rec)["patch_policy"]["status"] == "FAIL", label


def test_failed_dry_run_index_drift(tmp_path: Path) -> None:
    settings, work, _ = validated(tmp_path)
    head = _branch_head(work)
    drift = reg_patch([
        {"op": "test", "path": "/5/id", "value": "F001"},
        {"op": "add", "path": "/5/hints/-", "value": {"chapter": CH, "scene": "S2"}},
    ])
    rec = run_su(settings, work, good_su(**{f"patch:{REG}": [drift]}))
    _assert_failed_clean(settings, work, rec, head)
    assert _checks(rec)["patch_policy"]["status"] == "PASS"
    assert _checks(rec)["patch_apply"]["status"] == "FAIL"


def test_failed_when_relationship_has_no_changes_key(tmp_path: Path) -> None:
    """D4: no `changes` key -> add /relationships/<i>/changes/- cannot be applied -> dry-run FAIL -> FAILED."""
    settings, work, _ = validated(tmp_path, with_changes=False)
    head = _branch_head(work)
    rec = run_su(settings, work, good_su())
    _assert_failed_clean(settings, work, rec, head)
    chk = _checks(rec)["patch_apply"]
    assert chk["status"] == "FAIL"
    assert "/relationships/0/changes/-" in chk["details"][0]
    assert _checks(rec)["patch_policy"]["status"] == "PASS"


def test_failed_claude_unavailable(tmp_path: Path) -> None:
    settings, work, _ = validated(tmp_path)
    head = _branch_head(work)
    rec = run_su(settings, work, FakeSU({"summary": [envelope(None, is_error=True)] * 3}))
    _assert_failed_clean(settings, work, rec, head)
    assert rec["history"][-1]["reason"] == "claude unavailable"
    assert _checks(rec)["claude_summary"]["status"] == "WARNING"


def test_failed_timeout(tmp_path: Path) -> None:
    settings, work, _ = validated(tmp_path)
    head = _branch_head(work)
    rec = run_su(settings, work, FakeSU({"summary": [b""]}, timed_out=True))
    _assert_failed_clean(settings, work, rec, head)
    assert rec["history"][-1]["event"] == "TIMED_OUT"


def test_failed_patch_call_after_summary(tmp_path: Path) -> None:
    settings, work, _ = validated(tmp_path)
    head = _branch_head(work)
    bad = {"type": "state_patch", "target": CHAR}
    fake = good_su(**{f"patch:{CHAR}": [envelope(bad)] * 3})
    rec = run_su(settings, work, fake)
    _assert_failed_clean(settings, work, rec, head)
    assert _checks(rec)["claude_state_patch_1"]["status"] == "FAIL"
    assert fake.kinds() == ["summary"] + [f"patch:{CHAR}"] * 3


def test_failed_draft_consistency_does_not_call_claude(tmp_path: Path) -> None:
    settings, work, draft = validated(tmp_path)
    head = _branch_head(work)
    (Path(draft["worktree"]) / "world" / "setting.md").write_text("改ざん\n", encoding="utf-8")
    fake = good_su()
    rec = run_su(settings, work, fake)
    assert rec["state"] == "FAILED"
    assert fake.calls == []
    assert _checks(rec)["draft_consistency"]["status"] == "FAIL"
    assert find_proposals(settings, work.work_key, CH) == []
    assert _branch_head(work) == head


# ---------- run_state_update：ChapterError と replace ----------

def test_chapter_errors_do_not_create_job_records(tmp_path: Path) -> None:
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()

    # not AI_VALIDATED
    settings, work, _ = drafted_rich(tmp_path / "a")
    with pytest.raises(ChapterError, match="AI_VALIDATED"):
        run_su(settings, work, good_su())
    assert not list(jobs_dir(settings, work.work_key).glob("job-*.yaml")) or all(
        load_job_record(p)["job_type"] != "state_update" for p in jobs_dir(settings, work.work_key).glob("job-*.yaml")
    )

    # no job branch
    settings2, work2 = rich_setup(tmp_path / "b")
    with pytest.raises(ChapterError, match="no job branch"):
        run_su(settings2, work2, good_su())


def test_pending_proposal_blocks_without_replace_and_replace_supersedes(tmp_path: Path) -> None:
    settings, work, _ = validated(tmp_path)
    first = run_su(settings, work, good_su())
    assert first["state"] == "WAITING_HUMAN"

    n_jobs = len(list(jobs_dir(settings, work.work_key).glob("job-*.yaml")))
    fake = good_su()
    with pytest.raises(ChapterError, match="pending"):
        run_su(settings, work, fake)
    assert fake.calls == []
    assert len(list(jobs_dir(settings, work.work_key).glob("job-*.yaml"))) == n_jobs

    second = run_su(settings, work, good_su(), replace=True)
    assert second["state"] == "WAITING_HUMAN" and second["job_id"] != first["job_id"]

    old = load_job_record(jobs_dir(settings, work.work_key) / f"{first['job_id']}.yaml")
    assert old["state"] == "CANCELLED"
    assert old["history"][-1]["reason"] == f"superseded by {second['job_id']}"
    proposals = {p["job_id"]: p for p in find_proposals(settings, work.work_key, CH)}
    assert proposals[first["job_id"]]["status"] == "superseded"
    assert proposals[first["job_id"]]["status_reason"] == f"superseded by {second['job_id']}"
    assert proposals[second["job_id"]]["status"] == "pending"
    assert pending_proposal(settings, work.work_key, CH)["job_id"] == second["job_id"]


def test_failed_replace_keeps_old_proposal_pending(tmp_path: Path) -> None:
    settings, work, _ = validated(tmp_path)
    first = run_su(settings, work, good_su())
    bad = FakeSU({"summary": [envelope({"type": "summary", "chapter_id": CH})] * 3})
    rec = run_su(settings, work, bad, replace=True)
    assert rec["state"] == "FAILED"
    assert pending_proposal(settings, work.work_key, CH)["job_id"] == first["job_id"]
    assert load_job_record(jobs_dir(settings, work.work_key) / f"{first['job_id']}.yaml")["state"] == "WAITING_HUMAN"


# ---------- approve_state ----------

def _proposed(tmp_path: Path, **kw: Any) -> tuple[Settings, WorkInfo, dict[str, Any]]:
    settings, work, _ = validated(tmp_path, **kw)
    rec = run_su(settings, work, good_su())
    assert rec["state"] == "WAITING_HUMAN"
    return settings, work, rec


def test_approve_state_with_patches(tmp_path: Path) -> None:
    settings, work, rec = _proposed(tmp_path)
    repo = work.path
    main_before = _main_snapshot(repo)
    head_before = _branch_head(work)
    proposal = pending_proposal(settings, work.work_key, CH)

    done = approve_state(settings, work, CH)

    assert done["state"] == "COMPLETED" and done["approval_id"] == "A-0001"
    assert [h["state"] for h in done["history"]][-2:] == ["WAITING_HUMAN", "COMPLETED"]
    assert _saved(settings, work, done)["approval_id"] == "A-0001"

    head_after = _branch_head(work)
    assert run_git(repo, "rev-list", "--count", f"{head_before}..{head_after}").decode().strip() == "1"
    assert _changed(work, head_before, head_after) == sorted([
        CHAR, REG, f"chapters/{CH}/summary.yaml", f"chapters/{CH}/chapter.yaml", ".novui/approvals/A-0001.yaml",
    ])
    msg = run_git(repo, "log", "-1", "--format=%B", head_after).decode()
    assert parse_trailers(msg) == {"NovUI-Job": [rec["job_id"]], "Approval-Id": ["A-0001"]}
    assert msg.splitlines()[0] == f"state update approved {CH} by {rec['job_id']}"

    meta = _branch_yaml(repo, f"chapters/{CH}/chapter.yaml")
    assert meta["state"] == "HUMAN_APPROVED" and meta["last_job"] == rec["job_id"]

    summary = _branch_yaml(repo, f"chapters/{CH}/summary.yaml")
    validate_or_raise(summary, "summary")
    assert summary == proposal["summary"]

    char = _branch_yaml(repo, CHAR)
    assert char["knowledge"][-1] == {"id": "K002", "fact": FACT, "source_chapter": CH}
    assert char["relationships"][0]["changes"] == [_change()]
    reg = _branch_yaml(repo, REG)
    assert reg[0]["hints"] == [{"chapter": CH, "scene": "S2"}] and reg[0]["status"] == "active"

    approval = _branch_yaml(repo, ".novui/approvals/A-0001.yaml")
    validate_or_raise(approval, "approval")
    assert check_approval(approval) == []
    assert approval["source"] == "proposal" and approval["job_id"] == rec["job_id"]
    assert approval["targets"] == [CHAR, REG]
    assert approval["patch_sha256"] == proposal["patch_set_sha256"]

    stored = show_proposal(settings, work, CH)
    assert stored["status"] == "approved" and stored["approval_id"] == "A-0001" and stored["decided_at"]
    assert pending_proposal(settings, work.work_key, CH) is None

    # main and the working tree of the job branch
    assert _main_snapshot(repo) == main_before
    assert read_chapter_meta(repo, CH)["state"] == "PLAN_APPROVED"
    assert get_changes(_wt(rec)) == []


def test_approve_state_without_patches_has_no_approval_record(tmp_path: Path) -> None:
    settings, work, _ = validated(tmp_path)
    s = summary_out(foreshadowing=[])
    s["characters"][0]["knowledge_added"] = []
    s["characters"][0]["relationship_changes"] = []
    rec = run_su(settings, work, FakeSU({"summary": [s]}))
    head_before = _branch_head(work)

    done = approve_state(settings, work, CH)
    assert done["state"] == "COMPLETED" and done["approval_id"] is None
    head_after = _branch_head(work)
    assert _changed(work, head_before, head_after) == sorted([f"chapters/{CH}/summary.yaml", f"chapters/{CH}/chapter.yaml"])
    msg = run_git(work.path, "log", "-1", "--format=%B", head_after).decode()
    assert parse_trailers(msg) == {"NovUI-Job": [rec["job_id"]]}
    assert _branch_yaml(work.path, f"chapters/{CH}/chapter.yaml")["state"] == "HUMAN_APPROVED"
    assert not _branch_has(work.path, ".novui/approvals/A-0001.yaml")
    stored = show_proposal(settings, work, CH)
    assert stored["status"] == "approved" and stored["approval_id"] is None


def test_commit_guard_applies_on_the_job_branch(tmp_path: Path) -> None:
    """The existing guard rejects a protected change without Approval-Id (or NovUI-Edit); nothing bypasses it."""
    settings, work, rec = _proposed(tmp_path)
    wt = _wt(rec)
    head = _branch_head(work)
    path = wt / CHAR
    original = path.read_bytes()
    path.write_text(path.read_text(encoding="utf-8") + "# stray\n", encoding="utf-8")
    with pytest.raises(CommitGuardError):
        commit_all(wt, "stray change", [("NovUI-Job", rec["job_id"])], name="t", email="t@t")
    path.write_bytes(original)
    assert _branch_head(work) == head
    assert get_changes(wt) == []


def _tamper_proposal(settings: Settings, work: WorkInfo, mutate: Any) -> None:
    proposal = pending_proposal(settings, work.work_key, CH)
    patches = copy.deepcopy([e["patch"] for e in proposal["patches"]])
    mutate(patches)
    tampered = new_proposal(
        job_id=proposal["job_id"], chapter_id=CH, branch=proposal["branch"],
        branch_head=proposal["branch_head"], summary=proposal["summary"], patches=patches,
    )
    save_proposal(settings, work.work_key, tampered)


def _assert_invalid(settings: Settings, work: WorkInfo, rec: dict[str, Any], head: str) -> None:
    stored = show_proposal(settings, work, CH)
    assert stored["status"] == "invalid" and stored["status_reason"]
    job = load_job_record(jobs_dir(settings, work.work_key) / f"{rec['job_id']}.yaml")
    assert job["state"] == "CANCELLED" and job["history"][-1]["reason"].startswith("invalid: ")
    assert _branch_head(work) == head
    assert get_changes(_wt(rec)) == []
    assert pending_proposal(settings, work.work_key, CH) is None


def test_approve_invalid_when_branch_head_moved(tmp_path: Path) -> None:
    settings, work, rec = _proposed(tmp_path)
    wt = _wt(rec)
    (wt / "chapters" / CH / "requests.yaml").write_text("[]\n", encoding="utf-8")
    commit_all(wt, "extra commit", [("NovUI-Job", "job-99")], name="t", email="t@t")
    head = _branch_head(work)
    with pytest.raises(ProposalError, match="invalid"):
        approve_state(settings, work, CH)
    _assert_invalid(settings, work, rec, head)
    assert not _branch_has(work.path, ".novui/approvals/A-0001.yaml")


def test_approve_invalid_when_base_hash_mismatch(tmp_path: Path) -> None:
    settings, work, rec = _proposed(tmp_path)
    head = _branch_head(work)

    def mutate(patches: list[dict[str, Any]]) -> None:
        patches[0]["base_hash"] = "sha256:" + "a" * 64

    _tamper_proposal(settings, work, mutate)
    with pytest.raises(ProposalError, match="base_hash mismatch"):
        approve_state(settings, work, CH)
    _assert_invalid(settings, work, rec, head)


def test_approve_invalid_when_policy_fails_again(tmp_path: Path) -> None:
    settings, work, rec = _proposed(tmp_path)
    head = _branch_head(work)

    def mutate(patches: list[dict[str, Any]]) -> None:
        patches[0]["operations"].append({"op": "remove", "path": "/knowledge/0"})

    _tamper_proposal(settings, work, mutate)
    with pytest.raises(ProposalError, match="patch_policy"):
        approve_state(settings, work, CH)
    _assert_invalid(settings, work, rec, head)


def test_approve_requires_pending_proposal_and_clean_worktree(tmp_path: Path) -> None:
    settings, work, _ = validated(tmp_path)
    with pytest.raises(ChapterError, match="no pending"):
        approve_state(settings, work, CH)

    rec = run_su(settings, work, good_su())
    wt = _wt(rec)
    (wt / "stray.txt").write_text("x\n", encoding="utf-8")
    with pytest.raises(ChapterError, match="uncommitted"):
        approve_state(settings, work, CH)
    assert pending_proposal(settings, work.work_key, CH) is not None
    (wt / "stray.txt").unlink()


def test_approve_failure_during_commit_restores_files_and_never_reuses_numbers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings, work, rec = _proposed(tmp_path)
    head = _branch_head(work)

    def boom(*args: Any, **kwargs: Any) -> str:
        raise RuntimeError("commit failed")

    with monkeypatch.context() as m:
        m.setattr("novui.stateupdate.commit_all", boom)
        with pytest.raises(RuntimeError, match="commit failed"):
            approve_state(settings, work, CH)

    assert _branch_head(work) == head
    assert get_changes(_wt(rec)) == []
    assert not (_wt(rec) / ".novui" / "approvals" / "A-0001.yaml").exists()
    assert pending_proposal(settings, work.work_key, CH)["job_id"] == rec["job_id"]
    assert load_job_record(jobs_dir(settings, work.work_key) / f"{rec['job_id']}.yaml")["state"] == "WAITING_HUMAN"

    # A-0001 was reserved and is never reused
    done = approve_state(settings, work, CH)
    assert done["approval_id"] == "A-0002"
    assert _branch_has(work.path, ".novui/approvals/A-0002.yaml")
    assert not _branch_has(work.path, ".novui/approvals/A-0001.yaml")


def test_approve_outside_allowed_paths_restores_written_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings, work, rec = _proposed(tmp_path)
    head = _branch_head(work)
    wt = _wt(rec)

    import novui.stateupdate as su
    real_apply = su.apply_patch

    def apply_and_stray(doc: Any, patch: Any) -> Any:
        (wt / "chapters" / CH / "stray.txt").write_text("x\n", encoding="utf-8")
        return real_apply(doc, patch)

    monkeypatch.setattr("novui.stateupdate.apply_patch", apply_and_stray)
    with pytest.raises(ChapterError, match="outside allowed paths"):
        approve_state(settings, work, CH)

    assert _branch_head(work) == head
    assert [c.path for c in get_changes(wt)] == [f"chapters/{CH}/stray.txt"]  # only the foreign file remains
    assert file_sha256(wt / CHAR) == pending_proposal(settings, work.work_key, CH)["patches"][0]["patch"]["base_hash"]
    assert not (wt / f"chapters/{CH}/summary.yaml").exists()
    assert pending_proposal(settings, work.work_key, CH) is not None
    (wt / "chapters" / CH / "stray.txt").unlink()


def test_approve_refuses_a_used_approval_number(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings, work, rec = _proposed(tmp_path)
    repo = work.path
    head = _branch_head(work)
    # A-0001 already exists on main
    p = repo / approval_rel("A-0001")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("approval_id: A-0001\n", encoding="utf-8")
    commit_all(repo, "used approval", [("NovUI-Edit", "human-content")], name="t", email="t@t")

    monkeypatch.setattr("novui.stateupdate.reserve_approval_id", lambda *a, **k: "A-0001")
    with pytest.raises(ApprovalError, match="already used"):
        approve_state(settings, work, CH)
    assert _branch_head(work) == head
    assert get_changes(_wt(rec)) == []
    assert pending_proposal(settings, work.work_key, CH) is not None


def test_approve_recovers_after_commit_without_record_update(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings, work, rec = _proposed(tmp_path)
    head_before = _branch_head(work)

    def crash(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("crashed after commit")

    with monkeypatch.context() as m:
        m.setattr("novui.stateupdate._finish_approval", crash)
        with pytest.raises(RuntimeError, match="crashed after commit"):
            approve_state(settings, work, CH)

    committed = _branch_head(work)
    assert committed != head_before
    assert pending_proposal(settings, work.work_key, CH) is not None  # not yet recorded
    assert load_job_record(jobs_dir(settings, work.work_key) / f"{rec['job_id']}.yaml")["state"] == "WAITING_HUMAN"

    done = approve_state(settings, work, CH)  # finishes the recording only
    assert _branch_head(work) == committed
    assert done["state"] == "COMPLETED" and done["approval_id"] == "A-0001"
    stored = show_proposal(settings, work, CH)
    assert stored["status"] == "approved" and stored["approval_id"] == "A-0001"
    assert pending_proposal(settings, work.work_key, CH) is None


# ---------- reject_state / show_proposal ----------

def test_reject_state(tmp_path: Path) -> None:
    settings, work, rec = _proposed(tmp_path)
    head = _branch_head(work)

    for bad in ("", "   "):
        with pytest.raises(ValueError):
            reject_state(settings, work, CH, bad)
    assert pending_proposal(settings, work.work_key, CH) is not None

    done = reject_state(settings, work, CH, "要約が不正確")
    assert done["state"] == "CANCELLED" and done["history"][-1]["reason"] == "要約が不正確"
    stored = show_proposal(settings, work, CH)
    assert stored["status"] == "rejected" and stored["status_reason"] == "要約が不正確"
    assert pending_proposal(settings, work.work_key, CH) is None
    assert _branch_head(work) == head
    assert get_changes(_wt(rec)) == []

    with pytest.raises(ChapterError, match="no pending"):
        reject_state(settings, work, CH, "again")

    # a new proposal can be made after the rejection (no replace needed)
    again = run_su(settings, work, good_su())
    assert again["state"] == "WAITING_HUMAN" and again["job_id"] != rec["job_id"]


def test_show_proposal(tmp_path: Path) -> None:
    settings, work, _ = validated(tmp_path)
    assert show_proposal(settings, work, CH) is None
    rec = run_su(settings, work, good_su())
    assert show_proposal(settings, work, CH)["job_id"] == rec["job_id"]
    reject_state(settings, work, CH, "やり直し")
    assert show_proposal(settings, work, CH)["status"] == "rejected"
    second = run_su(settings, work, good_su())
    assert show_proposal(settings, work, CH)["job_id"] == second["job_id"]
    assert loads_yaml(dumps_yaml(show_proposal(settings, work, CH)))["status"] == "pending"
