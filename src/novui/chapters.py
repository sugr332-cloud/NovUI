"""Chapter management and state transitions on main branch."""

from pathlib import Path
import re
from typing import Any, Mapping, Sequence

from novui.config import Settings
from novui.gitinspect import run_git
from novui.jobrecord import load_job_record, now_iso
from novui.jobrunner import jobs_dir
from novui.locks import can_accept_write_job
from novui.paths import ensure_within, is_safe_relpath
from novui.schema import SchemaError, validate_or_raise
from novui.semantics import check_chapter
from novui.states import ChapterEvent, ChapterState, JobState, next_chapter_state
from novui.workrepo import chapter_branches, commit_all
from novui.works import WorkInfo
from novui.yamlio import load_yaml, write_yaml_atomic

_CHAPTER_ID_RE = re.compile(r"^ch-[0-9]{3,}$")


class ChapterError(Exception):
    """Raised when a chapter operation is invalid or repository is not ready."""


def read_chapters_order(root: Path) -> list[str]:
    """Read and validate chapters-order.yaml from repository root."""
    path = root / "chapters-order.yaml"
    if not path.is_file():
        raise ChapterError(f"chapters-order.yaml not found at {path}")
    doc = load_yaml(path)
    validate_or_raise(doc, "chapters_order")
    return list(doc)


def read_chapter_meta(root: Path, chapter_id: str) -> dict | None:
    """Read and validate chapters/<id>/chapter.yaml if it exists, else None."""
    path = root / "chapters" / chapter_id / "chapter.yaml"
    if not path.is_file():
        return None
    doc = load_yaml(path)
    if not isinstance(doc, dict):
        raise SchemaError("chapter.yaml must be a mapping", errors=["root is not a mapping"])
    validate_or_raise(doc, "chapter")
    sem_errs = check_chapter(doc)
    if sem_errs:
        raise SchemaError(f"Semantic validation failed for chapter: {sem_errs}", errors=sem_errs)
    return doc


def ensure_main_ready(repo: Path) -> None:
    """Ensure repository is checked out on clean 'main' branch."""
    cur_branch = run_git(repo, "branch", "--show-current").decode("utf-8").strip()
    if cur_branch != "main":
        raise ChapterError(f"Repository must be on 'main' branch, got: {cur_branch!r}")

    status_out = run_git(repo, "status", "--porcelain", "--untracked-files=all")
    if status_out.strip():
        raise ChapterError(f"Repository has uncommitted changes:\n{status_out.decode('utf-8', errors='replace')}")


def add_chapter(
    settings: Settings,
    work: WorkInfo,
    chapter_id: str,
    title: str,
    outline_text: str,
) -> str:
    """Add a new chapter to the novel work on main branch.

    Returns the new commit hash.
    """
    repo = work.path
    ensure_main_ready(repo)

    if not _CHAPTER_ID_RE.match(chapter_id):
        raise ValueError(f"Invalid chapter_id {chapter_id!r}; must match pattern ^ch-[0-9]{{3,}}$")
    if not title or not title.strip():
        raise ValueError("title cannot be empty")
    if not outline_text or not outline_text.strip():
        raise ValueError("outline_text cannot be empty")

    order = read_chapters_order(repo)
    if chapter_id in order:
        raise ChapterError(f"Chapter {chapter_id!r} already present in chapters-order.yaml")

    ch_dir = repo / "chapters" / chapter_id
    if ch_dir.exists():
        raise ChapterError(f"Chapter directory already exists: {ch_dir}")

    ch_dir.mkdir(parents=True)

    # Compute initial state
    initial_st = next_chapter_state(None, ChapterEvent.OUTLINE_CREATED)

    # Write outline.md (ensure trailing newline)
    outline_path = ch_dir / "outline.md"
    outline_content = outline_text if outline_text.endswith("\n") else outline_text + "\n"
    outline_path.write_text(outline_content, encoding="utf-8")

    # Write chapter.yaml
    meta = {
        "id": chapter_id,
        "title": title,
        "state": initial_st.name,
        "review_required": False,
        "review_reasons": [],
        "validation_skipped": False,
        "last_job": None,
    }
    validate_or_raise(meta, "chapter")
    write_yaml_atomic(ch_dir / "chapter.yaml", meta)

    # Update chapters-order.yaml
    order.append(chapter_id)
    validate_or_raise(order, "chapters_order")
    write_yaml_atomic(repo / "chapters-order.yaml", order)

    commit_hash = commit_all(
        repo,
        f"add chapter {chapter_id}",
        [("NovUI-Edit", "human-content")],
        name=settings.git_name,
        email=settings.git_email,
    )
    if commit_hash is None:
        raise ChapterError(f"Failed to create commit for adding chapter {chapter_id}")
    return commit_hash


