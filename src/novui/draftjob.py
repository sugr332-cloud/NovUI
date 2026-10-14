"""Draft job based on an approved plan (Phase 2-B1)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from novui.chapters import (
    ChapterError,
    ensure_main_ready,
    read_chapter_meta,
    read_chapters_order,
)
from novui.charrules import render_rule_tables, scene_rule_tables
from novui.checks import CheckResult
from novui.config import Settings
from novui.container import run_container
from novui.ids import next_job_id
from novui.jobrecord import apply_transition, new_job_record, save_job_record
from novui.jobrunner import ContainerRunner, DraftJobRequest, jobs_dir, run_draft_job
from novui.locks import RunLock, can_accept_write_job
from novui.mechanical import MechanicalInputs, load_mechanical_inputs, plan_ref_ids, run_draft_mechanical_checks
from novui.models import ModelUnavailable, resolve_model
from novui.paths import PathError, ensure_within, is_safe_relpath
from novui.prompt import load_prompt_template
from novui.schema import SchemaError, validate_or_raise
from novui.scriptcheck import load_earlier_plans, load_script_inputs, run_script_checks, script_char_count
from novui.semantics import check_chapter, check_plan
from novui.semantics import plan_scene_successors
from novui.states import ChapterEvent, ChapterState, JobEvent, JobState, next_chapter_state
from novui.textformat import read_text_format
from novui.workrepo import commit_all
from novui.works import WorkInfo
from novui.yamlio import load_yaml, write_yaml_atomic

DRAFT_MODEL_ROLE = "draft"


def read_plan(root: Path, chapter_id: str) -> dict[str, Any]:
    """Read and validate chapters/<id>/plan.yaml (schema plan and semantics)."""
    path = root / "chapters" / chapter_id / "plan.yaml"
    if not path.is_file():
        raise ChapterError(f"plan.yaml not found for chapter {chapter_id!r}")
    doc = load_yaml(path)
    validate_or_raise(doc, "plan")
    sem_errs = check_plan(doc)
    if sem_errs:
        raise SchemaError(f"Semantic validation failed for plan: {sem_errs}", errors=sem_errs)
    if doc["chapter_id"] != chapter_id:
        raise ChapterError(f"plan.yaml chapter_id {doc['chapter_id']!r} does not match {chapter_id!r}")
    return doc


def collect_draft_context(root: Path, chapter_id: str, plan: dict[str, Any]) -> list[str]:
    """Collect context paths for draft in the priority order of §16.1.

    1. 直前章の summary.yaml、2. その他の過去章の summary.yaml、3. 過去章の draft.md、
    4. plan の context.settings、場面の登場人物の characters/<ID>.yaml、場面が参照する伏線があれば registry.yaml、
    5. 対象章の outline.md と plan.yaml。
    Raises ChapterError if a referenced chapter is not before the target chapter or a file is missing.
    """
    order = read_chapters_order(root)
    if chapter_id not in order:
        raise ChapterError(f"Chapter {chapter_id!r} not in chapters-order.yaml")
    idx = order.index(chapter_id)
    prior = order[:idx]
    ctx = plan["context"]

    for key in ("past_summaries", "past_drafts"):
        for ch in ctx[key]:
            if ch not in prior:
                raise ChapterError(f"plan context.{key} chapter {ch!r} is not before {chapter_id!r}")

    summaries = [ch for ch in prior if ch in ctx["past_summaries"]]
    if summaries and summaries[-1] == (prior[-1] if prior else None):
        summaries = [summaries[-1]] + summaries[:-1]
    drafts = [ch for ch in prior if ch in ctx["past_drafts"]]

    char_ids, fore_ids = plan_ref_ids(plan)

    # (path, required)。plan が明示した資料は必須。場面の参照から加える資料は、存在しなければ含めない
    # （存在しない ID は機械検査 ref_ids の WARNING で報告する。§11.3）
    candidates: list[tuple[str, bool]] = []
    candidates += [(f"chapters/{ch}/summary.yaml", True) for ch in summaries]
    candidates += [(f"chapters/{ch}/draft.md", True) for ch in drafts]
    candidates += [(p, True) for p in ctx["settings"]]
    candidates += [(f"characters/{cid}.yaml", False) for cid in char_ids]
    if fore_ids:
        candidates.append(("foreshadowing/registry.yaml", False))
    candidates += [(f"chapters/{chapter_id}/outline.md", True), (f"chapters/{chapter_id}/plan.yaml", True)]

    result: list[str] = []
    missing: list[str] = []
    for rel, required in candidates:
        if rel in result or rel in missing:
            continue
        # run_draft_job は worktree を作った後に Context を読むため、パスの安全性はここで先に確認する
        if not is_safe_relpath(rel):
            raise ChapterError(f"Unsafe context path: {rel!r}")
        try:
            ensure_within(root, root / rel)
        except (PathError, ValueError) as exc:
            raise ChapterError(f"Context path outside work: {rel!r}") from exc
        if (root / rel).is_file():
            result.append(rel)
        elif required:
            missing.append(rel)
    if missing:
        raise ChapterError(f"Context files not found: {missing}")
    return result


def build_character_rules_text(
    root: Path, chapter_id: str, plan: Mapping[str, Any], inputs: MechanicalInputs
) -> str:
    """Rule tables of the plan's scenes for {{character_rules}} (phase2d-design §3). 'なし' when empty."""
    return render_rule_tables(scene_rule_tables(read_chapters_order(root), chapter_id, plan, inputs.characters))


