"""instruction_routing: Human instruction -> Claude's Job proposal -> Human approval -> queue (phase2d5-design §6;
spec §13.1, §13.3, §13.4).

Claude does not carry out the instruction. Its proposal is kept in Controller data (routings/INS-0001.yaml) until the
Human approves it (its Jobs go to the queue) or rejects it (the file is removed, §13.3).
"""

from __future__ import annotations

from pathlib import Path
import re
from typing import Any, Mapping, Sequence

from novui.chapters import ChapterError, ensure_main_ready, read_chapter_meta
from novui.claude_cli import run_claude
from novui.claudejob import ClaudeJobRequest, ClaudeRunner, run_claude_job
from novui.config import Settings
from novui.editjob import normalize_anchor
from novui.finalize import cycle_info, discard_cycle
from novui.ids import next_job_id
from novui.jobqueue import enqueue, new_item, queue_lock
from novui.jobrecord import apply_transition, load_job_record, now_iso, save_job_record
from novui.jobrunner import jobs_dir
from novui.planjob import collect_plan_context
from novui.prompt import load_prompt_template
from novui.states import JobEvent, JobState
from novui.validatejob import _read_branch_chapter_meta
from novui.workrepo import chapter_branches, head_commit
from novui.works import WorkInfo
from novui.yamlio import load_yaml, write_yaml_atomic

ROUTING_SCOPES: tuple[str, ...] = ("range", "chapter", "plan")  # Phase 2 で受け付ける scope（§6.1）
ROUTABLE_JOBS: tuple[str, ...] = ("range_edit", "chapter_rewrite", "plan_revision")
_INS_RE = re.compile(r"^INS-([0-9]{4,})$")


def routings_dir(settings: Settings, work_key: str) -> Path:
    return settings.data_dir / "works" / work_key / "routings"


def _routing_path(settings: Settings, work_key: str, instruction_id: str) -> Path:
    if not _INS_RE.match(instruction_id):
        raise ValueError(f"invalid instruction id {instruction_id!r}")
    return routings_dir(settings, work_key) / f"{instruction_id}.yaml"


def next_instruction_id(settings: Settings, work_key: str) -> str:
    """INS-0001... from a counter file, so that the IDs of removed (rejected) routings are not reused."""
    counter = routings_dir(settings, work_key) / "next-id"
    counter.parent.mkdir(parents=True, exist_ok=True)
    n = int(counter.read_text(encoding="utf-8").strip()) if counter.is_file() else 1
    counter.write_text(f"{n + 1}\n", encoding="utf-8")
    return f"INS-{n:04d}"


def load_routing(settings: Settings, work_key: str, instruction_id: str) -> dict[str, Any]:
    path = _routing_path(settings, work_key, instruction_id)
    if not path.is_file():
        raise ChapterError(f"routing not found: {instruction_id}")
    doc = load_yaml(path)
    if not isinstance(doc, dict):
        raise ChapterError(f"routing {instruction_id} is not a mapping")
    return doc


def _save_routing(settings: Settings, work_key: str, doc: Mapping[str, Any]) -> None:
    write_yaml_atomic(_routing_path(settings, work_key, doc["instruction_id"]), dict(doc))


def check_proposed_jobs(jobs: Sequence[Mapping[str, Any]]) -> list[str]:
    """Problems in Claude's proposed_jobs that the schema and check_instruction_routing do not catch."""
    problems: list[str] = []
    for i, job in enumerate(jobs):
        jt = job["job_type"]
        if jt not in ROUTABLE_JOBS:
            problems.append(f"proposed_jobs[{i}]: {jt} cannot be proposed (use {', '.join(ROUTABLE_JOBS)})")
        if jt == "range_edit" and job.get("anchor") is None:
            problems.append(f"proposed_jobs[{i}]: range_edit needs an anchor")
        if jt != "range_edit" and job.get("anchor") is not None:
            problems.append(f"proposed_jobs[{i}]: only range_edit takes an anchor")
    return problems


def _cycle_state(settings: Settings, work: WorkInfo, chapter_id: str) -> tuple[Path, str, str, str]:
    """(root to read, chapter state, ref, commit). The job branch's worktree when there is one, else main."""
    repo = work.path
    meta = read_chapter_meta(repo, chapter_id)
    if meta is None:
        raise ChapterError(f"Chapter {chapter_id!r} not found")
    if chapter_branches(repo, chapter_id):
        info = cycle_info(settings, work, chapter_id, require_worktree=True)
        state = _read_branch_chapter_meta(repo, info.branch, chapter_id)["state"]
        return info.jw.path, state, info.branch, head_commit(repo, info.branch)
    return repo, meta["state"], "main", head_commit(repo, "main")


