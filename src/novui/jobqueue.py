"""Job queue of a work and range_edit from validation findings (phase2d5-design §7; spec §11.5, §13.4, §18.2).

The queue holds Jobs the Human approved, in order. `run_queue` runs them one at a time and stops where a Human
decision is needed; there is no resident process (decision 4). One run at a time per work (a file lock).
"""

from __future__ import annotations

from contextlib import contextmanager
import fcntl
from pathlib import Path
import re
from typing import Any, Callable, Iterator, Mapping, Sequence

from novui.chapters import ChapterError, ensure_main_ready, read_chapter_meta
from novui.claude_cli import run_claude
from novui.claudejob import ClaudeRunner
from novui.config import Settings
from novui.container import run_container
from novui.draftjob import run_chapter_draft_job
from novui.editjob import run_chapter_rewrite_job, run_range_edit_job
from novui.finalize import cycle_info
from novui.jobrecord import load_job_record, now_iso, save_job_record
from novui.jobrunner import ContainerRunner, jobs_dir
from novui.locks import RunLock
from novui.planjob import run_plan_revision_job
from novui.schema import validate_or_raise
from novui.states import ChapterState, JobState
from novui.validatejob import _read_branch_chapter_meta, _read_branch_yaml
from novui.works import WorkInfo
from novui.yamlio import load_yaml, write_yaml_atomic

QUEUE_JOB_TYPES: tuple[str, ...] = ("range_edit", "chapter_rewrite", "plan_revision", "draft")
INTEGRITY_ORDER: tuple[str, ...] = (
    "character", "world", "timeline", "plot", "foreshadowing", "plan_compliance", "undefined_setting",
)
FIX_ROUND_LIMIT = 2  # §11.5：文章レビューから range_edit への変換は、同じ章で連続2回まで
_ITEM_RE = re.compile(r"^Q-([0-9]{4,})$")


class QueueBusy(Exception):
    """Another run-queue (or edit command) holds the queue lock of the work."""


def _work_dir(settings: Settings, work_key: str) -> Path:
    return settings.data_dir / "works" / work_key


def queue_path(settings: Settings, work_key: str) -> Path:
    return _work_dir(settings, work_key) / "queue.yaml"


@contextmanager
def queue_lock(settings: Settings, work_key: str) -> Iterator[None]:
    """Exclusive lock of the work's queue (fcntl.flock on queue.lock). QueueBusy when another process holds it."""
    path = _work_dir(settings, work_key) / "queue.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a+b") as f:
        try:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise QueueBusy(f"another run-queue or edit is running for work {work_key}") from None
        try:
            yield
        finally:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)


def load_queue(settings: Settings, work_key: str) -> list[dict[str, Any]]:
    path = queue_path(settings, work_key)
    if not path.is_file():
        return []
    doc = load_yaml(path)
    validate_or_raise(doc, "queue")
    return list(doc["items"])


def save_queue(settings: Settings, work_key: str, items: Sequence[Mapping[str, Any]]) -> None:
    doc = {"items": [dict(i) for i in items]}
    validate_or_raise(doc, "queue")
    write_yaml_atomic(queue_path(settings, work_key), doc)


def _next_item_number(items: Sequence[Mapping[str, Any]]) -> int:
    nums = [int(m.group(1)) for i in items if (m := _ITEM_RE.match(i["item_id"]))]
    return max(nums, default=0) + 1


def new_item(
    job_type: str,
    chapter_id: str,
    *,
    instruction: str,
    source: Mapping[str, Any],
    anchor: Mapping[str, Any] | None = None,
    instruction_id: str | None = None,
) -> dict[str, Any]:
    """A queue item (item_id is given by enqueue)."""
    if job_type not in QUEUE_JOB_TYPES:
        raise ValueError(f"job type {job_type!r} cannot be queued")
    return {
        "item_id": None,
        "job_type": job_type,
        "chapter_id": chapter_id,
        "source": dict(source),
        "anchor": dict(anchor) if anchor is not None else None,
        "instruction": instruction,
        "instruction_id": instruction_id,
        "status": "pending",
        "job_id": None,
        "note": None,
        "added_at": now_iso(),
    }