def build_draft_instruction(chapter_id: str, plan: dict[str, Any], *, character_rules: str) -> str:
    """Build AGY instruction text from prompts/agy/draft.md."""
    scene_ids = [s["id"] for s in plan["scenes"]]
    return load_prompt_template(
        "draft",
        group="agy",
        chapter_id=chapter_id,
        scene_ids="、".join(scene_ids),
        scene_count=str(len(scene_ids)),
        scene_marker_lines="\n".join(f"<!-- scene: {sid} -->" for sid in scene_ids),
        min_chars=str(plan["target_chars"]["min"]),
        max_chars=str(plan["target_chars"]["max"]),
        character_rules=character_rules,
    ).rstrip("\n")


def scene_flow_text(plan: Mapping[str, Any]) -> str:
    """{{scene_flow}} of draft_script.md: one line per scene with its background and how it continues."""
    scenes = plan["scenes"]
    succ = plan_scene_successors(list(scenes))
    lines: list[str] = []
    for s in scenes:
        head = s["id"] + (f"（背景 {s['background']}）" if s.get("background") else "")
        if s.get("choice"):
            opts = "／".join(
                f"{c['text']} → {c['goto']}" + (f"（フラグ {c['flag']}）" if c.get("flag") else "") for c in s["choice"]
            )
            lines.append(f"{head} → 選択肢：{opts}")
        elif s.get("next"):
            lines.append(f"{head} → @goto {s['next']}")
        elif succ[s["id"]]:
            lines.append(f"{head} → {succ[s['id']][0]}（@goto は書かない）")
        else:
            lines.append(f"{head} → 章の終わり")
    return "\n".join(lines)


def extras_text(plan: Mapping[str, Any]) -> str:
    """{{extras}} of draft_script.md: the extras of each scene, or 'なし'."""
    lines = [f"{s['id']}：{'、'.join(s['extras'])}" for s in plan["scenes"] if s.get("extras")]
    return "\n".join(lines) if lines else "なし"


def script_format_text() -> str:
    """{{script_format}}: the line kinds of the script format (prompts/agy/script_format.md), shared by AGY prompts."""
    return load_prompt_template("script_format", group="agy").rstrip("\n")


def build_script_draft_instruction(chapter_id: str, plan: dict[str, Any], *, character_rules: str) -> str:
    """Build AGY instruction text for a script-format work from prompts/agy/draft_script.md."""
    scene_ids = [s["id"] for s in plan["scenes"]]
    return load_prompt_template(
        "draft_script",
        group="agy",
        chapter_id=chapter_id,
        scene_ids="、".join(scene_ids),
        scene_count=str(len(scene_ids)),
        script_format=script_format_text(),
        scene_flow=scene_flow_text(plan),
        extras=extras_text(plan),
        min_chars=str(plan["target_chars"]["min"]),
        max_chars=str(plan["target_chars"]["max"]),
        character_rules=character_rules,
    ).rstrip("\n")


@dataclass(frozen=True)
class DraftSpec:
    """What a draft Job needs besides the context: the instruction and the checks (novel or script)."""

    instruction: str
    target_chars: tuple[int, int] | None
    extra_checks: Callable[[str], Sequence[CheckResult]]