def transition_on_main(
    settings: Settings,
    work: WorkInfo,
    chapter_id: str,
    event: ChapterEvent,
    *,
    subject: str,
    trailers: Sequence[tuple[str, str]] = (),
    extra_files: Mapping[str, bytes] | None = None,
    last_job: str | None = None,
) -> str:
    """Transition a chapter state directly on main branch and commit changes.

    Returns the new commit hash.
    """
    repo = work.path
    ensure_main_ready(repo)

    if not can_accept_write_job(repo, chapter_id):
        raise ChapterError(f"Chapter {chapter_id!r} has active worktree/branch; cannot transition on main")

    meta = read_chapter_meta(repo, chapter_id)
    if meta is None:
        raise ChapterError(f"Chapter metadata for {chapter_id!r} not found")

    cur_state = ChapterState[meta["state"]]
    review_req = bool(meta.get("review_required", False))
    next_st = next_chapter_state(cur_state, event, review_required=review_req)

    meta["state"] = next_st.name
    if last_job is not None:
        meta["last_job"] = last_job

    validate_or_raise(meta, "chapter")
    sem_errs = check_chapter(meta)
    if sem_errs:
        raise SchemaError(f"Semantic validation failed for chapter: {sem_errs}", errors=sem_errs)

    write_yaml_atomic(repo / "chapters" / chapter_id / "chapter.yaml", meta)

    if extra_files:
        for rel_path, content in extra_files.items():
            if not is_safe_relpath(rel_path):
                raise ValueError(f"Invalid extra file relative path: {rel_path!r}")
            target = repo / rel_path
            resolved = ensure_within(repo, target)
            resolved.parent.mkdir(parents=True, exist_ok=True)
            resolved.write_bytes(content)

    commit_hash = commit_all(
        repo,
        subject,
        trailers,
        name=settings.git_name,
        email=settings.git_email,
    )
    if commit_hash is None:
        raise ChapterError(f"No changes to commit for transition {event.name} on chapter {chapter_id}")
    return commit_hash


def review_marked(meta: Mapping[str, Any], code: str, detail: str, at: str) -> dict[str, Any]:
    """Return a new chapter metadata dict with review_required=True and the reason added.

    Does not modify meta. If an identical reason (same code and detail) already exists,
    it is not added again and 'at' is not updated.
    """
    res = dict(meta)
    res["review_required"] = True
    existing_reasons = list(meta.get("review_reasons", []))
    already_exists = any(r.get("code") == code and r.get("detail") == detail for r in existing_reasons)
    if not already_exists:
        existing_reasons.append({"code": code, "detail": detail, "at": at})
    res["review_reasons"] = existing_reasons
    return res


def subsequent_chapters(order: Sequence[str], chapter_id: str) -> list[str]:
    """Return chapters after chapter_id in order. Raises ValueError if chapter_id not in order."""
    if chapter_id not in order:
        raise ValueError(f"Chapter {chapter_id!r} not found in chapters order")
    idx = order.index(chapter_id)
    return list(order[idx + 1:])


def mark_review_required(
    settings: Settings,
    work: WorkInfo,
    chapter_ids: Sequence[str],
    code: str,
    detail: str,
    *,
    branch_only: bool = False,
) -> list[str]:
    """Mark review_required on chapters, committing to chapter worktree or main.

    Returns the list of created commit hashes in order.
    """
    repo = work.path
    order = read_chapters_order(repo)
    order_set = set(order)

    # chapter_ids は重複を除き、chapters-order.yaml の順に並べる。order に無い章は ChapterError。
    seen: set[str] = set()
    deduped: list[str] = []
    for cid in chapter_ids:
        if cid not in seen:
            seen.add(cid)
            deduped.append(cid)

    for cid in deduped:
        if cid not in order_set:
            raise ChapterError(f"Chapter {cid!r} not found in chapters-order.yaml")

    ordered_target_ids = [cid for cid in order if cid in seen]

    # 作業ブランチの有無で分類
    branch_chapters: list[tuple[str, str]] = []  # (chapter_id, branch_name)
    main_chapters: list[str] = []

    for cid in ordered_target_ids:
        branches = chapter_branches(repo, cid)
        if branches:
            branch_chapters.append((cid, branches[-1]))
        else:
            main_chapters.append(cid)

    # 作業ブランチがある章：そのブランチに紐づく Job 記録に、state が RUNNING・CHECKING・VALIDATING のものがあれば ChapterError（何も書かない）。
    # この検査は、書く前にすべての章についてまとめて行う。
    if branch_chapters:
        jdir = jobs_dir(settings, work.work_key)
        if jdir.is_dir():
            target_branches = {b for _, b in branch_chapters}
            active_states = {JobState.RUNNING.name, JobState.CHECKING.name, JobState.VALIDATING.name}
            for p in sorted(jdir.glob("job-*.yaml")):
                rec = load_job_record(p)
                if rec.get("branch") in target_branches and rec.get("state") in active_states:
                    raise ChapterError(
                        f"Active job {rec.get('job_id')} in state {rec.get('state')} on branch {rec.get('branch')}"
                    )

    commits: list[str] = []
    at = now_iso()

    from novui.finalize import cycle_info

    # 作業ブランチがある章は、その worktree の chapter.yaml を書き換えて、章ごとに1つ commit する
    for cid, branch in branch_chapters:
        info = cycle_info(settings, work, cid, require_worktree=True)
        wt = info.jw.path
        meta = load_yaml(wt / "chapters" / cid / "chapter.yaml")
        new_meta = review_marked(meta, code, detail, at)
        if new_meta != meta:
            validate_or_raise(new_meta, "chapter")
            sem = check_chapter(new_meta)
            if sem:
                raise SchemaError(f"Semantic validation failed for chapter: {sem}", errors=sem)
            write_yaml_atomic(wt / "chapters" / cid / "chapter.yaml", new_meta)
            c_hash = commit_all(
                wt,
                f"review required {cid} ({code})",
                [],
                name=settings.git_name,
                email=settings.git_email,
            )
            if c_hash:
                commits.append(c_hash)

    # 作業ブランチが無い章は、branch_only が False なら、main の chapter.yaml を書き換えて、まとめて1つ commit する
    if not branch_only and main_chapters:
        ensure_main_ready(repo)
        changed_cids: list[str] = []
        for cid in main_chapters:
            meta = read_chapter_meta(repo, cid)
            if meta is None:
                raise ChapterError(f"chapter.yaml of {cid!r} not found on main")
            new_meta = review_marked(meta, code, detail, at)
            if new_meta != meta:
                validate_or_raise(new_meta, "chapter")
                sem = check_chapter(new_meta)
                if sem:
                    raise SchemaError(f"Semantic validation failed for chapter: {sem}", errors=sem)
                write_yaml_atomic(repo / "chapters" / cid / "chapter.yaml", new_meta)
                changed_cids.append(cid)

        if changed_cids:
            subject = f"review required {','.join(changed_cids)} ({code})"
            c_hash = commit_all(
                repo,
                subject,
                [],
                name=settings.git_name,
                email=settings.git_email,
            )
            if c_hash:
                commits.append(c_hash)

    return commits


