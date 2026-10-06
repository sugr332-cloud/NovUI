"""Draft job runner and execution lifecycle."""

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Sequence

from novui.agy_output import AgyOutputError, markers_to_requests, parse_agy_text
from novui.checks import (
    CheckResult,
    check_allowed_paths,
    check_append_only,
    check_char_range,
    check_cli_output,
    check_ignored_unchanged,
    compare_hash_records,
)
from novui.config import Settings
from novui.container import (
    PromptTooLarge,
    agy_command,
    build_agy_mounts,
    image_id,
    image_label,
    run_container,
)
from novui.gitinspect import (
    get_changes,
    get_ignored,
    git_protection_targets,
    hash_targets,
)
from novui.jobhome import job_home
from novui.jobrecord import apply_transition, new_job_record, save_job_record
from novui.locks import RunLock, can_accept_write_job
from novui.procrun import ProcResult
from novui.prompt import build_agy_prompt
from novui.schema import SchemaError, validate_or_raise
from novui.semantics import check_requests
from novui.states import JobEvent, JobState
from novui.workrepo import commit_all, create_job_worktree, remove_job_worktree
from novui.yamlio import dumps_yaml, load_yaml

ContainerRunner = Callable[..., ProcResult]


class JobRejected(Exception):
    """Raised when job cannot be accepted due to chapter lock."""


class RunLockBusy(Exception):
    """Raised when execution lock cannot be acquired."""


@dataclass(frozen=True)
class DraftJobRequest:
    work_key: str
    repo: Path  # 作品リポジトリ（main を checkout した main worktree）
    chapter_id: str
    job_id: str
    model: str
    context_paths: tuple[str, ...]
    instruction: str
    target_chars: tuple[int, int] | None = None
    needs_validation: bool = False
    # 本文（draft.md に書く文字列）を受け取り、追加の検査結果を返す（§11.3 の機械検査など）
    extra_checks: Callable[[str], Sequence[CheckResult]] | None = None


def jobs_dir(settings: Settings, work_key: str) -> Path:
    """Return jobs directory path for work_key: <data_dir>/works/<work_key>/jobs."""
    return settings.data_dir / "works" / work_key / "jobs"