def prepare_draft(root: Path, chapter_id: str, plan: dict[str, Any]) -> DraftSpec:
    """Instruction and checks of a draft Job for the work's text format (phase2e-design decision 1).

    Script works: every character of the plan's scenes needs a display_name (ChapterError otherwise), the
    character count is taken from the script text, and script_syntax / script_flow FAIL the Job.
    """
    inputs = load_mechanical_inputs(root)
    rules = build_character_rules_text(root, chapter_id, plan, inputs)
    min_c, max_c = plan["target_chars"]["min"], plan["target_chars"]["max"]
    if read_text_format(root) != "script":
        return DraftSpec(
            instruction=build_draft_instruction(chapter_id, plan, character_rules=rules),
            target_chars=(min_c, max_c),
            extra_checks=lambda text: run_draft_mechanical_checks(text, plan, inputs),
        )
    missing = sorted({
        cid for s in plan["scenes"] for cid in s["characters"]
        if cid in inputs.characters and not inputs.characters[cid].get("display_name")
    })
    if missing:
        raise ChapterError(f"characters without display_name in a script work: {missing}")
    script_inputs = load_script_inputs(root, inputs.characters)
    earlier_plans = load_earlier_plans(root, chapter_id)
    return DraftSpec(
        instruction=build_script_draft_instruction(chapter_id, plan, character_rules=rules),
        target_chars=None,
        extra_checks=lambda text: (
            [script_char_count(text, min_c, max_c)]
            + run_draft_mechanical_checks(text, plan, inputs)
            + run_script_checks(text, plan, script_inputs, for_draft=True, earlier_plans=earlier_plans)
        ),
    )


def _mark_drafted_on_branch(settings: Settings, worktree: Path, chapter_id: str, job_id: str) -> str:
    """Write chapter.yaml DRAFTED on the job branch and commit with NovUI-Job (phase2-plan §2)."""
    meta = read_chapter_meta(worktree, chapter_id)
    if meta is None:
        raise ChapterError(f"chapter.yaml not found on job branch for {chapter_id!r}")
    next_st = next_chapter_state(
        ChapterState[meta["state"]],
        ChapterEvent.DRAFT_COMPLETED,
        review_required=bool(meta["review_required"]),
    )
    meta["state"] = next_st.name
    meta["last_job"] = job_id
    validate_or_raise(meta, "chapter")
    sem_errs = check_chapter(meta)
    if sem_errs:
        raise SchemaError(f"Semantic validation failed for chapter: {sem_errs}", errors=sem_errs)
    write_yaml_atomic(worktree / "chapters" / chapter_id / "chapter.yaml", meta)
    commit = commit_all(
        worktree,
        f"chapter {chapter_id} {next_st.name} by {job_id}",
        [("NovUI-Job", job_id)],
        name=settings.git_name,
        email=settings.git_email,
    )
    if commit is None:
        raise ChapterError(f"No changes to commit for chapter {chapter_id} on job branch")
    return commit


def run_chapter_draft_job(
    settings: Settings,
    work: WorkInfo,
    chapter_id: str,
    *,
    job_id: str | None = None,
    run_lock: RunLock | None = None,
    container_runner: ContainerRunner = run_container,
) -> dict[str, Any]:
    """Run a draft Job for a PLAN_APPROVED chapter based on its plan.yaml.

    On COMPLETED, chapter.yaml on the job branch becomes DRAFTED. On WAITING_HUMAN
    (【要確認】 markers, prompt too large, model unavailable) or FAILED, chapter.yaml is not changed.
    Returns the job record.
    """
    repo = work.path
    ensure_main_ready(repo)

    if not can_accept_write_job(repo, chapter_id):
        raise ChapterError(f"Chapter {chapter_id!r} has active worktree/branch; cannot accept write job")

    meta = read_chapter_meta(repo, chapter_id)
    if meta is None or meta["state"] != ChapterState.PLAN_APPROVED.name:
        cur_st = meta["state"] if meta else "NONE"
        raise ChapterError(f"Chapter {chapter_id!r} must be in PLAN_APPROVED state to run draft, got: {cur_st}")

    plan = read_plan(repo, chapter_id)
    ctx_paths = tuple(collect_draft_context(repo, chapter_id, plan))
    spec = prepare_draft(repo, chapter_id, plan)

    actual_job_id = job_id or next_job_id(settings, work.work_key)

    try:
        model = resolve_model(settings, DRAFT_MODEL_ROLE, work_key=work.work_key)
    except ModelUnavailable as exc:
        # Phase 1 計画 §4 の 13：選んだモデルがなければ Job を開始せず WAITING_HUMAN
        jdir = jobs_dir(settings, work.work_key)
        jdir.mkdir(parents=True, exist_ok=True)
        record = new_job_record(actual_job_id, "draft", chapter_id=chapter_id)
        record = apply_transition(record, JobEvent.NEEDS_HUMAN_INPUT, reason=f"model_unavailable: {exc}")
        save_job_record(jdir, record)
        return record

    req = DraftJobRequest(
        work_key=work.work_key,
        repo=repo,
        chapter_id=chapter_id,
        job_id=actual_job_id,
        model=model,
        context_paths=ctx_paths,
        instruction=spec.instruction,
        target_chars=spec.target_chars,
        needs_validation=False,
        extra_checks=spec.extra_checks,
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
