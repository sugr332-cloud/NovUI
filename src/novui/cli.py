"""Command-line interface for NovUI Controller."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
from typing import Any, Sequence

from novui.chapters import (
    ChapterError,
    add_chapter,
    read_chapter_meta,
    read_chapters_order,
)
from novui.claude_cli import run_claude
from novui.claudejob import ClaudeRunner
from novui.config import Settings, load_settings
from novui.container import run_container
from novui.draftjob import run_chapter_draft_job
from novui.finalize import discard_cycle, final_approve
from novui.jobrecord import load_job_record
from novui.jobrunner import ContainerRunner, jobs_dir
from novui.models import (
    fetch_agy_models,
    load_catalog,
    new_model_ids,
    save_catalog,
    select_model,
)
from novui.locks import RunLock
from novui.planjob import approve_plan, reject_plan, run_plan_job
from novui.requestflow import redraft, resolve_request, unresolved_requests
from novui.stateupdate import approve_state, reject_state, run_state_update, show_proposal
from novui.validatejob import (
    DECISION_ACTIONS,
    _read_branch_chapter_meta,
    _read_branch_yaml,
    decide_validation,
    find_chapter_branch,
    run_validate_job,
    skip_validation,
)
from novui.workrepo import CommitGuardError, MergeConflict, chapter_branches, head_commit
from novui.workinit import init_work
from novui.works import get_work
from novui.yamlio import dumps_yaml, load_yaml


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="novui", description="NovUI Novel Production Controller")
    subparsers = parser.add_subparsers(dest="subcommand", required=True)

    # init-work <path> --key <work_key> --title <title>
    p_init = subparsers.add_parser("init-work")
    p_init.add_argument("path", help="Target repository directory path")
    p_init.add_argument("--key", required=True, help="Novel work key")
    p_init.add_argument("--title", required=True, help="Novel title")

    # add-chapter --work <key> --id <chapter_id> --title <title> --outline-file <path>
    p_add_ch = subparsers.add_parser("add-chapter")
    p_add_ch.add_argument("--work", required=True, help="Novel work key")
    p_add_ch.add_argument("--id", required=True, help="Chapter ID (e.g. ch-001)")
    p_add_ch.add_argument("--title", required=True, help="Chapter title")
    p_add_ch.add_argument("--outline-file", required=True, help="Path to outline file")

    # plan --work <key> --chapter <id>
    p_plan = subparsers.add_parser("plan")
    p_plan.add_argument("--work", required=True, help="Novel work key")
    p_plan.add_argument("--chapter", required=True, help="Chapter ID")

    # approve-plan --work <key> --chapter <id>
    p_app = subparsers.add_parser("approve-plan")
    p_app.add_argument("--work", required=True, help="Novel work key")
    p_app.add_argument("--chapter", required=True, help="Chapter ID")

    # reject-plan --work <key> --chapter <id>
    p_rej = subparsers.add_parser("reject-plan")
    p_rej.add_argument("--work", required=True, help="Novel work key")
    p_rej.add_argument("--chapter", required=True, help="Chapter ID")

    # draft --work <key> --chapter <id>
    p_draft = subparsers.add_parser("draft")
    p_draft.add_argument("--work", required=True, help="Novel work key")
    p_draft.add_argument("--chapter", required=True, help="Chapter ID")

    # validate --work <key> --chapter <id>
    p_validate = subparsers.add_parser("validate")
    p_validate.add_argument("--work", required=True, help="Novel work key")
    p_validate.add_argument("--chapter", required=True, help="Chapter ID")

    # decide --work <key> --chapter <id> --action <action> [--reason <text>]
    p_decide = subparsers.add_parser("decide")
    p_decide.add_argument("--work", required=True, help="Novel work key")
    p_decide.add_argument("--chapter", required=True, help="Chapter ID")
    p_decide.add_argument("--action", required=True, choices=DECISION_ACTIONS, help="Human decision")
    p_decide.add_argument("--reason", default=None, help="Reason (required for OVERRIDE and REQUEST_FIX)")

    # skip-validation --work <key> --chapter <id> --reason <text>
    p_skip = subparsers.add_parser("skip-validation")
    p_skip.add_argument("--work", required=True, help="Novel work key")
    p_skip.add_argument("--chapter", required=True, help="Chapter ID")
    p_skip.add_argument("--reason", required=True, help="Reason for skipping validation")

    # state-update --work <key> --chapter <id> [--replace]
    p_su = subparsers.add_parser("state-update")
    p_su.add_argument("--work", required=True, help="Novel work key")
    p_su.add_argument("--chapter", required=True, help="Chapter ID")
    p_su.add_argument("--replace", action="store_true", help="Supersede a pending proposal")

    # approve-state --work <key> --chapter <id>
    p_as = subparsers.add_parser("approve-state")
    p_as.add_argument("--work", required=True, help="Novel work key")
    p_as.add_argument("--chapter", required=True, help="Chapter ID")

    # reject-state --work <key> --chapter <id> --reason <text>
    p_rs = subparsers.add_parser("reject-state")
    p_rs.add_argument("--work", required=True, help="Novel work key")
    p_rs.add_argument("--chapter", required=True, help="Chapter ID")
    p_rs.add_argument("--reason", required=True, help="Reason for rejecting the proposal")

    # final-approve --work <key> --chapter <id>
    p_fa = subparsers.add_parser("final-approve")
    p_fa.add_argument("--work", required=True, help="Novel work key")
    p_fa.add_argument("--chapter", required=True, help="Chapter ID")

    # resolve-request --work <key> --chapter <id> --index <n> --decision <text>
    p_rr = subparsers.add_parser("resolve-request")
    p_rr.add_argument("--work", required=True, help="Novel work key")
    p_rr.add_argument("--chapter", required=True, help="Chapter ID")
    p_rr.add_argument("--index", required=True, type=int, help="Index of the request in requests.yaml")
    p_rr.add_argument("--decision", required=True, help="Human decision")

    # redraft --work <key> --chapter <id>
    p_rd = subparsers.add_parser("redraft")
    p_rd.add_argument("--work", required=True, help="Novel work key")
    p_rd.add_argument("--chapter", required=True, help="Chapter ID")

    # discard --work <key> --chapter <id> [--force]
    p_dc = subparsers.add_parser("discard")
    p_dc.add_argument("--work", required=True, help="Novel work key")
    p_dc.add_argument("--chapter", required=True, help="Chapter ID")
    p_dc.add_argument("--force", action="store_true", help="Discard a HUMAN_APPROVED job branch")

    # show --work <key> [--chapter <id>]
    p_show = subparsers.add_parser("show")
    p_show.add_argument("--work", required=True, help="Novel work key")
    p_show.add_argument("--chapter", default=None, help="Chapter ID")

    # models fetch / select
    p_models = subparsers.add_parser("models")
    models_sub = p_models.add_subparsers(dest="models_action", required=True)

    models_sub.add_parser("fetch")

    p_mselect = models_sub.add_parser("select")
    p_mselect.add_argument("--role", required=True, help="Role name")
    p_mselect.add_argument("--model", required=True, help="Model ID")
    p_mselect.add_argument("--work", default=None, help="Optional work key")

    return parser


def _report_job(record: dict[str, Any]) -> int:
    """Print job state and checks. Returns exit code: FAILED 2, WAITING_HUMAN 3, otherwise 0."""
    state = record.get("state")
    print(f"Job {record.get('job_id')} finished with state: {state}")
    checks = record.get("checks", [])
    for chk in checks:
        name = chk.get("name")
        status = chk.get("status")
        print(f"Check {name}: {status}")
        for d in chk.get("details", []):
            print(f"  - {d}")
    if state == "WAITING_HUMAN":
        history = record.get("history", [])
        reason = history[-1].get("reason") if history else None
        if reason:
            print(f"Reason: {reason}")
    if state == "FAILED":
        return 2
    elif state == "WAITING_HUMAN":
        return 3
    return 0


def _show_proposal_summary(settings: Settings, work: Any, chapter_id: str) -> None:
    """Print the latest state update proposal of the chapter (any status)."""
    proposal = show_proposal(settings, work, chapter_id)
    if proposal is None:
        return
    print(
        f"Proposal: {proposal['job_id']} [{proposal['status']}] "
        f"(branch head {proposal['branch_head'][:10]})"
    )
    if proposal["status_reason"]:
        print(f"  Reason: {proposal['status_reason']}")
    summary = proposal["summary"]
    print(f"  Summary events: {len(summary['events'])}")
    for c in summary["characters"]:
        print(
            f"  - {c['id']}: knowledge +{len(c['knowledge_added'])}, "
            f"relationship changes {len(c['relationship_changes'])}"
        )
    for f in summary["foreshadowing"]:
        print(f"  - foreshadowing {f['id']}: {f['status']}")
    if proposal["patches"]:
        for e in proposal["patches"]:
            print(f"  Patch: {e['patch']['target']} ({len(e['patch']['operations'])} operations)")
    else:
        print("  Patches: none (no setting file changes)")
    jpath = jobs_dir(settings, work.work_key) / f"{proposal['job_id']}.yaml"
    if jpath.is_file():
        for chk in load_job_record(jpath).get("checks", []):
            if chk.get("status") == "WARNING":
                print(f"  Warning check {chk['name']}: {'; '.join(chk.get('details', []))}")


def _show_cycle(settings: Settings, work: Any, chapter_id: str) -> None:
    """Print the job branch state, the latest proposal and unresolved requests of the chapter."""
    repo = work.path
    branches = chapter_branches(repo, chapter_id)
    if not branches:
        return
    if len(branches) > 1:
        print(f"Job branches: {', '.join(branches)} (more than one; resolve by hand)")
        return
    branch = branches[0]
    try:
        meta = _read_branch_chapter_meta(repo, branch, chapter_id)
        print(f"Job branch: {branch} (chapter state on the branch: {meta['state']})")
    except Exception as exc:
        print(f"Job branch: {branch} (chapter state unreadable: {exc})")
    _show_proposal_summary(settings, work, chapter_id)
    try:
        requests = _read_branch_yaml(repo, branch, f"chapters/{chapter_id}/requests.yaml")
    except Exception:
        requests = None
    if isinstance(requests, list) and requests:
        pending = unresolved_requests(requests)
        print(f"Requests: {len(pending)} unresolved of {sum(1 for r in requests if r.get('type') == 'request')}")
        for i in pending:
            print(f"  [{i}] {requests[i]['message']}")


def _unresolved_after(record: dict[str, Any], chapter_id: str) -> list[int]:
    path = Path(record["worktree"]) / "chapters" / chapter_id / "requests.yaml"
    return unresolved_requests(load_yaml(path)) if path.is_file() else []


def main(
    argv: Sequence[str] | None = None,
    *,
    settings: Settings | None = None,
    claude_runner: ClaudeRunner = run_claude,
    container_runner: ContainerRunner = run_container,
) -> int:
    """Entry point for NovUI CLI."""
    if argv is None:
        argv = sys.argv[1:]

    parser = _build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return 0 if exc.code == 0 else 1

    cfg = settings if settings is not None else load_settings()

    try:
        if args.subcommand == "init-work":
            target_path = Path(args.path)
            info = init_work(cfg, target_path, args.key, args.title)
            print(f"Work initialized: {info.work_key} at {info.path}")
            return 0

        elif args.subcommand == "add-chapter":
            work = get_work(cfg, args.work)
            outline_path = Path(args.outline_file)
            outline_text = outline_path.read_text(encoding="utf-8")
            c_hash = add_chapter(cfg, work, args.id, args.title, outline_text)
            print(f"Chapter {args.id} added (commit {c_hash})")
            return 0

        elif args.subcommand == "plan":
            work = get_work(cfg, args.work)
            record = run_plan_job(cfg, work, args.chapter, claude_runner=claude_runner)
            return _report_job(record)

        elif args.subcommand == "draft":
            work = get_work(cfg, args.work)
            record = run_chapter_draft_job(cfg, work, args.chapter, container_runner=container_runner)
            return _report_job(record)

        elif args.subcommand == "validate":
            work = get_work(cfg, args.work)
            record = run_validate_job(cfg, work, args.chapter, claude_runner=claude_runner)
            return _report_job(record)

        elif args.subcommand == "decide":
            work = get_work(cfg, args.work)
            record = decide_validation(cfg, work, args.chapter, args.action, args.reason)
            return _report_job(record)

        elif args.subcommand == "skip-validation":
            work = get_work(cfg, args.work)
            record = skip_validation(cfg, work, args.chapter, args.reason)
            return _report_job(record)

        elif args.subcommand == "state-update":
            work = get_work(cfg, args.work)
            record = run_state_update(
                cfg, work, args.chapter, replace=args.replace, claude_runner=claude_runner
            )
            rc = _report_job(record)
            if record.get("state") == "WAITING_HUMAN":
                _show_proposal_summary(cfg, work, args.chapter)
                print("Awaiting approval: run approve-state or reject-state.")
            return rc

        elif args.subcommand == "approve-state":
            work = get_work(cfg, args.work)
            record = approve_state(cfg, work, args.chapter)
            branch = find_chapter_branch(work.path, args.chapter)
            print(f"Job {record.get('job_id')} finished with state: {record.get('state')}")
            approval_id = record.get("approval_id")
            if approval_id:
                print(f"Approval-Id: {approval_id}")
            else:
                print("No approval record (the proposal has no setting file changes)")
            print(f"Chapter {args.chapter} is HUMAN_APPROVED (commit {head_commit(work.path, branch)})")
            return 0

        elif args.subcommand == "reject-state":
            work = get_work(cfg, args.work)
            record = reject_state(cfg, work, args.chapter, args.reason)
            print(f"Job {record.get('job_id')} finished with state: {record.get('state')}")
            print(f"State update proposal for chapter {args.chapter} rejected")
            return 0

        elif args.subcommand == "final-approve":
            work = get_work(cfg, args.work)
            try:
                commit = final_approve(cfg, work, args.chapter)
            except (MergeConflict, CommitGuardError) as exc:
                print(f"Final approval is waiting for a Human decision: {exc}")
                for path in exc.paths:
                    print(f"  - {path}")
                print("main was not changed. Resolve the cause and run final-approve again, or discard.")
                return 3
            print(f"Chapter {args.chapter} is FINAL (merge commit {commit})")
            return 0

        elif args.subcommand == "resolve-request":
            work = get_work(cfg, args.work)
            record = resolve_request(cfg, work, args.chapter, args.index, args.decision)
            print(f"Job {record.get('job_id')} finished with state: {record.get('state')}")
            remaining = _unresolved_after(record, args.chapter)
            print(f"Request {args.index} resolved; {len(remaining)} unresolved")
            if not remaining:
                print("All requests are resolved: run redraft to write the chapter again with these decisions.")
            return 0

        elif args.subcommand == "redraft":
            work = get_work(cfg, args.work)
            record = redraft(cfg, work, args.chapter, run_lock=RunLock(), container_runner=container_runner)
            print(
                "Note: the decisions apply to this chapter's text only. "
                "To keep them for later chapters, edit the setting files directly."
            )
            return _report_job(record)

        elif args.subcommand == "discard":
            work = get_work(cfg, args.work)
            discard_cycle(cfg, work, args.chapter, force=args.force)
            print(f"Job branch of chapter {args.chapter} discarded")
            return 0

        elif args.subcommand == "approve-plan":
            work = get_work(cfg, args.work)
            c_hash = approve_plan(cfg, work, args.chapter)
            print(f"Plan for chapter {args.chapter} approved (commit {c_hash})")
            return 0

        elif args.subcommand == "reject-plan":
            work = get_work(cfg, args.work)
            c_hash = reject_plan(cfg, work, args.chapter)
            print(f"Plan for chapter {args.chapter} rejected (commit {c_hash})")
            return 0

        elif args.subcommand == "show":
            work = get_work(cfg, args.work)
            repo = work.path
            if args.chapter:
                meta = read_chapter_meta(repo, args.chapter)
                if meta is None:
                    raise ChapterError(f"Chapter {args.chapter!r} not found in {work.work_key}")
                print(dumps_yaml(meta).strip())
                jdir = jobs_dir(cfg, work.work_key)
                if jdir.is_dir():
                    for p in sorted(jdir.glob("job-*.yaml")):
                        try:
                            jrec = load_job_record(p)
                            if jrec.get("chapter_id") == args.chapter:
                                history = jrec.get("history", [])
                                last_h = history[-1] if history else {}
                                print(
                                    f"Job: {jrec.get('job_id')} | "
                                    f"Type: {jrec.get('job_type')} | "
                                    f"State: {jrec.get('state')} | "
                                    f"Last: {last_h.get('state')} (event: {last_h.get('event')})"
                                )
                        except Exception:
                            pass
                _show_cycle(cfg, work, args.chapter)
            else:
                order = read_chapters_order(repo)
                for ch_id in order:
                    meta = read_chapter_meta(repo, ch_id)
                    title = meta.get("title", "") if meta else ""
                    state = meta.get("state", "UNKNOWN") if meta else "UNKNOWN"
                    print(f"{ch_id}: {title} [{state}]")
            return 0

        elif args.subcommand == "models":
            if args.models_action == "fetch":
                old_catalog = load_catalog(cfg)
                new_catalog = fetch_agy_models(cfg)
                save_catalog(cfg, new_catalog)
                new_ids = new_model_ids(old_catalog, new_catalog)
                if new_ids:
                    print(f"New models available: {', '.join(new_ids)}")
                else:
                    print("Catalog updated (no new models).")
                return 0

            elif args.models_action == "select":
                select_model(cfg, args.role, args.model, work_key=args.work)
                msg = f"Model for role {args.role!r} set to {args.model!r}"
                if args.work:
                    msg += f" for work {args.work!r}"
                print(msg)
                return 0

    except Exception as exc:
        sys.stderr.write(f"Error: {exc}\n")
        return 1

    return 0