def run_draft_job(
    settings: Settings,
    req: DraftJobRequest,
    run_lock: RunLock,
    *,
    container_runner: ContainerRunner = run_container,
) -> dict[str, Any]:
    """Execute a complete draft Job lifecycle.

    Returns the final job record dict.
    """
    # 1. can_accept_write_job
    if not can_accept_write_job(req.repo, req.chapter_id):
        raise JobRejected(f"Chapter {req.chapter_id} has unmerged branches")

    jdir = jobs_dir(settings, req.work_key)
    jdir.mkdir(parents=True, exist_ok=True)

    # 2. new_job_record in QUEUED state
    record = new_job_record(req.job_id, "draft", chapter_id=req.chapter_id)
    save_job_record(jdir, record)

    # 3. acquire run_lock
    if not run_lock.acquire(req.work_key, req.job_id):
        raise RunLockBusy(f"Run lock for work {req.work_key} is busy")

    jw = None
    try:
        # 5. create_job_worktree
        jw = create_job_worktree(
            req.repo,
            settings.worktree_root,
            req.work_key,
            req.chapter_id,
            req.job_id,
        )
        worktree = jw.path
        record["branch"] = jw.branch
        record["worktree"] = str(jw.path)
        record["base_commit"] = jw.base_commit
        save_job_record(jdir, record)

        # 6. build_agy_prompt & agy_command
        built = build_agy_prompt(worktree, req.context_paths, req.instruction)
        record["context"] = [{"path": e.path, "sha256": e.sha256} for e in built.context]
        save_job_record(jdir, record)

        try:
            cmd = agy_command(model=req.model, prompt=built.text)
        except PromptTooLarge as exc:
            record = apply_transition(
                record,
                JobEvent.NEEDS_HUMAN_INPUT,
                reason=f"prompt_too_large: {exc.size} > {exc.limit}",
            )
            remove_job_worktree(req.repo, jw, delete_branch=True)
            save_job_record(jdir, record)
            return record

        # 7. LOCK_ACQUIRED -> RUNNING
        record = apply_transition(record, JobEvent.LOCK_ACQUIRED)
        save_job_record(jdir, record)

        # 8. Pre-execution snapshots
        pre_hashes = hash_targets(git_protection_targets(worktree))
        pre_ignored = get_ignored(worktree)

        # 9. Run container
        log_dir = jdir / f"{req.job_id}-logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        timeout_sec = float(settings.timeouts["agy_draft"])

        with job_home(settings.jobhome_root, req.job_id, settings.agy_token_path) as jh:
            mounts = build_agy_mounts(job_home=jh, jobhome_root=settings.jobhome_root)
            proc_res = container_runner(
                name=f"novui-{req.work_key}-{req.job_id}",
                image=settings.agy_image,
                mounts=mounts,
                command=cmd,
                timeout_seconds=timeout_sec,
                log_dir=log_dir,
            )

        # 10. Record CLI, container, and run info
        v = image_label(settings.agy_image, "org.novui.agy.version")
        try:
            img_id = image_id(settings.agy_image)
        except Exception:
            img_id = "unknown"

        record["cli"] = {
            "name": "agy",
            "version": v or "unknown",
            "model": req.model,
            "actual_model": None,
        }
        record["container"] = {
            "image_tag": settings.agy_image,
            "image_id": img_id,
        }
        record["run"] = {
            "exit_code": proc_res.exit_code,
            "elapsed_seconds": proc_res.elapsed_seconds,
            "timed_out": proc_res.timed_out,
            "signal": proc_res.signal_sent,
            "timeout_seconds": int(timeout_sec),
        }

        # 11. Timed out handling
        if proc_res.timed_out:
            record = apply_transition(record, JobEvent.TIMED_OUT)
            save_job_record(jdir, record)
            return record

        # 12. CLI_EXITED -> CHECKING & immediate post-hashes
        record = apply_transition(record, JobEvent.CLI_EXITED)
        save_job_record(jdir, record)
        post_hashes = hash_targets(git_protection_targets(worktree))

        # 13. Checks
        checks: list[CheckResult] = []
        checks.append(check_cli_output(proc_res.exit_code, proc_res.stdout))
        checks.append(compare_hash_records(pre_hashes, post_hashes))

        parsed = None
        try:
            parsed = parse_agy_text(proc_res.stdout)
            checks.append(CheckResult(name="agy_output", status="PASS"))
        except AgyOutputError as exc:
            checks.append(CheckResult(name="agy_output", status="FAIL", details=(exc.kind,)))

        # 13.4 Controller writes draft.md and optionally requests.yaml if checks 1-3 PASS
        first_three_pass = all(c.status == "PASS" for c in checks)
        if first_three_pass and parsed is not None:
            draft_relpath = f"chapters/{req.chapter_id}/draft.md"
            draft_file = worktree / draft_relpath
            draft_file.parent.mkdir(parents=True, exist_ok=True)
            draft_file.write_text(parsed.text, encoding="utf-8")

            if parsed.markers:
                req_relpath = f"chapters/{req.chapter_id}/requests.yaml"
                req_file = worktree / req_relpath
                before_req_bytes = req_file.read_bytes() if req_file.is_file() else None
                new_requests = markers_to_requests(
                    parsed.markers,
                    job_id=req.job_id,
                    chapter_id=req.chapter_id,
                )
                new_yaml = dumps_yaml(new_requests)
                if before_req_bytes is not None and len(before_req_bytes) > 0:
                    sep = b"" if before_req_bytes.endswith(b"\n") else b"\n"
                    new_req_bytes = before_req_bytes + sep + new_yaml.encode("utf-8")
                else:
                    new_req_bytes = new_yaml.encode("utf-8")
                req_file.parent.mkdir(parents=True, exist_ok=True)
                req_file.write_bytes(new_req_bytes)

                loaded_reqs = load_yaml(req_file)
                validate_or_raise(loaded_reqs, "requests")
                sem_errors = check_requests(loaded_reqs)
                if sem_errors:
                    raise SchemaError(
                        f"Semantic validation failed for requests: {sem_errors}",
                        errors=sem_errors,
                    )
                append_chk = check_append_only(before_req_bytes, new_req_bytes)
                checks.append(append_chk)

        # 13.5 Allowed paths check
        allowed = {f"chapters/{req.chapter_id}/draft.md"}
        if parsed is not None and parsed.markers:
            allowed.add(f"chapters/{req.chapter_id}/requests.yaml")
        allowed_chk = check_allowed_paths(get_changes(worktree), allowed)
        checks.append(allowed_chk)

        # 13.6 Ignored files unchanged check
        post_ignored = get_ignored(worktree)
        ignored_chk = check_ignored_unchanged(pre_ignored, post_ignored)
        checks.append(ignored_chk)

        # 13.7 Character count range check if configured
        if req.target_chars is not None and parsed is not None:
            min_c, max_c = req.target_chars
            char_chk = check_char_range(parsed.text, min_c, max_c)
            checks.append(char_chk)

        # 13.8 Additional checks supplied by the caller
        if req.extra_checks is not None and parsed is not None:
            checks.extend(req.extra_checks(parsed.text))

        record["checks"] = [
            {"name": c.name, "status": c.status, "details": list(c.details)}
            for c in checks
        ]
        save_job_record(jdir, record)

        # 14. Check if any check failed
        has_fail = any(c["status"] == "FAIL" for c in record["checks"])
        if has_fail:
            record = apply_transition(record, JobEvent.CHECK_FAILED)
            save_job_record(jdir, record)
            return record

        # 15. Commit all changes
        commit_all(
            worktree,
            f"draft {req.chapter_id} by {req.job_id}",
            [("NovUI-Job", req.job_id)],
            name=settings.git_name,
            email=settings.git_email,
        )

        # 16. Needs human input if markers present, else check passed
        if parsed is not None and len(parsed.markers) > 0:
            record = apply_transition(
                record,
                JobEvent.NEEDS_HUMAN_INPUT,
                reason=f"undefined_settings: {len(parsed.markers)}",
            )
        else:
            record = apply_transition(
                record,
                JobEvent.CHECK_PASSED,
                needs_validation=req.needs_validation,
            )
        save_job_record(jdir, record)
        return record

    except BaseException:
        # 17. Transition to FAILED if possible on unexpected error
        curr_st = record.get("state")
        if curr_st == JobState.RUNNING.name:
            try:
                record = apply_transition(record, JobEvent.CLI_EXITED)
                record = apply_transition(record, JobEvent.CHECK_FAILED)
                save_job_record(jdir, record)
            except Exception:
                pass
        elif curr_st == JobState.CHECKING.name:
            try:
                record = apply_transition(record, JobEvent.CHECK_FAILED)
                save_job_record(jdir, record)
            except Exception:
                pass
        raise
    finally:
        run_lock.release(req.work_key, req.job_id)
