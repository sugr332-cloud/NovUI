"""Chapter management and state transitions on main branch."""

from pathlib import Path
import re
from typing import Mapping, Sequence

from novui.config import Settings
from novui.gitinspect import run_git
from novui.locks import can_accept_write_job
from novui.paths import ensure_within, is_safe_relpath
from novui.schema import SchemaError, validate_or_raise
from novui.semantics import check_chapter
from novui.states import ChapterEvent, ChapterState, next_chapter_state
from novui.workrepo import commit_all
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
