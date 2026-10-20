"""Direct edit and reopen operations by Human (Phase 3-C2 & 3-C3)."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from novui.chapters import (
    ChapterError,
    ensure_main_ready,
    mark_review_required,
    read_chapter_meta,
    read_chapters_order,
    review_marked,
    subsequent_chapters,
)
from novui.claude_cli import run_claude
from novui.claudejob import ClaudeRunner
from novui.config import Settings
from novui.draftjob import collect_draft_context, read_plan
from novui.flagcheck import FLAGS_REL
from novui.gitinspect import run_git
from novui.ids import next_job_id
from novui.impactjob import run_impact_job
from novui.jobrecord import apply_transition, new_job_record, now_iso, save_job_record
from novui.jobrunner import jobs_dir
from novui.mechanical import check_scene_markers
from novui.paths import is_safe_relpath
from novui.protected import is_protected
from novui.schema import SchemaError, validate_or_raise
from novui.semantics import (
    check_chapter,
    check_character,
    check_flags,
    check_plan,
    check_registry,
)
from novui.states import ChapterEvent, ChapterState, InvalidTransition, JobEvent, JobState, next_chapter_state
from novui.workrepo import chapter_branches, commit_all, create_job_worktree, remove_job_worktree
from novui.works import WorkInfo
from novui.yamlio import YamlError, load_yaml, write_yaml_atomic


def direct_edit(
    settings: Settings,
    work: WorkInfo,
    *,
    kind: str,
    note: str | None = None,
    run_impact: bool = True,
    claude_runner: ClaudeRunner = run_claude,
) -> dict[str, Any]:
    """Execute direct edit commit by human on main branch."""
    # 1. Kind and branch validation
    if kind not in ("typo", "content"):
        raise ValueError(f"kind must be 'typo' or 'content', got: {kind!r}")

    cur_branch = run_git(work.path, "branch", "--show-current").decode("utf-8").strip()
    if cur_branch != "main":
        raise ChapterError(f"Repository must be on 'main' branch, got: {cur_branch!r}")

    # 2. Get list of modified files
    status_out = run_git(
        work.path,
        "-c",
        "diff.renames=false",
        "status",
        "--porcelain=v1",
        "-z",
        "--untracked-files=all",
    )
    entries = [e for e in status_out.decode("utf-8", errors="surrogateescape").split("\0") if e]
    if not entries:
        raise ChapterError("nothing to edit")

    changed_paths: list[str] = []
    for entry in entries:
        status_code = entry[:2]
        path = entry[3:]
        if "D" in status_code:
            raise ChapterError(f"Direct edit does not allow deleting files: {path}")
        changed_paths.append(path)

    # 3. Path inspection
    try:
        order = read_chapters_order(work.path)
    except Exception as exc:
        raise ChapterError(f"Failed to read chapters-order.yaml: {exc}") from exc
    order_set = set(order)

    disallowed_paths: list[str] = []
    for p in changed_paths:
        if not is_safe_relpath(p):
            disallowed_paths.append(p)
            continue

        if p.startswith("chapters/"):
            parts = p.split("/")
            if len(parts) == 3 and parts[1] in order_set and parts[2] in ("draft.md", "outline.md", "plan.yaml"):
                if chapter_branches(work.path, parts[1]):
                    disallowed_paths.append(p)
            else:
                disallowed_paths.append(p)
        elif is_protected(p):
            if p == ".novui" or p.startswith(".novui/"):
                disallowed_paths.append(p)
        else:
            disallowed_paths.append(p)

    if disallowed_paths:
        raise ChapterError(f"Direct edit disallowed for paths: {', '.join(sorted(disallowed_paths))}")

    # 4. Content inspection
    for p in changed_paths:
        full_p = work.path / p
        try:
            if p == "project.yaml":
                doc = load_yaml(full_p)
                validate_or_raise(doc, "project")
            elif p == "chapters-order.yaml":
                doc = load_yaml(full_p)
                validate_or_raise(doc, "chapters_order")
            elif p.startswith("characters/") and p.endswith(".yaml"):
                doc = load_yaml(full_p)
                validate_or_raise(doc, "character")
                sem_errs = check_character(doc)
                if sem_errs:
                    raise ChapterError(f"Semantic error in {p}: {sem_errs}")
                cid = Path(p).stem
                if doc.get("id") != cid:
                    raise ChapterError(f"Character ID {doc.get('id')!r} does not match filename {p}")
            elif p == "foreshadowing/registry.yaml":
                doc = load_yaml(full_p)
                validate_or_raise(doc, "registry")
                sem_errs = check_registry(doc)
                if sem_errs:
                    raise ChapterError(f"Semantic error in {p}: {sem_errs}")
            elif p == "rules/prohibited.yaml":
                doc = load_yaml(full_p)
                validate_or_raise(doc, "prohibited")
            elif p == "rules/script.yaml":
                doc = load_yaml(full_p)
                validate_or_raise(doc, "script_rules")
            elif p == "assets/backgrounds.yaml":
                doc = load_yaml(full_p)
                validate_or_raise(doc, "backgrounds")
            elif p == FLAGS_REL:
                doc = load_yaml(full_p)
                validate_or_raise(doc, "flags")
                sem_errs = check_flags(doc)
                if sem_errs:
                    raise ChapterError(f"Semantic error in {p}: {sem_errs}")
            elif p.startswith("chapters/") and p.endswith("/plan.yaml"):
                cid = p.split("/")[1]
                doc = load_yaml(full_p)
                validate_or_raise(doc, "plan")
                sem_errs = check_plan(doc)
                if sem_errs:
                    raise ChapterError(f"Semantic error in {p}: {sem_errs}")
                if doc.get("chapter_id") != cid:
                    raise ChapterError(f"Plan chapter_id {doc.get('chapter_id')!r} does not match {cid!r}")
            elif p.startswith("chapters/") and p.endswith("/draft.md"):
                cid = p.split("/")[1]
                text = full_p.read_text(encoding="utf-8")
                plan_file = work.path / f"chapters/{cid}/plan.yaml"
                if not plan_file.is_file():
                    raise ChapterError(f"plan.yaml not found for {cid}")
                plan_doc = load_yaml(plan_file)
                scene_ids = [s["id"] for s in plan_doc.get("scenes", [])]
                chk = check_scene_markers(text, scene_ids)
                if chk.status != "PASS":
                    raise ChapterError(f"Scene markers check failed for {p}: {chk.details}")
            elif p.endswith(".yaml"):
                load_yaml(full_p)
            elif p.endswith(".md"):
                full_p.read_text(encoding="utf-8")
        except (YamlError, SchemaError, UnicodeDecodeError, OSError) as exc:
            raise ChapterError(f"Content validation failed for {p}: {exc}") from exc

    # 5. Chapter state handling
    chapters_drafted: list[str] = []
    chapters_replanned: list[str] = []
    review_marked_cids: list[str] = []
    orig_chapter_yamls: dict[str, dict[str, Any]] = {}
    new_chapter_yamls: dict[str, dict[str, Any]] = {}
    branch_targets: list[tuple[str, str]] = []

    if kind == "content":
        at = now_iso()
        draft_cids = {p.split("/")[1] for p in changed_paths if p.startswith("chapters/") and p.endswith("/draft.md")}
        plan_cids = {
            p.split("/")[1]
            for p in changed_paths
            if p.startswith("chapters/") and p.endswith("/plan.yaml") and p.split("/")[1] not in draft_cids
        }

        review_candidates: list[tuple[str, str]] = []  # (sub_cid, detail)

        # 5.1 draft.md changed chapters -> DRAFTED (in-memory preparation)
        for cid in order:
            if cid in draft_cids:
                meta = read_chapter_meta(work.path, cid)
                if meta is None:
                    raise ChapterError(f"chapter.yaml not found for {cid}")
                cur_st = ChapterState[meta["state"]]
                try:
                    nxt_st = next_chapter_state(cur_st, ChapterEvent.CONTENT_CHANGED)
                except InvalidTransition as exc:
                    raise ChapterError(f"Invalid transition for {cid}: {exc}") from exc

                new_meta = dict(meta)
                new_meta["state"] = nxt_st.name
                new_meta = review_marked(new_meta, "previous_chapter_changed", f"{cid} content edited", at)
                validate_or_raise(new_meta, "chapter")
                sem = check_chapter(new_meta)
                if sem:
                    raise ChapterError(f"Semantic error in chapter.yaml for {cid}: {sem}")

                orig_chapter_yamls[cid] = meta
                new_chapter_yamls[cid] = new_meta
                chapters_drafted.append(cid)

                for sub in subsequent_chapters(order, cid):
                    review_candidates.append((sub, f"{cid} content edited"))

        # 5.2 plan.yaml changed chapters (without draft.md change)
        for cid in order:
            if cid in plan_cids:
                meta = read_chapter_meta(work.path, cid)
                if meta is None:
                    raise ChapterError(f"chapter.yaml not found for {cid}")
                cur_st = ChapterState[meta["state"]]
                if cur_st == ChapterState.PLAN_APPROVED:
                    new_meta = dict(meta)
                    # PLAN_APPROVED to PLANNED has no ChapterEvent transition, written directly as specified
                    new_meta["state"] = ChapterState.PLANNED.name
                    validate_or_raise(new_meta, "chapter")
                    sem = check_chapter(new_meta)
                    if sem:
                        raise ChapterError(f"Semantic error in chapter.yaml for {cid}: {sem}")

                    orig_chapter_yamls[cid] = meta
                    new_chapter_yamls[cid] = new_meta
                    chapters_replanned.append(cid)
                elif cur_st in (
                    ChapterState.DRAFTED,
                    ChapterState.AI_VALIDATED,
                    ChapterState.HUMAN_APPROVED,
                    ChapterState.FINAL,
                ):
                    for sub in subsequent_chapters(order, cid):
                        review_candidates.append((sub, f"{cid} content edited"))

        # 5.3 Review required on subsequent chapters (excluding chapters drafted in this edit)
        seen_targets: set[str] = set()
        deduped_targets: list[tuple[str, str]] = []
        for sub_cid, detail in review_candidates:
            if sub_cid not in chapters_drafted and sub_cid not in seen_targets:
                seen_targets.add(sub_cid)
                deduped_targets.append((sub_cid, detail))

        deduped_targets.sort(key=lambda t: order.index(t[0]))

        for sub_cid, detail in deduped_targets:
            branches = chapter_branches(work.path, sub_cid)
            if branches:
                branch_targets.append((sub_cid, detail))
            else:
                sub_meta = read_chapter_meta(work.path, sub_cid)
                if sub_meta is None:
                    raise ChapterError(f"chapter.yaml not found for {sub_cid}")
                new_sub_meta = review_marked(sub_meta, "previous_chapter_changed", detail, at)
                validate_or_raise(new_sub_meta, "chapter")
                sem = check_chapter(new_sub_meta)
                if sem:
                    raise ChapterError(f"Semantic error in chapter.yaml for {sub_cid}: {sem}")

                orig_chapter_yamls[sub_cid] = sub_meta
                new_chapter_yamls[sub_cid] = new_sub_meta

            review_marked_cids.append(sub_cid)

        # 5.4 Mark review required on subsequent chapters with work branches (after in-memory validation)
        for sub_cid, detail in branch_targets:
            mark_review_required(
                settings,
                work,
                [sub_cid],
                code="previous_chapter_changed",
                detail=detail,
                branch_only=True,
            )

    # 6. Write main's chapter.yamls and create single commit
    if note and note.strip():
        first_line = note.strip().splitlines()[0]
        subject = f"human edit ({kind}): {first_line}"
    else:
        subject = f"human edit ({kind})"

    trailers = [("NovUI-Edit", f"human-{kind}")]

    written_cids: list[str] = []
    try:
        for cid, meta in new_chapter_yamls.items():
            write_yaml_atomic(work.path / f"chapters/{cid}/chapter.yaml", meta)
            written_cids.append(cid)

        commit_hash = commit_all(
            work.path,
            subject,
            trailers,
            name=settings.git_name,
            email=settings.git_email,
        )
        if commit_hash is None:
            raise ChapterError("nothing to commit")
    except Exception as exc:
        # Revert written chapter.yamls on failure (leave human edits intact)
        for cid in written_cids:
            orig_meta = orig_chapter_yamls.get(cid)
            if orig_meta is not None:
                write_yaml_atomic(work.path / f"chapters/{cid}/chapter.yaml", orig_meta)
        run_git(work.path, "reset", "-q")
        if isinstance(exc, ChapterError):
            raise
        raise ChapterError(f"Failed to commit direct edit: {exc}") from exc

    # 7. Impact analysis
    has_protected = any(is_protected(p) and not (p == ".novui" or p.startswith(".novui/")) for p in changed_paths)
    impact_job_id: str | None = None
    impact_error: str | None = None

    if has_protected and run_impact:
        parents_out = run_git(work.path, "rev-list", "--parents", "-n", "1", commit_hash).decode("utf-8").strip()
        parents = parents_out.split()[1:]
        if parents:
            parent_commit = parents[0]
            impact_reason = note.strip() if (note and note.strip()) else "human edit"
            try:
                rec, affected, notes = run_impact_job(
                    settings,
                    work,
                    base=parent_commit,
                    head=commit_hash,
                    reason=impact_reason,
                    claude_runner=claude_runner,
                )
                impact_job_id = rec.get("job_id")
                if rec.get("state") != JobState.COMPLETED.name:
                    last_h = rec.get("history", [])[-1] if rec.get("history") else {}
                    impact_error = last_h.get("reason") or f"impact analysis failed with state {rec.get('state')}"
            except Exception as exc:
                impact_error = str(exc)

    return {
        "commit": commit_hash,
        "paths": sorted(changed_paths),
        "chapters_drafted": sorted(chapters_drafted, key=lambda x: order.index(x)),
        "chapters_replanned": sorted(chapters_replanned, key=lambda x: order.index(x)),
        "review_marked": sorted(review_marked_cids, key=lambda x: order.index(x)),
        "impact_job": impact_job_id,
        "impact_error": impact_error,
    }


def reopen_chapter(settings: Settings, work: WorkInfo, chapter_id: str) -> dict[str, Any]:
    """Reopen a DRAFTED chapter on main after human content edit (Phase 3-C3)."""
    # 1. ensure_main_ready and chapter preconditions
    ensure_main_ready(work.path)

    order = read_chapters_order(work.path)
    if chapter_id not in order:
        raise ChapterError(f"Chapter {chapter_id!r} not found in chapters-order.yaml")

    meta = read_chapter_meta(work.path, chapter_id)
    if meta is None or meta.get("state") != ChapterState.DRAFTED.name:
        cur_st = meta.get("state") if meta else "NONE"
        raise ChapterError(f"Chapter {chapter_id!r} must be in DRAFTED state to reopen, got: {cur_st}")

    branches = chapter_branches(work.path, chapter_id)
    if branches:
        raise ChapterError(f"Chapter {chapter_id!r} already has active job branch: {branches[0]}")

    draft_path = work.path / f"chapters/{chapter_id}/draft.md"
    plan_path = work.path / f"chapters/{chapter_id}/plan.yaml"
    if not draft_path.is_file():
        raise ChapterError(f"draft.md not found for chapter {chapter_id!r}")
    if not plan_path.is_file():
        raise ChapterError(f"plan.yaml not found for chapter {chapter_id!r}")

    # 2. Create job branch and worktree
    actual_job_id = next_job_id(settings, work.work_key)
    jw = create_job_worktree(work.path, settings.worktree_root, work.work_key, chapter_id, actual_job_id)

    try:
        # 3. Context collection
        plan = read_plan(jw.path, chapter_id)
        ctx_paths = collect_draft_context(jw.path, chapter_id, plan)

        context_entries: list[dict[str, str]] = []
        for p in ctx_paths:
            content = (jw.path / p).read_bytes()
            context_entries.append({
                "path": p,
                "sha256": "sha256:" + hashlib.sha256(content).hexdigest().lower(),
            })

        # 4. Job record
        jdir = jobs_dir(settings, work.work_key)
        jdir.mkdir(parents=True, exist_ok=True)

        record = new_job_record(actual_job_id, "draft", chapter_id=chapter_id)
        record["base_commit"] = jw.base_commit
        record["branch"] = jw.branch
        record["worktree"] = str(jw.path)
        record["context"] = context_entries
        record["cli"] = None
        record["container"] = None

        record = apply_transition(record, JobEvent.LOCK_ACQUIRED)
        record = apply_transition(record, JobEvent.CLI_EXITED)
        record = apply_transition(
            record,
            JobEvent.CHECK_PASSED,
            needs_validation=False,
            reason="reopened after human content edit",
        )
        record["checks"] = []
        save_job_record(jdir, record)
        return record
    except Exception:
        remove_job_worktree(work.path, jw, delete_branch=True)
        raise
