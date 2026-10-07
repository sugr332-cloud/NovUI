"""resolve_request and redraft for 【要確認】 requests of a draft Job (Phase 2-C3, phase2c-design §7).

resolve_request records a Human decision in requests.yaml (append only) and changes nothing else. The only way on
from a draft that contains 【要確認】 is redraft: discard the job branch and run the draft Job again with the decisions
added to the AGY instruction. redraft does every check that can fail before it removes anything.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Sequence

from novui.chapters import ChapterError, ensure_main_ready, read_chapter_meta
from novui.checks import check_allowed_paths, check_append_only
from novui.container import run_container
from novui.config import Settings
from novui.draftjob import (
    DRAFT_MODEL_ROLE,
    _mark_drafted_on_branch,
    build_draft_instruction,
    collect_draft_context,
    read_plan,
)
from novui.finalize import CycleInfo, cycle_info, remove_cycle
from novui.gitinspect import get_changes
from novui.ids import next_job_id
from novui.jobrecord import apply_transition, new_job_record, now_iso, save_job_record
from novui.jobrunner import ContainerRunner, DraftJobRequest, jobs_dir, run_draft_job
from novui.locks import RunLock
from novui.mechanical import load_mechanical_inputs, run_draft_mechanical_checks
from novui.models import ModelUnavailable, resolve_model
from novui.schema import validate_or_raise
from novui.semantics import check_requests
from novui.states import ChapterState, JobEvent, JobState
from novui.validatejob import _read_branch_chapter_meta, _restore_paths
from novui.workrepo import chapter_branches, commit_all
from novui.works import WorkInfo
from novui.yamlio import dumps_yaml, load_yaml

DECISION_HEADER = (
    "---\n"
    "【Human の判断】\n"
    "前回の本文には次の確認事項がありました。Human は次のとおり判断しました。"
    "この判断に従って書いてください。判断にないことは、これまでどおり推測で決めず【要確認：…】と書いてください。"
)
ARCHIVE_NAME = "requests-before-redraft.yaml"


def unresolved_requests(requests: Sequence[Mapping[str, Any]]) -> list[int]:
    """Indexes of the requests that no resolution points to."""
    resolved = {r["request_index"] for r in requests if r.get("type") == "resolution"}
    return [i for i, r in enumerate(requests) if r.get("type") == "request" and i not in resolved]


def build_decision_section(requests: Sequence[Mapping[str, Any]]) -> str:
    """The fixed text appended to the AGY instruction: each resolved request with its (last) decision."""
    decisions: dict[int, str] = {}
    for r in requests:
        if r.get("type") == "resolution":
            decisions[r["request_index"]] = r["decision"]
    lines = [DECISION_HEADER, ""]
    for i, r in enumerate(requests):
        if r.get("type") == "request" and i in decisions:
            lines.append(f"* 確認事項：{r['message']}")
            lines.append(f"  判断：{decisions[i]}")
    return "\n".join(lines)


def _requests_rel(chapter_id: str) -> str:
    return f"chapters/{chapter_id}/requests.yaml"


def _load_requests(worktree: Path, chapter_id: str) -> tuple[list[dict[str, Any]], bytes]:
    path = worktree / _requests_rel(chapter_id)
    if not path.is_file():
        raise ChapterError(f"{_requests_rel(chapter_id)} not found on the job branch")
    raw = path.read_bytes()
    doc = load_yaml(path)
    validate_or_raise(doc, "requests")
    sem = check_requests(doc)
    if sem:
        raise ChapterError(f"invalid requests.yaml: {sem}")
    if not doc:
        raise ChapterError("requests.yaml has no requests")
    return doc, raw


def resolve_request(
    settings: Settings,
    work: WorkInfo,
    chapter_id: str,
    index: int,
    decision: str,
    *,
    job_id: str | None = None,
) -> dict[str, Any]:
    """Append a resolution for request `index` to requests.yaml on the job branch (resolve_request Job).

    Only requests.yaml changes (append only). The chapter state and the draft Job record are not changed.
    Returns the resolve_request job record (COMPLETED; the history reason holds the decision).
    """
    if decision is None or not decision.strip():
        raise ValueError("decision must not be empty")
    decision = decision.strip()

    repo = work.path
    ensure_main_ready(repo)
    info = cycle_info(settings, work, chapter_id, require_worktree=True)
    worktree = info.jw.path
    requests, before = _load_requests(worktree, chapter_id)

    if not isinstance(index, int) or isinstance(index, bool) or not 0 <= index < len(requests):
        raise ChapterError(f"request index {index!r} is out of range (0..{len(requests) - 1})")
    if requests[index].get("type") != "request":
        raise ChapterError(f"requests[{index}] is not a request")
    if index not in unresolved_requests(requests):
        raise ChapterError(f"requests[{index}] is already resolved")

    actual_job_id = job_id or next_job_id(settings, work.work_key)
    jdir = jobs_dir(settings, work.work_key)
    jdir.mkdir(parents=True, exist_ok=True)
    record = new_job_record(actual_job_id, "resolve_request", chapter_id=chapter_id)
    record["branch"] = info.branch
    record["worktree"] = str(worktree)
    record["base_commit"] = info.jw.base_commit
    save_job_record(jdir, record)
    record = apply_transition(record, JobEvent.LOCK_ACQUIRED)
    record = apply_transition(record, JobEvent.CLI_EXITED)
    save_job_record(jdir, record)

    rel = _requests_rel(chapter_id)
    try:
        resolution = {
            "type": "resolution",
            "request_index": index,
            "decision": decision,
            "resolved_at": now_iso(),
        }
        expected = requests + [resolution]
        validate_or_raise(expected, "requests")
        sem = check_requests(expected)
        if sem:
            raise ChapterError(f"invalid requests after the append: {sem}")
        sep = b"" if before.endswith(b"\n") else b"\n"
        after = before + sep + dumps_yaml([resolution]).encode("utf-8")
        append_chk = check_append_only(before, after)
        if append_chk.status != "PASS":
            raise ChapterError(f"requests.yaml change is not append only: {append_chk.details}")

        (worktree / rel).write_bytes(after)
        if load_yaml(worktree / rel) != expected:
            raise ChapterError("requests.yaml does not hold the expected content after the append")
        allowed = check_allowed_paths(get_changes(worktree), {rel})
        if allowed.status != "PASS":
            raise ChapterError(f"Changes outside allowed paths: {list(allowed.details)}")
        commit = commit_all(
            worktree,
            f"resolve request {index} of {chapter_id}",
            [("NovUI-Job", actual_job_id)],
            name=settings.git_name,
            email=settings.git_email,
        )
        if commit is None:
            raise ChapterError(f"No changes to commit for {rel}")
    except BaseException:
        _restore_paths(worktree, [rel])
        record = apply_transition(record, JobEvent.CHECK_FAILED)
        save_job_record(jdir, record)
        raise

    record = apply_transition(
        record, JobEvent.CHECK_PASSED, needs_validation=False, reason=f"request {index}: {decision}"
    )
    save_job_record(jdir, record)
    return record


def _preflight(
    settings: Settings, work: WorkInfo, chapter_id: str, info: CycleInfo
) -> tuple[list[dict[str, Any]], bytes]:
    """Checks of the redraft that must pass before anything is removed. Returns (requests, raw requests.yaml)."""
    meta = _read_branch_chapter_meta(work.path, info.branch, chapter_id)
    if meta["state"] != ChapterState.PLAN_APPROVED.name:
        raise ChapterError(
            f"redraft is only for a draft that stopped at 【要確認】 (chapter PLAN_APPROVED on {info.branch}), "
            f"got: {meta['state']}"
        )
    if info.draft_record["state"] != JobState.WAITING_HUMAN.name:
        raise ChapterError(f"draft job {info.draft_job_id} is {info.draft_record['state']}, not WAITING_HUMAN")
    requests, raw = _load_requests(info.jw.path, chapter_id)
    foreign = sorted({r["job_id"] for r in requests if r.get("type") == "request" and r["job_id"] != info.draft_job_id})
    if foreign:
        raise ChapterError(f"requests of other jobs than {info.draft_job_id}: {foreign}")
    pending = unresolved_requests(requests)
    if pending:
        raise ChapterError(f"unresolved requests remain: {pending}; run resolve-request first")
    return requests, raw


def redraft(
    settings: Settings,
    work: WorkInfo,
    chapter_id: str,
    *,
    run_lock: RunLock | None = None,
    job_id: str | None = None,
    container_runner: ContainerRunner = run_container,
) -> dict[str, Any]:
    """Discard the job branch of a draft that stopped at 【要確認】 and run the draft Job again with the decisions.

    Order (phase2c-design §7.4, Human decision D1): everything that can fail is checked first (main, plan, model,
    context, instruction), the old requests.yaml is archived, and only then the old branch and worktree are removed.
    A failure before the removal leaves the old branch, worktree and requests untouched.
    """
    repo = work.path
    ensure_main_ready(repo)
    info = cycle_info(settings, work, chapter_id, require_worktree=True)
    requests, raw = _preflight(settings, work, chapter_id, info)

    main_meta = read_chapter_meta(repo, chapter_id)
    if main_meta is None or main_meta["state"] != ChapterState.PLAN_APPROVED.name:
        cur = main_meta["state"] if main_meta else "NONE"
        raise ChapterError(f"Chapter {chapter_id!r} must be PLAN_APPROVED on main to redraft, got: {cur}")
    plan = read_plan(repo, chapter_id)
    ctx_paths = tuple(collect_draft_context(repo, chapter_id, plan))
    instruction = build_draft_instruction(chapter_id, plan) + "\n\n" + build_decision_section(requests)
    inputs = load_mechanical_inputs(repo)
    try:
        model = resolve_model(settings, DRAFT_MODEL_ROLE, work_key=work.work_key)
    except ModelUnavailable as exc:
        raise ChapterError(f"draft model is not available: {exc}") from exc
    actual_job_id = job_id or next_job_id(settings, work.work_key)

    # archive the old requests (request and resolution entries) in Controller data
    log_dir = jobs_dir(settings, work.work_key) / f"{info.draft_job_id}-logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    archive = log_dir / ARCHIVE_NAME
    try:
        with open(archive, "xb") as f:
            f.write(raw)
    except FileExistsError:
        raise ChapterError(f"archive already exists: {archive}") from None

    try:
        remove_cycle(settings, work, chapter_id, cancel_reason=f"redrafted by {actual_job_id}", info=info)
    except BaseException:
        if chapter_branches(repo, chapter_id):  # nothing was removed: the archive is not needed yet
            archive.unlink(missing_ok=True)
        raise

    req = DraftJobRequest(
        work_key=work.work_key,
        repo=repo,
        chapter_id=chapter_id,
        job_id=actual_job_id,
        model=model,
        context_paths=ctx_paths,
        instruction=instruction,
        target_chars=(plan["target_chars"]["min"], plan["target_chars"]["max"]),
        needs_validation=False,
        extra_checks=lambda text: run_draft_mechanical_checks(text, plan, inputs),
    )
    record = run_draft_job(
        settings,
        req,
        run_lock if run_lock is not None else RunLock(),
        container_runner=container_runner,
    )
    if record["state"] == JobState.COMPLETED.name:
        _mark_drafted_on_branch(settings, Path(record["worktree"]), chapter_id, actual_job_id)
    return record
