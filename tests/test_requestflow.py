"""Tests for novui.requestflow (resolve_request and redraft; fake AGY, no podman)."""

import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_finalize import MARKER_TEXT, _record, main_state, marker_draft  # noqa: E402
from test_stateupdate import CH, rich_setup  # noqa: E402
from test_validatejob import GOOD_TEXT, _agy_runner  # noqa: E402

from novui.chapters import ChapterError, read_chapter_meta
from novui.config import Settings
from novui.draftjob import build_character_rules_text, build_draft_instruction, read_plan, run_chapter_draft_job
from novui.gitinspect import get_changes, run_git
from novui.jobrecord import save_job_record
from novui.jobrunner import jobs_dir
from novui.mechanical import load_mechanical_inputs
from novui.models import ModelUnavailable
from novui.procrun import ProcResult
from novui.requestflow import (
    ARCHIVE_NAME,
    build_decision_section,
    redraft,
    resolve_request,
    unresolved_requests,
)
from novui.schema import validate_or_raise
from novui.semantics import check_requests
from novui.workrepo import chapter_branches, commit_all, head_commit, parse_trailers
from novui.works import WorkInfo
from novui.yamlio import dumps_yaml, load_yaml

@pytest.fixture(autouse=True)
def mock_container_meta(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("novui.jobrunner.image_label", lambda img, key: "1.2.14")
    monkeypatch.setattr(
        "novui.jobrunner.image_id",
        lambda img: "sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
    )


REQ = f"chapters/{CH}/requests.yaml"
D1 = "門番には名前を付けず、ただの門番とする"
D2 = "通行の許可は紋章で得たことにする"


class CaptureAgy:
    """Fake AGY runner that records the command and returns a fixed text."""

    def __init__(self, text: str) -> None:
        self.text = text
        self.commands: list[list[str]] = []

    def __call__(self, **kwargs: Any) -> ProcResult:
        self.commands.append(kwargs["command"])
        return ProcResult(
            exit_code=0, elapsed_seconds=1.0, timed_out=False, signal_sent=None,
            group_remaining=False, stdout=self.text.encode("utf-8"), stderr=b"",
        )

    def prompt(self, index: int = -1) -> str:
        return self.commands[index][-1].removeprefix("--print=")


def _stopped(tmp_path: Path) -> tuple[Settings, WorkInfo, dict[str, Any], CaptureAgy]:
    """A draft that stopped at two 【要確認】 (WAITING_HUMAN, branch kept)."""
    settings, work = rich_setup(tmp_path)
    agy = CaptureAgy(MARKER_TEXT)
    rec = run_chapter_draft_job(settings, work, CH, container_runner=agy)
    assert rec["state"] == "WAITING_HUMAN"
    return settings, work, rec, agy


def _wt(rec: dict[str, Any]) -> Path:
    return Path(rec["worktree"])


def _requests(rec: dict[str, Any]) -> list[dict[str, Any]]:
    return load_yaml(_wt(rec) / REQ)


def _resolve_all(settings: Settings, work: WorkInfo) -> None:
    resolve_request(settings, work, CH, 0, D1)
    resolve_request(settings, work, CH, 1, D2)


# ---------- 純粋関数 ----------

def test_unresolved_requests_and_decision_section() -> None:
    reqs: list[dict[str, Any]] = [
        {"type": "request", "job_id": "job-1", "chapter_id": CH, "kind": "undefined_setting", "target": None, "message": "A"},
        {"type": "request", "job_id": "job-1", "chapter_id": CH, "kind": "undefined_setting", "target": None, "message": "B"},
        {"type": "resolution", "request_index": 1, "decision": "bの判断", "resolved_at": "2026-10-07T12:00:00+09:00"},
        {"type": "resolution", "request_index": 1, "decision": "bの最終判断", "resolved_at": "2026-10-07T12:01:00+09:00"},
    ]
    assert unresolved_requests(reqs) == [0]
    assert unresolved_requests(reqs[:2]) == [0, 1]
    assert unresolved_requests([]) == []
    reqs.append({"type": "resolution", "request_index": 0, "decision": "aの判断",
                 "resolved_at": "2026-10-07T12:02:00+09:00"})
    assert unresolved_requests(reqs) == []

    section = build_decision_section(reqs)
    assert section.startswith("---\n【Human の判断】\n前回の本文には次の確認事項がありました。")
    assert section.endswith("* 確認事項：A\n  判断：aの判断\n* 確認事項：B\n  判断：bの最終判断")
    # requests without a decision are not listed
    assert "確認事項：B" not in build_decision_section(reqs[:2])


# ---------- resolve_request ----------

def test_resolve_request_appends_one_resolution_at_a_time(tmp_path: Path) -> None:
    settings, work, rec, _ = _stopped(tmp_path)
    repo = work.path
    wt = _wt(rec)
    original = (wt / REQ).read_bytes()
    assert unresolved_requests(_requests(rec)) == [0, 1]
    head0 = head_commit(wt, "HEAD")

    r1 = resolve_request(settings, work, CH, 0, f"  {D1}  ")
    assert r1["state"] == "COMPLETED" and r1["job_type"] == "resolve_request"
    assert r1["history"][-1]["reason"] == f"request 0: {D1}"
    assert r1["branch"] == rec["branch"] and r1["worktree"] == rec["worktree"]
    assert [h["state"] for h in r1["history"]] == ["QUEUED", "RUNNING", "CHECKING", "COMPLETED"]
    assert _record(settings, work, r1["job_id"])["state"] == "COMPLETED"

    after1 = (wt / REQ).read_bytes()
    assert after1.startswith(original)  # append only
    reqs = _requests(rec)
    validate_or_raise(reqs, "requests")
    assert check_requests(reqs) == []
    assert reqs[2]["type"] == "resolution" and reqs[2]["request_index"] == 0 and reqs[2]["decision"] == D1
    assert unresolved_requests(reqs) == [1]
    assert run_git(repo, "rev-list", "--count", f"{head0}..{head_commit(wt, 'HEAD')}").decode().strip() == "1"
    msg = run_git(wt, "log", "-1", "--format=%B").decode()
    assert msg.splitlines()[0] == f"resolve request 0 of {CH}"
    assert parse_trailers(msg) == {"NovUI-Job": [r1["job_id"]]}
    assert get_changes(wt) == []

    r2 = resolve_request(settings, work, CH, 1, D2)
    assert r2["job_id"] != r1["job_id"]
    assert (wt / REQ).read_bytes().startswith(after1)
    assert unresolved_requests(_requests(rec)) == []

    # nothing else changed: the chapter state, the draft Job, main
    assert load_yaml(wt / f"chapters/{CH}/chapter.yaml")["state"] == "PLAN_APPROVED"
    assert _record(settings, work, rec["job_id"])["state"] == "WAITING_HUMAN"
    assert read_chapter_meta(repo, CH)["state"] == "PLAN_APPROVED"
    changed = run_git(repo, "diff", "--name-only", head0, head_commit(wt, "HEAD")).decode().split()
    assert changed == [REQ]


@pytest.mark.parametrize("index,decision,exc,match", [
    (5, "x", ChapterError, "out of range"),
    (-1, "x", ChapterError, "out of range"),
    (2, "x", ChapterError, "is not a request"),  # index 2 is the resolution appended below
    (0, "x", ChapterError, "already resolved"),
    (1, "   ", ValueError, "must not be empty"),
    (1, "", ValueError, "must not be empty"),
])
def test_resolve_request_errors_leave_everything_unchanged(
    tmp_path: Path, index: int, decision: str, exc: type, match: str
) -> None:
    settings, work, rec, _ = _stopped(tmp_path)
    resolve_request(settings, work, CH, 0, D1)  # index 0 is resolved, index 2 is a resolution
    wt = _wt(rec)
    head = head_commit(wt, "HEAD")
    content = (wt / REQ).read_bytes()
    n_jobs = len(list(jobs_dir(settings, work.work_key).glob("job-*.yaml")))

    with pytest.raises(exc, match=match):
        resolve_request(settings, work, CH, index, decision)

    assert head_commit(wt, "HEAD") == head and (wt / REQ).read_bytes() == content
    assert len(list(jobs_dir(settings, work.work_key).glob("job-*.yaml"))) == n_jobs
    assert get_changes(wt) == []


def test_resolve_request_requires_clean_worktree_and_matching_record(tmp_path: Path) -> None:
    settings, work, rec, _ = _stopped(tmp_path)
    wt = _wt(rec)
    (wt / "stray.txt").write_text("x\n", encoding="utf-8")
    with pytest.raises(ChapterError, match="uncommitted"):
        resolve_request(settings, work, CH, 0, D1)
    (wt / "stray.txt").unlink()

    stored = _record(settings, work, rec["job_id"])
    stored["branch"] = "ai/ch-001/job-77"
    save_job_record(jobs_dir(settings, work.work_key), stored)
    with pytest.raises(ChapterError, match="does not match its draft job record"):
        resolve_request(settings, work, CH, 0, D1)
    assert unresolved_requests(_requests(rec)) == [0, 1]


def test_resolve_request_without_requests_file(tmp_path: Path) -> None:
    settings, work = rich_setup(tmp_path)
    rec = run_chapter_draft_job(settings, work, CH, container_runner=_agy_runner(GOOD_TEXT))
    assert rec["state"] == "COMPLETED"
    with pytest.raises(ChapterError, match="requests.yaml"):
        resolve_request(settings, work, CH, 0, D1)


# ---------- redraft ----------

def test_redraft_runs_a_new_draft_with_the_decisions(tmp_path: Path) -> None:
    settings, work, rec, agy = _stopped(tmp_path)
    repo = work.path
    old_wt = _wt(rec)
    _resolve_all(settings, work)
    archived_source = (old_wt / REQ).read_bytes()
    first_prompt = agy.prompt(0)
    main_before = main_state(repo)

    clean = CaptureAgy(GOOD_TEXT)
    new = redraft(settings, work, CH, container_runner=clean)

    assert new["job_type"] == "draft" and new["state"] == "COMPLETED"
    assert new["job_id"] != rec["job_id"] and new["branch"] != rec["branch"]
    assert chapter_branches(repo, CH) == [new["branch"]]
    assert not old_wt.exists() and _wt(new).is_dir()
    assert load_yaml(_wt(new) / f"chapters/{CH}/chapter.yaml")["state"] == "DRAFTED"
    assert main_state(repo) == main_before

    old = _record(settings, work, rec["job_id"])
    assert old["state"] == "CANCELLED" and old["history"][-1]["reason"] == f"redrafted by {new['job_id']}"

    archive = jobs_dir(settings, work.work_key) / f"{rec['job_id']}-logs" / ARCHIVE_NAME
    assert archive.read_bytes() == archived_source
    archived = load_yaml(archive)
    assert [r["type"] for r in archived] == ["request", "request", "resolution", "resolution"]

    # the AGY instruction is the normal draft instruction plus the decisions; the context is identical
    prompt = clean.prompt()
    plan = read_plan(repo, CH)
    assert prompt.startswith(first_prompt)  # same preamble, same context files, same base instruction
    assert prompt.endswith(
        build_draft_instruction(
            CH, plan,
            character_rules=build_character_rules_text(repo, CH, plan, load_mechanical_inputs(repo)),
        )
        + "\n\n" + build_decision_section(archived)
    )
    assert "【Human の判断】" in prompt and f"判断：{D1}" in prompt and f"判断：{D2}" in prompt
    assert "確認事項：門番の名前" in prompt and "確認事項：通行の許可" in prompt
    assert new["context"] == rec["context"]


def test_redraft_can_stop_again_at_new_markers(tmp_path: Path) -> None:
    settings, work, rec, _ = _stopped(tmp_path)
    _resolve_all(settings, work)
    new = redraft(settings, work, CH, container_runner=CaptureAgy(MARKER_TEXT.replace("門番の名前", "門番の年齢")))
    assert new["state"] == "WAITING_HUMAN"
    assert chapter_branches(work.path, CH) == [new["branch"]]
    reqs = _requests(new)
    assert [r["type"] for r in reqs] == ["request", "request"]
    assert all(r["job_id"] == new["job_id"] for r in reqs)
    assert load_yaml(_wt(new) / f"chapters/{CH}/chapter.yaml")["state"] == "PLAN_APPROVED"


def _assert_old_cycle_intact(settings: Settings, work: WorkInfo, rec: dict[str, Any], head: str, requests: bytes) -> None:
    assert chapter_branches(work.path, CH) == [rec["branch"]]
    assert _wt(rec).is_dir() and head_commit(_wt(rec), "HEAD") == head
    assert (_wt(rec) / REQ).read_bytes() == requests
    assert get_changes(_wt(rec)) == []
    assert _record(settings, work, rec["job_id"])["state"] == "WAITING_HUMAN"
    assert not (jobs_dir(settings, work.work_key) / f"{rec['job_id']}-logs" / ARCHIVE_NAME).exists()


def test_redraft_with_unresolved_requests_removes_nothing(tmp_path: Path) -> None:
    settings, work, rec, _ = _stopped(tmp_path)
    resolve_request(settings, work, CH, 0, D1)
    head, content = head_commit(_wt(rec), "HEAD"), (_wt(rec) / REQ).read_bytes()
    runner = CaptureAgy(GOOD_TEXT)
    with pytest.raises(ChapterError, match="unresolved requests remain"):
        redraft(settings, work, CH, container_runner=runner)
    assert runner.commands == []
    _assert_old_cycle_intact(settings, work, rec, head, content)


def test_redraft_preflight_failures_never_remove_the_old_cycle(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings, work, rec, _ = _stopped(tmp_path)
    _resolve_all(settings, work)
    repo = work.path
    head, content = head_commit(_wt(rec), "HEAD"), (_wt(rec) / REQ).read_bytes()
    runner = CaptureAgy(GOOD_TEXT)

    # 1. the draft model is not available
    def unavailable(*args: Any, **kwargs: Any) -> str:
        raise ModelUnavailable("no model", role="draft", model_id="gone")

    with monkeypatch.context() as m:
        m.setattr("novui.requestflow.resolve_model", unavailable)
        with pytest.raises(ChapterError, match="draft model is not available"):
            redraft(settings, work, CH, container_runner=runner)
    _assert_old_cycle_intact(settings, work, rec, head, content)

    # 2. main is dirty
    (repo / "stray.txt").write_text("x\n", encoding="utf-8")
    with pytest.raises(ChapterError, match="uncommitted"):
        redraft(settings, work, CH, container_runner=runner)
    (repo / "stray.txt").unlink()
    _assert_old_cycle_intact(settings, work, rec, head, content)

    # 3. the plan on main is broken
    (repo / "chapters" / CH / "plan.yaml").write_text("type: plan\nchapter_id: ch-001\n", encoding="utf-8")
    commit_all(repo, "break plan", [], name="t", email="t@t")
    with pytest.raises(Exception):
        redraft(settings, work, CH, container_runner=runner)
    _assert_old_cycle_intact(settings, work, rec, head, content)
    assert runner.commands == []


def test_redraft_stops_when_the_archive_already_exists(tmp_path: Path) -> None:
    settings, work, rec, _ = _stopped(tmp_path)
    _resolve_all(settings, work)
    head, content = head_commit(_wt(rec), "HEAD"), (_wt(rec) / REQ).read_bytes()
    log_dir = jobs_dir(settings, work.work_key) / f"{rec['job_id']}-logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    (log_dir / ARCHIVE_NAME).write_text("earlier archive\n", encoding="utf-8")
    runner = CaptureAgy(GOOD_TEXT)
    with pytest.raises(ChapterError, match="archive already exists"):
        redraft(settings, work, CH, container_runner=runner)
    assert runner.commands == []
    assert (log_dir / ARCHIVE_NAME).read_text(encoding="utf-8") == "earlier archive\n"
    assert chapter_branches(work.path, CH) == [rec["branch"]]
    assert head_commit(_wt(rec), "HEAD") == head and (_wt(rec) / REQ).read_bytes() == content


def test_redraft_stops_on_a_record_mismatch(tmp_path: Path) -> None:
    settings, work, rec, _ = _stopped(tmp_path)
    _resolve_all(settings, work)
    stored = _record(settings, work, rec["job_id"])
    stored["worktree"] = "/tmp/elsewhere/job-x"
    save_job_record(jobs_dir(settings, work.work_key), stored)
    runner = CaptureAgy(GOOD_TEXT)
    with pytest.raises(ChapterError, match="does not match its draft job record"):
        redraft(settings, work, CH, container_runner=runner)
    assert runner.commands == [] and chapter_branches(work.path, CH) == [rec["branch"]] and _wt(rec).is_dir()


def test_redraft_is_only_for_a_stopped_draft(tmp_path: Path) -> None:
    settings, work = rich_setup(tmp_path)
    done = run_chapter_draft_job(settings, work, CH, container_runner=_agy_runner(GOOD_TEXT))
    assert done["state"] == "COMPLETED"
    with pytest.raises(ChapterError, match="PLAN_APPROVED"):
        redraft(settings, work, CH, container_runner=CaptureAgy(GOOD_TEXT))
    assert chapter_branches(work.path, CH) == [done["branch"]]


def test_redraft_without_a_branch(tmp_path: Path) -> None:
    settings, work = rich_setup(tmp_path)
    with pytest.raises(ChapterError, match="no job branch"):
        redraft(settings, work, CH, container_runner=CaptureAgy(GOOD_TEXT))