def enqueue(settings: Settings, work_key: str, new_items: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Append items to the queue (item IDs Q-0001...). Returns the appended items."""
    items = load_queue(settings, work_key)
    n = _next_item_number(items)
    added = []
    for k, item in enumerate(new_items):
        added.append({**item, "item_id": f"Q-{n + k:04d}"})
    save_queue(settings, work_key, items + added)
    return added


def cancel_item(settings: Settings, work_key: str, item_id: str) -> dict[str, Any]:
    """Mark an item that is not done as cancelled. The Job of a waiting item is not changed (use cancel-job)."""
    with queue_lock(settings, work_key):
        items = load_queue(settings, work_key)
        for item in items:
            if item["item_id"] == item_id:
                if item["status"] in ("done", "cancelled", "running"):
                    raise ChapterError(f"{item_id} is {item['status']}; it cannot be cancelled")
                item["status"] = "cancelled"
                save_queue(settings, work_key, items)
                return item
    raise ChapterError(f"queue item not found: {item_id}")


# ---------- 検証の指摘 ----------

def numbered_findings(review: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """Findings of a review.yaml by number: I1... for integrity (in check order), W1... (Claude's IDs) for writing.

    Each value: {"kind", "anchor", "message", "suggestion", "severity", "check"}.
    """
    found: dict[str, dict[str, Any]] = {}
    integrity = review.get("integrity") or {}
    n = 0
    for check in INTEGRITY_ORDER:
        for f in ((integrity.get("checks") or {}).get(check) or {}).get("findings", []):
            n += 1
            found[f"I{n}"] = {"kind": "integrity", "anchor": f.get("anchor"), "message": f["message"],
                              "suggestion": None, "severity": f["severity"], "check": check}
    for f in (review.get("writing") or {}).get("findings", []):
        found[f["id"]] = {"kind": "writing", "anchor": f.get("anchor"), "message": f["message"],
                          "suggestion": f.get("suggestion"), "severity": None, "check": f["category"]}
    return found


def finding_reason(fid: str, finding: Mapping[str, Any], fix_reason: str | None, note: str | None) -> str:
    """The instruction of a range_edit made from a finding."""
    lines = [f"検証の指摘（{fid}）：{finding['message']}"]
    if finding.get("suggestion"):
        lines.append(f"提案：{finding['suggestion']}")
    if fix_reason:
        lines.append(f"Human の指示：{fix_reason}")
    if note:
        lines.append(f"Human の補足：{note}")
    return "\n".join(lines)


def _fix_rounds(settings: Settings, work: WorkInfo, chapter_id: str, branch: str) -> set[str]:
    """validate Job IDs whose findings were already queued for this job branch (one fix round each)."""
    rounds: set[str] = set()
    jdir = jobs_dir(settings, work.work_key)
    for item in load_queue(settings, work.work_key):
        src = item["source"]
        if item["chapter_id"] != chapter_id or src.get("kind") != "finding" or item["status"] == "cancelled":
            continue
        path = jdir / f"{src['review_job']}.yaml"
        if path.is_file() and load_job_record(path)["branch"] == branch:
            rounds.add(src["review_job"])
    return rounds


def fix_findings(
    settings: Settings,
    work: WorkInfo,
    chapter_id: str,
    finding_ids: Sequence[str],
    *,
    note: str | None = None,
    force: bool = False,
) -> list[dict[str, Any]]:
    """Queue one range_edit per selected finding of the latest review on the job branch (decision 1).

    Allowed when the review's decision is REQUEST_FIX (chapter DRAFTED) or the chapter is AI_VALIDATED (adopting
    writing findings). Findings without an anchor cannot be chosen. From the third round on this branch, force is
    required (§11.5). Returns the queued items.
    """
    if not finding_ids:
        raise ValueError("choose at least one finding")
    ensure_main_ready(work.path)
    info = cycle_info(settings, work, chapter_id, require_worktree=True)
    meta = _read_branch_chapter_meta(work.path, info.branch, chapter_id)
    review = _read_branch_yaml(work.path, info.branch, f"chapters/{chapter_id}/review.yaml")
    if not isinstance(review, dict):
        raise ChapterError(f"no review.yaml on {info.branch}; run validate first")
    decision = review.get("decision") or {}
    if not (decision.get("action") == "REQUEST_FIX" and meta["state"] == ChapterState.DRAFTED.name) and \
            meta["state"] != ChapterState.AI_VALIDATED.name:
        raise ChapterError(
            "fix needs a review decided REQUEST_FIX (chapter DRAFTED) or an AI_VALIDATED chapter, "
            f"got state {meta['state']} and decision {decision.get('action')}"
        )
    findings = numbered_findings(review)
    chosen: list[tuple[str, dict[str, Any]]] = []
    for fid in dict.fromkeys(finding_ids):
        if fid not in findings:
            raise ChapterError(f"finding {fid} not found in the review (have: {sorted(findings)})")
        if findings[fid]["anchor"] is None:
            raise ChapterError(f"finding {fid} has no anchor; use instruct --scope chapter for it")
        chosen.append((fid, findings[fid]))

    review_job = review["job_id"]
    with queue_lock(settings, work.work_key):
        rounds = _fix_rounds(settings, work, chapter_id, info.branch)
        if review_job not in rounds and len(rounds) >= FIX_ROUND_LIMIT and not force:
            raise ChapterError(
                f"{len(rounds)} fix rounds were already made on {info.branch} (limit {FIX_ROUND_LIMIT}, §11.5); "
                "use --force to continue"
            )
        fix_reason = decision.get("reason") if decision.get("action") == "REQUEST_FIX" else None
        items = [
            new_item("range_edit", chapter_id,
                     instruction=finding_reason(fid, f, fix_reason, note),
                     source={"kind": "finding", "review_job": review_job, "finding": fid},
                     anchor=f["anchor"])
            for fid, f in chosen
        ]
        return enqueue(settings, work.work_key, items)


# ---------- 実行 ----------

def _run_item(
    settings: Settings,
    work: WorkInfo,
    item: Mapping[str, Any],
    *,
    retry_job: str | None,
    run_lock: RunLock,
    container_runner: ContainerRunner,
    claude_runner: ClaudeRunner,
) -> dict[str, Any]:
    ch, jt = item["chapter_id"], item["job_type"]
    common = {"instruction_id": item["instruction_id"], "run_lock": run_lock, "container_runner": container_runner}
    if jt == "range_edit":
        return run_range_edit_job(settings, work, ch, anchor=item["anchor"], reason=item["instruction"],
                                  source=item["source"], retry_of=retry_job, **common)
    if jt == "chapter_rewrite":
        return run_chapter_rewrite_job(settings, work, ch, reason=item["instruction"], source=item["source"],
                                       retry_of=retry_job, **common)
    if jt == "plan_revision":
        return run_plan_revision_job(settings, work, ch, item["instruction"], source=item["source"],
                                     instruction_id=item["instruction_id"], claude_runner=claude_runner)
    record = run_chapter_draft_job(settings, work, ch, run_lock=run_lock, container_runner=container_runner)
    if item["instruction_id"] is not None:
        record["instruction_id"] = item["instruction_id"]
        save_job_record(jobs_dir(settings, work.work_key), record)
    return record


_RESULT_STATUS = {
    JobState.COMPLETED.name: "done",
    JobState.WAITING_HUMAN.name: "waiting",
    JobState.FAILED.name: "failed",
}


def run_queue(
    settings: Settings,
    work: WorkInfo,
    *,
    retry: bool = False,
    run_lock: RunLock | None = None,
    container_runner: ContainerRunner = run_container,
    claude_runner: ClaudeRunner = run_claude,
    report: Callable[[str], None] = lambda _msg: None,
) -> list[dict[str, Any]]:
    """Run the queue from its first open item until a Human is needed (phase2d5-design §7.1).

    A waiting or failed item at the head stops the run unless retry (it runs again; a waiting edit Job is retried
    with its Human decisions). A draft item waits while its chapter is not PLAN_APPROVED. A ChapterError leaves the
    item as it was and stops. Returns the Job records that were run.
    """
    lock = run_lock if run_lock is not None else RunLock()
    records: list[dict[str, Any]] = []
    with queue_lock(settings, work.work_key):
        while True:
            items = load_queue(settings, work.work_key)
            head = next((i for i in items if i["status"] not in ("done", "cancelled")), None)
            if head is None:
                report("The queue is empty.")
                return records
            retry_job: str | None = None
            if head["status"] == "running":
                head["status"], head["note"] = "failed", "interrupted (the previous run-queue stopped)"
                save_queue(settings, work.work_key, items)
            if head["status"] in ("waiting", "failed"):
                if not retry:
                    report(f"{head['item_id']} ({head['job_type']} {head['chapter_id']}) is {head['status']}: "
                           f"{head['note'] or ''} — decide, then run-queue --retry, or queue --cancel {head['item_id']}")
                    return records
                retry = False  # 先頭の1件だけをやり直す
                if head["status"] == "waiting" and head["job_id"] and head["job_type"] in ("range_edit", "chapter_rewrite"):
                    rec_path = jobs_dir(settings, work.work_key) / f"{head['job_id']}.yaml"
                    if rec_path.is_file() and load_job_record(rec_path)["state"] == JobState.WAITING_HUMAN.name:
                        retry_job = head["job_id"]
            if head["job_type"] == "draft":
                meta = read_chapter_meta(work.path, head["chapter_id"])
                if meta is None or meta["state"] != ChapterState.PLAN_APPROVED.name:
                    report(f"{head['item_id']} (draft {head['chapter_id']}) waits for approve-plan "
                           f"(chapter is {meta['state'] if meta else 'missing'}).")
                    return records

            before = head["status"]
            head["status"], head["note"] = "running", None
            save_queue(settings, work.work_key, items)
            try:
                record = _run_item(settings, work, head, retry_job=retry_job, run_lock=lock,
                                   container_runner=container_runner, claude_runner=claude_runner)
            except (ChapterError, ValueError) as exc:
                head["status"], head["note"] = before, f"not run: {exc}"
                save_queue(settings, work.work_key, items)
                report(f"{head['item_id']} ({head['job_type']} {head['chapter_id']}) was not run: {exc}")
                return records
            except BaseException:
                head["status"], head["note"] = "failed", "error while running (see the Job record)"
                save_queue(settings, work.work_key, items)
                raise
            records.append(record)
            head["job_id"] = record["job_id"]
            head["status"] = _RESULT_STATUS.get(record["state"], "failed")
            history = record.get("history") or [{}]
            head["note"] = history[-1].get("reason") if head["status"] != "done" else None
            save_queue(settings, work.work_key, items)
            report(f"{head['item_id']} ({head['job_type']} {head['chapter_id']}): Job {record['job_id']} "
                   f"{record['state']}")
            if head["status"] != "done":
                return records
