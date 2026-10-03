"""Plan job execution and lifecycle management."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from novui.chapters import (
    ChapterError,
    ensure_main_ready,
    read_chapter_meta,
    read_chapters_order,
    transition_on_main,
)
from novui.claude_cli import run_claude
from novui.claudejob import ClaudeJobRequest, ClaudeRunner, run_claude_job
from novui.config import Settings
from novui.ids import next_job_id
from novui.jobrecord import apply_transition, save_job_record
from novui.jobrunner import jobs_dir
from novui.locks import can_accept_write_job
from novui.prompt import load_prompt_template
from novui.states import ChapterEvent, ChapterState, JobEvent
from novui.works import WorkInfo
from novui.yamlio import dumps_yaml, load_yaml


def collect_plan_context(repo: Path, chapter_id: str) -> list[str]:
    """Collect context file relative paths for plan generation in strict priority order."""
    candidates: list[str] = [
        "project.yaml",
        "rules/style.md",
        "rules/prohibited.yaml",
        "chapters-order.yaml",
    ]

    # world/ 以下の全ファイル（昇順）
    world_dir = repo / "world"
    if world_dir.is_dir():
        for p in sorted(world_dir.rglob("*")):
            if p.is_file():
                candidates.append(str(p.relative_to(repo)))

    # characters/ の *.yaml（昇順）
    chars_dir = repo / "characters"
    if chars_dir.is_dir():
        for p in sorted(chars_dir.glob("*.yaml")):
            if p.is_file():
                candidates.append(str(p.relative_to(repo)))

    candidates.append("foreshadowing/registry.yaml")
    candidates.append("plot/timeline.yaml")

    # chapters-order.yaml で対象の章の直前にある章の summary.yaml（あれば）
    order_file = repo / "chapters-order.yaml"
    if order_file.is_file():
        try:
            order = read_chapters_order(repo)
            if chapter_id in order:
                idx = order.index(chapter_id)
                if idx > 0:
                    prev_id = order[idx - 1]
                    candidates.append(f"chapters/{prev_id}/summary.yaml")
        except Exception:
            pass

    # 最後に chapters/<id>/outline.md
    candidates.append(f"chapters/{chapter_id}/outline.md")

    # 存在するファイルだけを並べる
    result: list[str] = []
    for rel_path in candidates:
        if (repo / rel_path).is_file():
            result.append(rel_path)

    return result


def _check_plan_refs(
    repo: Path,
    chapter_id: str,
    data: dict[str, Any],
    context_paths: tuple[str, ...],
) -> list[str]:
    """Perform plan_refs validation. Returns list of error details (empty if PASS)."""
    details: list[str] = []

    # 1. data の chapter_id が対象と一致
    if data.get("chapter_id") != chapter_id:
        details.append(f"chapter_id mismatch: expected {chapter_id!r}, got {data.get('chapter_id')!r}")

    # 2. scenes の id が S1 から連番
    scenes = data.get("scenes", [])
    for idx, scene in enumerate(scenes, start=1):
        expected_s_id = f"S{idx}"
        if not isinstance(scene, dict) or scene.get("id") != expected_s_id:
            actual_id = scene.get("id") if isinstance(scene, dict) else None
            details.append(f"scene id mismatch at scene {idx}: expected {expected_s_id!r}, got {actual_id!r}")

    # 3. scenes の characters の各 ID について characters/<ID>.yaml が存在
    seen_chars: set[str] = set()
    for scene in scenes:
        if isinstance(scene, dict):
            for c_id in scene.get("characters", []):
                if isinstance(c_id, str):
                    seen_chars.add(c_id)
    for c_id in sorted(seen_chars):
        char_file = repo / "characters" / f"{c_id}.yaml"
        if not char_file.is_file():
            details.append(f"character file not found for {c_id}: characters/{c_id}.yaml")

    # 4. scenes の foreshadowing の各 ID が foreshadowing/registry.yaml にある
    seen_fs: set[str] = set()
    for scene in scenes:
        if isinstance(scene, dict):
            for f_id in scene.get("foreshadowing", []):
                if isinstance(f_id, str):
                    seen_fs.add(f_id)
    if seen_fs:
        reg_file = repo / "foreshadowing" / "registry.yaml"
        reg_ids: set[str] = set()
        if reg_file.is_file():
            try:
                reg_doc = load_yaml(reg_file)
                if isinstance(reg_doc, list):
                    for item in reg_doc:
                        if isinstance(item, dict) and "id" in item:
                            reg_ids.add(item["id"])
            except Exception:
                pass
        for f_id in sorted(seen_fs):
            if f_id not in reg_ids:
                details.append(f"foreshadowing ID {f_id!r} not found in foreshadowing/registry.yaml")

    # 5. context.settings の各パスが context_paths に含まれる
    ctx_obj = data.get("context", {})
    if isinstance(ctx_obj, dict):
        settings_paths = ctx_obj.get("settings", [])
        if isinstance(settings_paths, list):
            for p in settings_paths:
                if p not in context_paths:
                    details.append(f"context.settings path {p!r} was not provided in context_paths")

    # 6. context.past_summaries・past_drafts の各章が chapters-order.yaml で対象の章より前にある
    try:
        order = read_chapters_order(repo)
    except Exception:
        order = []
    try:
        cur_idx = order.index(chapter_id)
        allowed_past = set(order[:cur_idx])
    except ValueError:
        allowed_past = set()

    if isinstance(ctx_obj, dict):
        past_summaries = ctx_obj.get("past_summaries", [])
        if isinstance(past_summaries, list):
            for ch in past_summaries:
                if ch not in allowed_past:
                    details.append(f"context.past_summaries chapter {ch!r} is not prior to {chapter_id!r}")

        past_drafts = ctx_obj.get("past_drafts", [])
        if isinstance(past_drafts, list):
            for ch in past_drafts:
                if ch not in allowed_past:
                    details.append(f"context.past_drafts chapter {ch!r} is not prior to {chapter_id!r}")

    return details


def run_plan_job(
    settings: Settings,
    work: WorkInfo,
    chapter_id: str,
    *,
    job_id: str | None = None,
    claude_runner: ClaudeRunner = run_claude,
) -> dict[str, Any]:
    """Execute plan job for a chapter.

    Returns the updated job record.
    """
    repo = work.path
    ensure_main_ready(repo)

    if not can_accept_write_job(repo, chapter_id):
        raise ChapterError(f"Chapter {chapter_id!r} has active worktree/branch; cannot accept write job")

    meta = read_chapter_meta(repo, chapter_id)
    if meta is None or meta.get("state") != ChapterState.OUTLINED.name:
        cur_st = meta.get("state") if meta else "NONE"
        raise ChapterError(f"Chapter {chapter_id!r} must be in OUTLINED state to run plan, got: {cur_st}")

    actual_job_id = job_id or next_job_id(settings, work.work_key)
    ctx_paths = tuple(collect_plan_context(repo, chapter_id))
    task_text = load_prompt_template("plan", chapter_id=chapter_id)

    req = ClaudeJobRequest(
        work_key=work.work_key,
        job_id=actual_job_id,
        job_type="plan",
        chapter_id=chapter_id,
        root=repo,
        context_paths=ctx_paths,
        task_text=task_text,
        expected_type="plan",
        model=settings.claude_model,
    )

    record, data = run_claude_job(settings, req, claude_runner=claude_runner)
    if data is None:
        return record

    jdir = jobs_dir(settings, work.work_key)

    # 追加の検査 (plan_refs)
    details = _check_plan_refs(repo, chapter_id, data, ctx_paths)
    if details:
        record["checks"].append({
            "name": "plan_refs",
            "status": "FAIL",
            "details": details,
        })
        record = apply_transition(record, JobEvent.CHECK_FAILED, reason="plan_refs check failed")
        save_job_record(jdir, record)
        return record

    # PASS
    record["checks"].append({
        "name": "plan_refs",
        "status": "PASS",
        "details": [],
    })

    plan_yaml_bytes = dumps_yaml(data).encode("utf-8")
    transition_on_main(
        settings,
        work,
        chapter_id,
        ChapterEvent.PLAN_WRITTEN,
        subject=f"plan {chapter_id} by {actual_job_id}",
        trailers=[("NovUI-Job", actual_job_id)],
        extra_files={f"chapters/{chapter_id}/plan.yaml": plan_yaml_bytes},
        last_job=actual_job_id,
    )

    record = apply_transition(record, JobEvent.CHECK_PASSED, needs_validation=False)
    save_job_record(jdir, record)
    return record


def approve_plan(settings: Settings, work: WorkInfo, chapter_id: str) -> str:
    """Approve plan on main branch. Returns new commit hash."""
    return transition_on_main(
        settings,
        work,
        chapter_id,
        ChapterEvent.PLAN_APPROVED,
        subject=f"approve plan {chapter_id}",
    )


def reject_plan(settings: Settings, work: WorkInfo, chapter_id: str) -> str:
    """Reject plan on main branch. Returns new commit hash."""
    return transition_on_main(
        settings,
        work,
        chapter_id,
        ChapterEvent.PLAN_REJECTED,
        subject=f"reject plan {chapter_id}",
    )
