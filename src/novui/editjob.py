"""range_edit on the chapter's job branch (phase2d5-design §2, §4; spec §5.8, §8.3, §13.5).

The Controller finds the range by its anchor, keeps the prefix and the suffix, lets AGY write only the replacement
and writes prefix + replacement + suffix to draft.md. The job branch of the draft cycle is reused: no new branch.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from novui.agy_output import splice_range
from novui.anchor import AnchorError, find_anchor, split_by_range
from novui.chapters import ChapterError, ensure_main_ready, read_chapters_order
from novui.charrules import render_rule_tables, scene_rule_tables
from novui.flagstate import load_flag_states
from novui.checks import (
    CheckResult,
    check_allowed_paths,
    check_char_range,
    check_ignored_unchanged,
    check_prefix_suffix,
)
from novui.config import Settings
from novui.container import run_container
from novui.draftjob import DRAFT_MODEL_ROLE, DraftSpec, prepare_draft, read_plan, script_format_text
from novui.finalize import CycleInfo, cycle_info, waiting_jobs_on_branch
from novui.gitinspect import get_changes, get_ignored
from novui.ids import next_job_id
from novui.jobrecord import apply_transition, load_job_record, new_job_record, save_job_record
from novui.jobrunner import ContainerRunner, append_requests, fail_unexpected, jobs_dir, run_agy_in_worktree
from novui.locks import RunLock
from novui.mechanical import SCENE_MARKER_RE, _MARKER_LIKE_RE, load_mechanical_inputs
from novui.models import ModelUnavailable, resolve_model
from novui.prompt import load_prompt_template
from novui.proposals import find_proposals
from novui.requestflow import DECISION_HEADER, unresolved_requests
from novui.schema import SchemaError, validate_or_raise
from novui.semantics import check_chapter
from novui.states import ChapterEvent, ChapterState, JobEvent, JobState, next_chapter_state
from novui.textformat import read_text_format
from novui.validatejob import _check_draft_consistency, _read_branch_chapter_meta, _restore_paths
from novui.workrepo import commit_all, head_commit
from novui.works import WorkInfo
from novui.yamlio import load_yaml, write_yaml_atomic

EDITABLE_STATES: tuple[ChapterState, ...] = (ChapterState.DRAFTED, ChapterState.AI_VALIDATED)
CONTEXT_CHARS = 1500  # 範囲の前後に AGY へ渡す本文の最大字数（同じ場面の中だけ）
SCRIPT_RANGE_NOTE = "* 書き直す範囲は台本の形式です。出力の各行も、次の台本の形式に従ってください。"


@dataclass(frozen=True)
class EditTarget:
    """The job branch, its worktree and what an edit Job reads from it."""

    info: CycleInfo
    worktree: Path
    meta: dict[str, Any]
    draft_record: dict[str, Any]
    plan: dict[str, Any]
    requests: list[dict[str, Any]]


@dataclass(frozen=True)
class EditRange:
    """The range found by the anchor, in bytes and characters, and the scene around it."""

    start: int  # draft.md の中のバイト位置
    end: int
    scene_id: str
    range_text: str
    before_text: str
    after_text: str


def _rel(chapter_id: str, name: str) -> str:
    return f"chapters/{chapter_id}/{name}"


def load_edit_target(
    settings: Settings, work: WorkInfo, chapter_id: str, *, retry_of: str | None = None
) -> EditTarget:
    """Check the conditions of an edit Job (phase2d5-design §2.2) and read the job branch. ChapterError otherwise.

    retry_of: a WAITING_HUMAN edit Job on the branch that this Job runs again (it is the only waiting Job allowed).
    """
    ensure_main_ready(work.path)
    info = cycle_info(settings, work, chapter_id, require_worktree=True)
    meta = _read_branch_chapter_meta(work.path, info.branch, chapter_id)
    if ChapterState[meta["state"]] not in EDITABLE_STATES:
        raise ChapterError(
            f"Chapter {chapter_id!r} must be DRAFTED or AI_VALIDATED on {info.branch} to edit, got: {meta['state']}"
        )
    waiting = [r["job_id"] for r in waiting_jobs_on_branch(settings, work, info.branch) if r["job_id"] != retry_of]
    if waiting:
        raise ChapterError(f"Jobs on {info.branch} are waiting for a Human decision: {waiting}")
    pending = [p["job_id"] for p in find_proposals(settings, work.work_key, chapter_id)
               if p["status"] == "pending" and p["branch"] == info.branch]
    if pending:
        raise ChapterError(f"state update proposal {pending} is pending; approve or reject it before editing")

    worktree = info.jw.path
    req_path = worktree / _rel(chapter_id, "requests.yaml")
    requests: list[dict[str, Any]] = []
    if req_path.is_file():
        requests = load_yaml(req_path) or []
        validate_or_raise(requests, "requests")
    open_reqs = unresolved_requests(requests)
    if open_reqs:
        raise ChapterError(f"requests {open_reqs} are not resolved; resolve them first (resolve-request)")

    draft_record, details = _check_draft_consistency(settings, work, chapter_id, info.branch)
    if details:
        raise ChapterError("draft job and its context do not match the job branch: " + "; ".join(details))
    assert draft_record is not None
    return EditTarget(info, worktree, meta, draft_record, read_plan(worktree, chapter_id), requests)


def _scene_spans(text: str) -> list[tuple[str, int, int]]:
    """(scene ID, first character after the marker line, character where the next marker line starts)."""
    spans: list[tuple[str, int, int]] = []
    pos = 0
    for line in text.splitlines(keepends=True):
        m = SCENE_MARKER_RE.match(line.rstrip("\r\n"))
        if m:
            if spans:
                spans[-1] = (spans[-1][0], spans[-1][1], pos)
            spans.append((m.group(1), pos + len(line), len(text)))
        pos += len(line)
    return spans


def normalize_anchor(anchor: Mapping[str, Any]) -> dict[str, str]:
    """The anchor with before / after ("" when missing). ValueError when text is empty."""
    text = anchor.get("text") or ""
    if not text:
        raise ValueError("anchor text must not be empty")
    return {"text": text, "before": anchor.get("before") or "", "after": anchor.get("after") or ""}


def locate_range(text: str, anchor: Mapping[str, Any]) -> EditRange:
    """Find the range of the anchor in text (exactly one match, §13.5) and cut the context around it.

    AnchorError when the anchor does not match exactly once. ChapterError when the range contains a scene marker
    line or is not inside one scene.
    """
    data = text.encode("utf-8")
    start, end = find_anchor(text, anchor["text"], anchor.get("before", ""), anchor.get("after", ""))
    c_start = len(data[:start].decode("utf-8"))
    c_end = len(data[:end].decode("utf-8"))
    range_text = text[c_start:c_end]
    if any(SCENE_MARKER_RE.match(line) or _MARKER_LIKE_RE.search(line) for line in range_text.splitlines()):
        raise ChapterError("the range contains a scene marker line; a range must stay inside one scene")
    for scene_id, s_start, s_end in _scene_spans(text):
        if s_start <= c_start and c_end <= s_end:
            return EditRange(
                start=start,
                end=end,
                scene_id=scene_id,
                range_text=range_text,
                before_text=text[max(s_start, c_start - CONTEXT_CHARS):c_start],
                after_text=text[c_end:min(s_end, c_end + CONTEXT_CHARS)],
            )
    raise ChapterError("the range is not inside a scene")


def decision_text(requests: Sequence[Mapping[str, Any]], job_id: str) -> str:
    """The Human decisions on the requests that job_id raised, in the redraft wording. '' when there are none."""
    decisions = {r["request_index"]: r["decision"] for r in requests if r.get("type") == "resolution"}
    lines: list[str] = []
    for i, r in enumerate(requests):
        if r.get("type") == "request" and r.get("job_id") == job_id and i in decisions:
            lines.append(f"* 確認事項：{r['message']}")
            lines.append(f"  判断：{decisions[i]}")
    return "\n".join([DECISION_HEADER, "", *lines]) if lines else ""


def build_range_edit_instruction(
    root: Path, chapter_id: str, plan: Mapping[str, Any], rng: EditRange, reason: str
) -> str:
    """AGY instruction of a range_edit from prompts/agy/range_edit.md (phase2d5-design §2.4)."""
    scene_plan = {**plan, "scenes": [s for s in plan["scenes"] if s["id"] == rng.scene_id]}
    inputs = load_mechanical_inputs(root)
    order = read_chapters_order(root)
    flag_states = load_flag_states(root, order, chapter_id=chapter_id, plan=plan)
    rules = render_rule_tables(scene_rule_tables(order, chapter_id, scene_plan, inputs.characters, flag_states))
    format_rules = ""
    if read_text_format(root) == "script":
        format_rules = SCRIPT_RANGE_NOTE + "\n\n" + script_format_text() + "\n"
    return load_prompt_template(
        "range_edit",
        group="agy",
        chapter_id=chapter_id,
        scene_id=rng.scene_id,
        reason=reason,
        before_text=rng.before_text.rstrip("\n") or "（場面の始まり）",
        range_text=rng.range_text.rstrip("\n"),
        after_text=rng.after_text.rstrip("\n") or "（場面の終わり）",
        character_rules=rules,
        format_rules=format_rules,
    ).rstrip("\n")


def _mark_edited(target: EditTarget, chapter_id: str, job_id: str) -> None:
    """chapter.yaml on the job branch: DRAFTED (§7.1 CONTENT_CHANGED) and last_job (written, not committed)."""
    meta = dict(target.meta)
    meta["state"] = next_chapter_state(
        ChapterState[meta["state"]], ChapterEvent.CONTENT_CHANGED, review_required=bool(meta["review_required"])
    ).name
    meta["last_job"] = job_id
    validate_or_raise(meta, "chapter")
    errs = check_chapter(meta)
    if errs:
        raise SchemaError(f"Semantic validation failed for chapter: {errs}", errors=errs)
    write_yaml_atomic(target.worktree / _rel(chapter_id, "chapter.yaml"), meta)


def _checks_dicts(checks: Sequence[CheckResult]) -> list[dict[str, Any]]:
    return [{"name": c.name, "status": c.status, "details": list(c.details)} for c in checks]


EDIT_JOB_TYPES: tuple[str, ...] = ("range_edit", "chapter_rewrite")


@dataclass(frozen=True)
class _EditPlan:
    """What one edit Job does: its instruction and how it writes the AGY output (None: wait for the Human)."""

    instruction: str
    edit: dict[str, Any]
    write: Callable[[str], list[CheckResult]] | None  # 出力の本文を draft.md に書き、検査結果を返す
    wait_reason: str | None
    timeout_key: str


def _load_retry(jdir: Path, retry_of: str | None, job_type: str, chapter_id: str) -> dict[str, Any] | None:
    if retry_of is None:
        return None
    path = jdir / f"{retry_of}.yaml"
    if not path.is_file():
        raise ChapterError(f"job record not found: {retry_of}")
    old = load_job_record(path)
    if old["job_type"] != job_type or old["chapter_id"] != chapter_id:
        raise ChapterError(f"{retry_of} is not a {job_type} of {chapter_id}")
    if old["state"] != JobState.WAITING_HUMAN.name:
        raise ChapterError(f"{retry_of} is {old['state']}, not WAITING_HUMAN")
    return old


def _full_reason(reason: str, target: EditTarget, old: Mapping[str, Any] | None) -> str:
    """The reason, plus the Human decisions on the requests of the retried Job."""
    text = reason.strip()
    if old is not None:
        decisions = decision_text(target.requests, old["job_id"])
        if decisions:
            text += "\n\n" + decisions
    return text


def _has_marker_line(text: str) -> bool:
    return any(SCENE_MARKER_RE.match(line) or _MARKER_LIKE_RE.search(line) for line in text.splitlines())


def _run_edit(
    settings: Settings,
    work: WorkInfo,
    chapter_id: str,
    *,
    job_type: str,
    reason: str,
    retry_of: str | None,
    instruction_id: str | None,
    job_id: str | None,
    run_lock: RunLock | None,
    container_runner: ContainerRunner,
    make_plan: Callable[[EditTarget, DraftSpec, str, dict[str, Any] | None], _EditPlan],
) -> dict[str, Any]:
    """Common lifecycle of range_edit and chapter_rewrite (phase2d5-design §2〜§4)."""
    if not reason or not reason.strip():
        raise ValueError("reason must not be empty")
    jdir = jobs_dir(settings, work.work_key)
    old = _load_retry(jdir, retry_of, job_type, chapter_id)
    target = load_edit_target(settings, work, chapter_id, retry_of=retry_of)
    if old is not None and old["branch"] != target.info.branch:
        raise ChapterError(f"{retry_of} is on {old['branch']}, not on {target.info.branch}")
    worktree = target.worktree
    text = (worktree / _rel(chapter_id, "draft.md")).read_text(encoding="utf-8")
    spec = prepare_draft(worktree, chapter_id, target.plan)
    plan = make_plan(target, spec, text, old)  # 指定の誤りは、Job を作る前に ChapterError

    actual_job_id = job_id or next_job_id(settings, work.work_key)
    jdir.mkdir(parents=True, exist_ok=True)
    lock = run_lock if run_lock is not None else RunLock()
    if not lock.acquire(work.work_key, actual_job_id):
        raise ChapterError(f"Run lock for work {work.work_key} is busy")
    try:
        record = new_job_record(actual_job_id, job_type, chapter_id=chapter_id)
        record["branch"] = target.info.branch
        record["worktree"] = str(worktree)
        record["base_commit"] = head_commit(worktree, "HEAD")
        record["instruction_id"] = instruction_id
        record["edit"] = plan.edit
        save_job_record(jdir, record)
        if old is not None:
            save_job_record(jdir, apply_transition(old, JobEvent.HUMAN_CANCEL, reason=f"retried as {actual_job_id}"))

        if plan.write is None:
            record = apply_transition(record, JobEvent.NEEDS_HUMAN_INPUT, reason=plan.wait_reason)
            save_job_record(jdir, record)
            return record
        try:
            model = resolve_model(settings, DRAFT_MODEL_ROLE, work_key=work.work_key)
        except ModelUnavailable as exc:
            record = apply_transition(record, JobEvent.NEEDS_HUMAN_INPUT, reason=f"model_unavailable: {exc}")
            save_job_record(jdir, record)
            return record
        return _execute(settings, work, chapter_id, target, plan, record, jdir, model, container_runner)
    finally:
        lock.release(work.work_key, actual_job_id)


def _execute(
    settings: Settings,
    work: WorkInfo,
    chapter_id: str,
    target: EditTarget,
    plan: _EditPlan,
    record: dict[str, Any],
    jdir: Path,
    model: str,
    container_runner: ContainerRunner,
) -> dict[str, Any]:
    job_id, job_type = record["job_id"], record["job_type"]
    worktree = target.worktree
    assert plan.write is not None
    try:
        run = run_agy_in_worktree(
            settings,
            record=record,
            jdir=jdir,
            work_key=work.work_key,
            worktree=worktree,
            model=model,
            context_paths=[e["path"] for e in target.draft_record["context"]],
            instruction=plan.instruction,
            timeout_key=plan.timeout_key,
            container_runner=container_runner,
        )
        record = run.record
        if run.stopped is not None:
            return record
        checks, parsed = run.checks, run.parsed
        written: list[str] = []

        if all(c.status == "PASS" for c in checks) and parsed is not None:
            if parsed.markers:
                # 【要確認】：本文は変えず、要求だけを追記する（phase2d5-design §4）
                checks.append(append_requests(worktree, chapter_id, job_id, parsed.markers))
                written.append(_rel(chapter_id, "requests.yaml"))
            else:
                written.append(_rel(chapter_id, "draft.md"))
                checks.extend(plan.write(parsed.text))

        checks.append(check_allowed_paths(get_changes(worktree), set(written)))
        checks.append(check_ignored_unchanged(run.pre_ignored, get_ignored(worktree)))
        record["checks"] = _checks_dicts(checks)
        save_job_record(jdir, record)

        if any(c.status == "FAIL" for c in checks):
            _restore_paths(worktree, written)
            record = apply_transition(record, JobEvent.CHECK_FAILED)
            save_job_record(jdir, record)
            return record

        if parsed is not None and parsed.markers:
            commit_all(worktree, f"{job_type} {chapter_id} requests by {job_id}",
                       [("NovUI-Job", job_id)], name=settings.git_name, email=settings.git_email)
            record = apply_transition(record, JobEvent.NEEDS_HUMAN_INPUT,
                                      reason=f"undefined_settings: {len(parsed.markers)}")
            save_job_record(jdir, record)
            return record

        _mark_edited(target, chapter_id, job_id)
        commit_all(worktree, f"{job_type} {chapter_id} by {job_id}",
                   [("NovUI-Job", job_id)], name=settings.git_name, email=settings.git_email)
        record = apply_transition(record, JobEvent.CHECK_PASSED, needs_validation=False)
        save_job_record(jdir, record)
        return record
    except BaseException:
        fail_unexpected(jdir, job_id)
        raise


def run_range_edit_job(
    settings: Settings,
    work: WorkInfo,
    chapter_id: str,
    *,
    anchor: Mapping[str, Any],
    reason: str,
    source: Mapping[str, Any],
    instruction_id: str | None = None,
    retry_of: str | None = None,
    job_id: str | None = None,
    run_lock: RunLock | None = None,
    container_runner: ContainerRunner = run_container,
) -> dict[str, Any]:
    """Run a range_edit Job on the chapter's job branch (phase2d5-design §2).

    COMPLETED: draft.md has the replacement and chapter.yaml is DRAFTED (one commit). WAITING_HUMAN: the anchor did
    not match exactly once (nothing changed), or the output has 【要確認】 (only requests.yaml is committed; draft.md is
    unchanged). FAILED: a check failed (nothing is committed). retry_of runs a WAITING_HUMAN range_edit again with the
    Human decisions on its requests; that Job is closed (CANCELLED, "retried as <job>").
    Raises ChapterError when the conditions are not met (no Job record is made).
    """
    anchor = normalize_anchor(anchor)

    def make_plan(target: EditTarget, spec: DraftSpec, text: str, old: dict[str, Any] | None) -> _EditPlan:
        # 範囲の誤り（区切りを含む、場面の外）は ChapterError。一致の件数の誤りは WAITING_HUMAN（§13.5）
        edit = {"anchor": dict(anchor), "reason": reason.strip(), "source": dict(source), "range_bytes": None}
        try:
            rng = locate_range(text, anchor)
        except AnchorError as exc:
            return _EditPlan("", edit, None, f"anchor_not_unique: {exc.count}", "agy_range_edit")
        edit["range_bytes"] = [rng.start, rng.end]
        instruction = build_range_edit_instruction(
            target.worktree, chapter_id, target.plan, rng, _full_reason(reason, target, old)
        )
        draft_path = target.worktree / _rel(chapter_id, "draft.md")

        def write(out: str) -> list[CheckResult]:
            if _has_marker_line(out):
                return [CheckResult(name="range_scene_marker", status="FAIL",
                                    details=("the replacement contains a scene marker line",))]
            data = draft_path.read_bytes()
            prefix, suffix = split_by_range(data, rng.start, rng.end)
            new_data = splice_range(prefix, data[rng.start:rng.end], out, suffix)
            draft_path.write_bytes(new_data)
            return [check_prefix_suffix(new_data, prefix, suffix), *spec.extra_checks(new_data.decode("utf-8"))]

        return _EditPlan(instruction, edit, write, None, "agy_range_edit")

    return _run_edit(
        settings, work, chapter_id, job_type="range_edit", reason=reason, retry_of=retry_of,
        instruction_id=instruction_id, job_id=job_id, run_lock=run_lock, container_runner=container_runner,
        make_plan=make_plan,
    )


def build_rewrite_instruction(spec: DraftSpec, current_text: str, reason: str) -> str:
    """AGY instruction of a chapter_rewrite: the draft instruction + prompts/agy/chapter_rewrite.md (§3)."""
    section = load_prompt_template(
        "chapter_rewrite", group="agy", reason=reason, current_text=current_text.rstrip("\n")
    ).rstrip("\n")
    return spec.instruction + "\n\n" + section


def run_chapter_rewrite_job(
    settings: Settings,
    work: WorkInfo,
    chapter_id: str,
    *,
    reason: str,
    source: Mapping[str, Any],
    instruction_id: str | None = None,
    retry_of: str | None = None,
    job_id: str | None = None,
    run_lock: RunLock | None = None,
    container_runner: ContainerRunner = run_container,
) -> dict[str, Any]:
    """Run a chapter_rewrite Job on the chapter's job branch (phase2d5-design §3): AGY writes the whole chapter again
    from the current text and the reason, under the same plan. Outcomes are those of run_range_edit_job; the checks
    are those of a draft Job."""

    def make_plan(target: EditTarget, spec: DraftSpec, text: str, old: dict[str, Any] | None) -> _EditPlan:
        edit = {"anchor": None, "reason": reason.strip(), "source": dict(source), "range_bytes": None}
        instruction = build_rewrite_instruction(spec, text, _full_reason(reason, target, old))
        draft_path = target.worktree / _rel(chapter_id, "draft.md")

        def write(out: str) -> list[CheckResult]:
            draft_path.write_text(out, encoding="utf-8")
            checks: list[CheckResult] = []
            if spec.target_chars is not None:
                checks.append(check_char_range(out, *spec.target_chars))
            return checks + list(spec.extra_checks(out))

        return _EditPlan(instruction, edit, write, None, "agy_draft")

    return _run_edit(
        settings, work, chapter_id, job_type="chapter_rewrite", reason=reason, retry_of=retry_of,
        instruction_id=instruction_id, job_id=job_id, run_lock=run_lock, container_runner=container_runner,
        make_plan=make_plan,
    )


def cancel_edit_job(settings: Settings, work: WorkInfo, job_id: str, reason: str = "cancelled by Human") -> dict[str, Any]:
    """Close a WAITING_HUMAN edit Job (CANCELLED). Its requests stay in requests.yaml and still need decisions."""
    path = jobs_dir(settings, work.work_key) / f"{job_id}.yaml"
    if not path.is_file():
        raise ChapterError(f"job record not found: {job_id}")
    record = load_job_record(path)
    if record["job_type"] not in EDIT_JOB_TYPES:
        raise ChapterError(f"{job_id} is a {record['job_type']} Job; only edit Jobs (range_edit, chapter_rewrite) are cancelled here")
    if record["state"] != JobState.WAITING_HUMAN.name:
        raise ChapterError(f"{job_id} is {record['state']}, not WAITING_HUMAN")
    record = apply_transition(record, JobEvent.HUMAN_CANCEL, reason=reason)
    save_job_record(jobs_dir(settings, work.work_key), record)
    return record