def clear_review_required(settings: Settings, work: WorkInfo, chapter_id: str) -> str:
    """Clear review_required on chapter's current chapter.yaml (worktree if branched, else main).

    Returns the commit hash.
    """
    repo = work.path
    order = read_chapters_order(repo)
    if chapter_id not in order:
        raise ChapterError(f"Chapter {chapter_id!r} not found in chapters-order.yaml")

    branches = chapter_branches(repo, chapter_id)
    if branches:
        branch = branches[-1]
        jdir = jobs_dir(settings, work.work_key)
        if jdir.is_dir():
            active_states = {JobState.RUNNING.name, JobState.CHECKING.name, JobState.VALIDATING.name}
            for p in sorted(jdir.glob("job-*.yaml")):
                rec = load_job_record(p)
                if rec.get("branch") == branch and rec.get("state") in active_states:
                    raise ChapterError(
                        f"Active job {rec.get('job_id')} in state {rec.get('state')} on branch {branch}"
                    )

        from novui.finalize import cycle_info
        info = cycle_info(settings, work, chapter_id, require_worktree=True)
        wt = info.jw.path
        ch_yaml_path = wt / "chapters" / chapter_id / "chapter.yaml"
        meta = load_yaml(ch_yaml_path)
        if not meta.get("review_required"):
            raise ChapterError(f"Chapter {chapter_id!r} does not have review_required set")

        meta["review_required"] = False
        meta["review_reasons"] = []
        validate_or_raise(meta, "chapter")
        sem = check_chapter(meta)
        if sem:
            raise SchemaError(f"Semantic validation failed for chapter: {sem}", errors=sem)
        write_yaml_atomic(ch_yaml_path, meta)
        c_hash = commit_all(
            wt,
            f"review done {chapter_id}",
            [],
            name=settings.git_name,
            email=settings.git_email,
        )
        if c_hash is None:
            raise ChapterError(f"Failed to commit review done for chapter {chapter_id}")
        return c_hash
    else:
        ensure_main_ready(repo)
        ch_yaml_path = repo / "chapters" / chapter_id / "chapter.yaml"
        meta = read_chapter_meta(repo, chapter_id)
        if meta is None or not meta.get("review_required"):
            raise ChapterError(f"Chapter {chapter_id!r} does not have review_required set")

        meta["review_required"] = False
        meta["review_reasons"] = []
        validate_or_raise(meta, "chapter")
        sem = check_chapter(meta)
        if sem:
            raise SchemaError(f"Semantic validation failed for chapter: {sem}", errors=sem)
        write_yaml_atomic(ch_yaml_path, meta)
        c_hash = commit_all(
            repo,
            f"review done {chapter_id}",
            [],
            name=settings.git_name,
            email=settings.git_email,
        )
        if c_hash is None:
            raise ChapterError(f"Failed to commit review done for chapter {chapter_id}")
        return c_hash