def run_routing_job(
    settings: Settings,
    work: WorkInfo,
    chapter_id: str,
    scope: str,
    instruction: str,
    *,
    anchor: Mapping[str, Any] | None = None,
    job_id: str | None = None,
    claude_runner: ClaudeRunner = run_claude,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Ask Claude to route a Human instruction (routing Job). Returns (job record, routing doc or None).

    The routing doc is saved with status `pending` (approvable) or `questions` (Claude asked; the Job is
    WAITING_HUMAN and the routing cannot be approved). Invalid proposals make the Job FAILED (nothing saved).
    """
    if scope not in ROUTING_SCOPES:
        raise ValueError(f"scope must be one of {ROUTING_SCOPES}, got {scope!r}")
    if not instruction or not instruction.strip():
        raise ValueError("instruction must not be empty")
    instruction = instruction.strip()
    if scope == "range":
        if anchor is None:
            raise ValueError("scope range needs an anchor")
        anchor = normalize_anchor(anchor)
    elif anchor is not None:
        raise ValueError("only scope range takes an anchor")
    ensure_main_ready(work.path)
    root, state, ref, commit = _cycle_state(settings, work, chapter_id)
    draft_rel = f"chapters/{chapter_id}/draft.md"
    has_draft = ref != "main" and (root / draft_rel).is_file()
    if scope == "range" and not has_draft:
        raise ChapterError(f"Chapter {chapter_id!r} has no draft on a job branch; a range cannot be chosen")

    ctx = list(collect_plan_context(root, chapter_id))
    for rel in (f"chapters/{chapter_id}/plan.yaml", draft_rel if has_draft else None):
        if rel is not None and (root / rel).is_file() and rel not in ctx:
            ctx.append(rel)

    instruction_id = next_instruction_id(settings, work.work_key)
    scope_detail = ""
    if anchor is not None:
        scope_detail = (
            "Human が選んだ範囲（本文の中の文字列）：\n\n```text\n" + anchor["text"] + "\n```\n"
            + (f"\n直前の文字列：{anchor['before']}" if anchor["before"] else "")
            + (f"\n直後の文字列：{anchor['after']}" if anchor["after"] else "")
        )
    task = load_prompt_template(
        "instruction_routing",
        chapter_id=chapter_id,
        instruction_id=instruction_id,
        scope=scope,
        instruction=instruction,
        scope_detail=scope_detail,
        chapter_state=f"{state}（本文{'あり' if has_draft else 'なし'}）",
        draft_note=(f"chapters/{chapter_id}/draft.md が今の本文です。" if has_draft else "この章にはまだ本文がありません。"),
    )
    actual_job_id = job_id or next_job_id(settings, work.work_key)
    req = ClaudeJobRequest(
        work_key=work.work_key,
        job_id=actual_job_id,
        job_type="routing",
        chapter_id=chapter_id,
        root=root,
        context_paths=tuple(ctx),
        task_text=task,
        expected_type="instruction_routing",
        model=settings.claude_model,
    )
    record, data = run_claude_job(settings, req, claude_runner=claude_runner)
    jdir = jobs_dir(settings, work.work_key)
    record["instruction_id"] = instruction_id
    save_job_record(jdir, record)
    if data is None:
        return record, None

    # Controller の値で上書きする（Claude が写し間違えても、記録は Human の指示のまま）
    data = {**data, "instruction_id": instruction_id, "scope": scope, "chapter_id": chapter_id,
            "instruction": instruction}
    problems = check_proposed_jobs(data["proposed_jobs"])
    if scope == "range" and not data["scope_mismatch"]:
        problems += [f"proposed_jobs[{i}]: scope range allows only range_edit (or set scope_mismatch)"
                     for i, j in enumerate(data["proposed_jobs"]) if j["job_type"] != "range_edit"]
    if not data["questions"] and not data["proposed_jobs"]:
        problems.append("neither proposed_jobs nor questions")
    record["checks"].append({"name": "routing_jobs", "status": "FAIL" if problems else "PASS", "details": problems})
    if problems:
        record = apply_transition(record, JobEvent.CHECK_FAILED, reason="routing_jobs check failed")
        save_job_record(jdir, record)
        return record, None

    doc = {
        "instruction_id": instruction_id,
        "chapter_id": chapter_id,
        "scope": scope,
        "instruction": instruction,
        "anchor": anchor,
        "status": "questions" if data["questions"] else "pending",
        "job_id": actual_job_id,
        "base": {"ref": ref, "commit": commit},
        "created_at": now_iso(),
        "routing": data,
    }
    _save_routing(settings, work.work_key, doc)
    if data["questions"]:
        record = apply_transition(record, JobEvent.NEEDS_HUMAN_INPUT, reason=f"questions: {len(data['questions'])}")
    else:
        record = apply_transition(record, JobEvent.CHECK_PASSED, needs_validation=False)
    save_job_record(jdir, record)
    return record, doc


def routing_items(doc: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Queue items of an approved routing. After a plan_revision, the first chapter_rewrite becomes a draft of the
    approved plan (decision 2); a range_edit after a plan_revision cannot run and is an error."""
    items: list[dict[str, Any]] = []
    revised = False
    rewritten = False
    ins = doc["instruction_id"]
    for i, job in enumerate(doc["routing"]["proposed_jobs"]):
        jt = job["job_type"]
        text = f"{job['instruction']}\n\nHuman の元の指示：{doc['instruction']}"
        source = {"kind": "instruction", "instruction_id": ins}
        if jt == "plan_revision":
            revised = True
            items.append(new_item("plan_revision", doc["chapter_id"], instruction=text, source=source,
                                  instruction_id=ins))
        elif revised and jt == "chapter_rewrite":
            if rewritten:
                continue
            rewritten = True
            items.append(new_item("draft", doc["chapter_id"], instruction="", source=source, instruction_id=ins))
        elif revised:
            raise ChapterError(f"proposed_jobs[{i}]: {jt} after plan_revision cannot run (the draft is discarded)")
        else:
            items.append(new_item(jt, doc["chapter_id"], instruction=text, source=source,
                                  anchor=job.get("anchor"), instruction_id=ins))
    return items


def _close_routing_job(settings: Settings, work: WorkInfo, doc: Mapping[str, Any], reason: str) -> None:
    path = jobs_dir(settings, work.work_key) / f"{doc['job_id']}.yaml"
    if path.is_file():
        record = load_job_record(path)
        if record["state"] == JobState.WAITING_HUMAN.name:
            save_job_record(jobs_dir(settings, work.work_key), apply_transition(record, JobEvent.HUMAN_CANCEL,
                                                                                 reason=reason))


def approve_routing(
    settings: Settings, work: WorkInfo, instruction_id: str, *, discard_branch: bool = False
) -> list[dict[str, Any]]:
    """Approve a pending routing: its Jobs go to the queue, in order (§6.3). Returns the queued items.

    Refused when the chapter changed since the routing (§13.4: instruct again). A routing with plan_revision on a
    chapter with a job branch needs discard_branch, and the branch is discarded here.
    """
    doc = load_routing(settings, work.work_key, instruction_id)
    if doc["status"] != "pending":
        raise ChapterError(f"{instruction_id} is {doc['status']}; only a pending routing can be approved")
    ensure_main_ready(work.path)
    chapter_id = doc["chapter_id"]
    _, _, ref, commit = _cycle_state(settings, work, chapter_id)
    if (ref, commit) != (doc["base"]["ref"], doc["base"]["commit"]):
        raise ChapterError(
            f"chapter {chapter_id} changed since {instruction_id} was routed ({doc['base']['ref']} "
            f"{doc['base']['commit'][:12]} -> {ref} {commit[:12]}); run instruct again"
        )
    items = routing_items(doc)
    with queue_lock(settings, work.work_key):
        if any(i["job_type"] == "plan_revision" for i in items) and ref != "main":
            if not discard_branch:
                raise ChapterError(
                    f"{instruction_id} revises the plan: the job branch {ref} has to be discarded; "
                    "approve with --discard-branch"
                )
            discard_cycle(settings, work, chapter_id)
        queued = enqueue(settings, work.work_key, items)
    doc = {**doc, "status": "approved", "approved_at": now_iso()}
    _save_routing(settings, work.work_key, doc)
    return queued


def reject_routing(settings: Settings, work: WorkInfo, instruction_id: str) -> None:
    """Discard a routing that is not approved: the file is removed (§13.3) and a waiting routing Job is cancelled."""
    doc = load_routing(settings, work.work_key, instruction_id)
    if doc["status"] == "approved":
        raise ChapterError(f"{instruction_id} is already approved; cancel its queue items instead")
    _close_routing_job(settings, work, doc, "routing rejected")
    _routing_path(settings, work.work_key, instruction_id).unlink()
