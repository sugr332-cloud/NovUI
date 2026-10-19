"""Command-line interface for NovUI Controller."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
from typing import Any, Sequence

from novui.chapters import (
    ChapterError,
    add_chapter,
    clear_review_required,
    read_chapter_meta,
    read_chapters_order,
)
from novui.claude_cli import run_claude
from novui.claudejob import ClaudeRunner
from novui.config import Settings, load_settings
from novui.container import run_container
from novui.draftjob import run_chapter_draft_job
from novui.editjob import EDIT_JOB_TYPES, cancel_edit_job, run_chapter_rewrite_job, run_range_edit_job
from novui.export import export_chapter, write_export
from novui.finalize import discard_cycle, final_approve, waiting_jobs_on_branch
from novui.jobqueue import cancel_item, fix_findings, load_queue, numbered_findings, queue_lock, run_queue
from novui.jobrecord import apply_transition, load_job_record, save_job_record
from novui.jobrunner import ContainerRunner, jobs_dir
from novui.models import (
    fetch_agy_models,
    load_catalog,
    new_model_ids,
    save_catalog,
    select_model,
)
from novui.locks import RunLock
from novui.planjob import approve_plan, reject_plan, run_plan_job, run_plan_revision_job
from novui.requestflow import redraft, resolve_request, unresolved_requests
from novui.routingjob import ROUTING_SCOPES, approve_routing, reject_routing, run_routing_job
from novui.settingchange import approve_setting, load_setting_proposal, reject_setting
from novui.states import JobEvent, JobState
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

    # edit-range --work <key> --chapter <id> --anchor <text> [--before <text>] [--after <text>] --reason <text>
    #            [--retry <job_id>]
    p_er = subparsers.add_parser("edit-range")
    p_er.add_argument("--work", required=True, help="Novel work key")
    p_er.add_argument("--chapter", required=True, help="Chapter ID")
    p_er.add_argument("--anchor", default=None, help="Text of the range to rewrite (must match exactly once)")
    p_er.add_argument("--before", default="", help="Text just before the range (to make the match unique)")
    p_er.add_argument("--after", default="", help="Text just after the range (to make the match unique)")
    p_er.add_argument("--reason", default=None, help="How to rewrite the range")
    p_er.add_argument("--retry", default=None, help="Run a WAITING_HUMAN range_edit Job again (same range and reason)")

    # rewrite --work <key> --chapter <id> --reason <text> | --retry <job_id>
    p_rw = subparsers.add_parser("rewrite")
    p_rw.add_argument("--work", required=True, help="Novel work key")
    p_rw.add_argument("--chapter", required=True, help="Chapter ID")
    p_rw.add_argument("--reason", default=None, help="How to rewrite the chapter")
    p_rw.add_argument("--retry", default=None, help="Run a WAITING_HUMAN chapter_rewrite Job again (same reason)")

    # revise-plan --work <key> --chapter <id> --instruction <text>
    p_rp = subparsers.add_parser("revise-plan")
    p_rp.add_argument("--work", required=True, help="Novel work key")
    p_rp.add_argument("--chapter", required=True, help="Chapter ID (PLAN_APPROVED, no job branch)")
    p_rp.add_argument("--instruction", required=True, help="How to revise the plan")

    # fix --work <key> --chapter <id> --findings I1,W2 [--note <text>] [--force]
    p_fx = subparsers.add_parser("fix")
    p_fx.add_argument("--work", required=True, help="Novel work key")
    p_fx.add_argument("--chapter", required=True, help="Chapter ID")
    p_fx.add_argument("--findings", required=True, help="Finding numbers shown by show, comma separated (I1,W2)")
    p_fx.add_argument("--note", default=None, help="Extra instruction for the edits")
    p_fx.add_argument("--force", action="store_true", help="Allow a third fix round on the job branch (§11.5)")

    # run-queue --work <key> [--retry]
    p_rq = subparsers.add_parser("run-queue")
    p_rq.add_argument("--work", required=True, help="Novel work key")
    p_rq.add_argument("--retry", action="store_true", help="Run the waiting / failed item at the head again")

    # queue --work <key> [--cancel <item_id>]
    p_q = subparsers.add_parser("queue")
    p_q.add_argument("--work", required=True, help="Novel work key")
    p_q.add_argument("--cancel", default=None, help="Cancel a queue item (Q-0001)")

    # instruct --work <key> [--chapter <id>] --scope range|chapter|plan|setting [--anchor ...] <instruction>
    p_in = subparsers.add_parser("instruct")
    p_in.add_argument("--work", required=True, help="Novel work key")
    p_in.add_argument("--chapter", default=None, help="Chapter ID (optional for scope setting)")
    p_in.add_argument("--scope", required=True, choices=ROUTING_SCOPES, help="What the instruction is about")
    p_in.add_argument("--anchor", default=None, help="scope range: text of the range")
    p_in.add_argument("--before", default="", help="scope range: text just before the range")
    p_in.add_argument("--after", default="", help="scope range: text just after the range")
    p_in.add_argument("instruction", help="The instruction to Claude")

    # approve-routing --work <key> --id <INS-0001> [--discard-branch]
    p_ar = subparsers.add_parser("approve-routing")
    p_ar.add_argument("--work", required=True, help="Novel work key")
    p_ar.add_argument("--id", required=True, help="Instruction ID (INS-0001)")
    p_ar.add_argument("--discard-branch", action="store_true", help="Discard the job branch (plan revision)")

    # reject-routing --work <key> --id <INS-0001>
    p_xr = subparsers.add_parser("reject-routing")
    p_xr.add_argument("--work", required=True, help="Novel work key")
    p_xr.add_argument("--id", required=True, help="Instruction ID (INS-0001)")

    # approve-setting --work <key> --id <INS-0001>
    p_aset = subparsers.add_parser("approve-setting")
    p_aset.add_argument("--work", required=True, help="Novel work key")
    p_aset.add_argument("--id", required=True, help="Instruction ID (INS-0001)")

    # reject-setting --work <key> --id <INS-0001> --reason <text>
    p_rset = subparsers.add_parser("reject-setting")
    p_rset.add_argument("--work", required=True, help="Novel work key")
    p_rset.add_argument("--id", required=True, help="Instruction ID (INS-0001)")
    p_rset.add_argument("--reason", required=True, help="Reason for rejecting the setting change")

    # setting-proposal --work <key> --id <INS-0001>
    p_sp = subparsers.add_parser("setting-proposal")
    p_sp.add_argument("--work", required=True, help="Novel work key")
    p_sp.add_argument("--id", required=True, help="Instruction ID (INS-0001)")

    # cancel-job --work <key> --job <job_id>
    p_cj = subparsers.add_parser("cancel-job")
    p_cj.add_argument("--work", required=True, help="Novel work key")
    p_cj.add_argument("--job", required=True, help="WAITING_HUMAN range_edit / chapter_rewrite / setting_change Job ID")

    # discard --work <key> --chapter <id> [--force]
    p_dc = subparsers.add_parser("discard")
    p_dc.add_argument("--work", required=True, help="Novel work key")
    p_dc.add_argument("--chapter", required=True, help="Chapter ID")
    p_dc.add_argument("--force", action="store_true", help="Discard a HUMAN_APPROVED job branch")

    # export --work <key> --chapter <id> --out <dir> [--route <r>] [--engine-chapter <name>]
    p_ex = subparsers.add_parser("export")
    p_ex.add_argument("--work", required=True, help="Novel work key")
    p_ex.add_argument("--chapter", required=True, help="Chapter ID (must be FINAL)")
    p_ex.add_argument("--out", required=True, help="Output directory (engine assets/scripts)")
    p_ex.add_argument("--route", default="common", help="Engine route directory (default: common)")
    p_ex.add_argument("--engine-chapter", default=None, help="Engine chapter id (default: chapter_NN)")

    # review-done --work <key> --chapter <id>
    p_rdone = subparsers.add_parser("review-done")
    p_rdone.add_argument("--work", required=True, help="Novel work key")
    p_rdone.add_argument("--chapter", required=True, help="Chapter ID")

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
        review = _read_branch_yaml(repo, branch, f"chapters/{chapter_id}/review.yaml")
    except Exception:
        review = None
    if isinstance(review, dict):
        decision = (review.get("decision") or {}).get("action")
        print(f"Review: {review.get('job_id')} (decision: {decision or 'none'})")
        for fid, f in numbered_findings(review).items():
            mark = f["severity"] or "writing"
            anchor = "" if f["anchor"] else " (no anchor)"
            print(f"  {fid} [{mark} {f['check']}] {f['message']}{anchor}")
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


def _waiting_edit_job(settings: Settings, work: Any, record: dict[str, Any]) -> tuple[str, str] | None:
    """(job ID, job type) of the WAITING_HUMAN edit Job on the branch of record, if any."""
    for rec in waiting_jobs_on_branch(settings, work, record["branch"]):
        if rec["job_type"] in EDIT_JOB_TYPES:
            return str(rec["job_id"]), str(rec["job_type"])
    return None


def _print_routing(doc: dict[str, Any]) -> None:
    """Print Claude's routing so that the Human can approve or reject it."""
    r = doc["routing"]
    target_label = doc["chapter_id"] if doc.get("chapter_id") is not None else "work"
    print(f"Routing {doc['instruction_id']} [{doc['status']}] for {target_label} (scope {doc['scope']})")
    if r["scope_mismatch"]:
        print(f"Scope mismatch: {r['scope_mismatch_reason']}")
    for i, job in enumerate(r["proposed_jobs"]):
        dep = f" (after {job['depends_on']})" if job["depends_on"] is not None else ""
        print(f"  [{i}] {job['job_type']}{dep}: {job['summary']}")
        if job["anchor"] is not None:
            print(f"      range: {job['anchor']['text']}")
    for imp in r["impact"]:
        print(f"Impact: {imp['target']}: {imp['reason']}")
    for q in r["questions"]:
        print(f"Question: {q}")
    if doc["status"] == "pending":
        print(f"Approve with approve-routing --id {doc['instruction_id']}, or reject-routing.")
    else:
        print(f"Answer the questions in a new instruct, and reject-routing --id {doc['instruction_id']}.")


def _show_setting_proposal(doc: dict[str, Any]) -> None:
    """Print a setting change proposal (§3.2, §3.3)."""
    print(f"Setting Proposal {doc['instruction_id']} [{doc['status']}] (job {doc['job_id']})")
    if doc.get("status_reason"):
        print(f"  Reason: {doc['status_reason']}")
    targets = [p["patch"]["target"] for p in doc.get("patches", [])]
    print(f"Targets: {', '.join(targets)}")
    for e in doc.get("patches", []):
        p = e["patch"]
        print(f"  Patch: {p['target']} (reason: {p['reason']})")
        for op in p.get("operations", []):
            print(f"    - {op.get('op')}: {op.get('path')}")
    impacted = doc.get("impact", [])
    if impacted:
        print("Impacted chapters:")
        for imp in impacted:
            print(f"  - {imp['chapter_id']}: {imp['reason']}")
    else:
        print("Impacted chapters: none")
    notes = doc.get("impact_notes") or []
    if notes:
        print("Notes: " + ", ".join(notes))
    else:
        print("Notes: none")


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
                waiting_edit = _waiting_edit_job(cfg, work, record)
                if waiting_edit is not None:
                    command = "edit-range" if waiting_edit[1] == "range_edit" else "rewrite"
                    print(f"All requests are resolved: run {command} --retry {waiting_edit[0]} "
                          "to run the edit again with these decisions.")
                else:
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

        elif args.subcommand == "edit-range":
            work = get_work(cfg, args.work)
            if args.retry is not None:
                if args.anchor is not None or args.reason is not None:
                    raise ValueError("--retry takes the range and the reason from the Job; omit --anchor and --reason")
                old = load_job_record(jobs_dir(cfg, work.work_key) / f"{args.retry}.yaml")
                if not old.get("edit"):
                    raise ValueError(f"{args.retry} is not a range_edit Job")
                anchor, reason, source = old["edit"]["anchor"], old["edit"]["reason"], old["edit"]["source"]
            else:
                if args.anchor is None or args.reason is None:
                    raise ValueError("--anchor and --reason are required (or --retry <job_id>)")
                anchor = {"text": args.anchor, "before": args.before, "after": args.after}
                reason, source = args.reason, {"kind": "human"}
            with queue_lock(cfg, work.work_key):
                record = run_range_edit_job(
                    cfg, work, args.chapter, anchor=anchor, reason=reason, source=source, retry_of=args.retry,
                    run_lock=RunLock(), container_runner=container_runner,
                )
            return _report_job(record)

        elif args.subcommand == "rewrite":
            work = get_work(cfg, args.work)
            if args.retry is not None:
                if args.reason is not None:
                    raise ValueError("--retry takes the reason from the Job; omit --reason")
                old = load_job_record(jobs_dir(cfg, work.work_key) / f"{args.retry}.yaml")
                if old["job_type"] != "chapter_rewrite" or not old.get("edit"):
                    raise ValueError(f"{args.retry} is not a chapter_rewrite Job")
                reason, source = old["edit"]["reason"], old["edit"]["source"]
            else:
                if args.reason is None:
                    raise ValueError("--reason is required (or --retry <job_id>)")
                reason, source = args.reason, {"kind": "human"}
            with queue_lock(cfg, work.work_key):
                record = run_chapter_rewrite_job(
                    cfg, work, args.chapter, reason=reason, source=source, retry_of=args.retry,
                    run_lock=RunLock(), container_runner=container_runner,
                )
            return _report_job(record)

        elif args.subcommand == "revise-plan":
            work = get_work(cfg, args.work)
            record = run_plan_revision_job(cfg, work, args.chapter, args.instruction, claude_runner=claude_runner)
            rc = _report_job(record)
            if record.get("state") == "COMPLETED":
                print(f"Chapter {args.chapter} is PLANNED: check the plan and run approve-plan before drafting.")
            return rc

        elif args.subcommand == "fix":
            work = get_work(cfg, args.work)
            ids = [f.strip() for f in args.findings.split(",") if f.strip()]
            items = fix_findings(cfg, work, args.chapter, ids, note=args.note, force=args.force)
            for item in items:
                print(f"Queued {item['item_id']}: range_edit {item['chapter_id']} ({item['source']['finding']})")
            print("Run run-queue to execute them.")
            return 0

        elif args.subcommand == "run-queue":
            work = get_work(cfg, args.work)
            records = run_queue(cfg, work, retry=args.retry, run_lock=RunLock(),
                                container_runner=container_runner, claude_runner=claude_runner, report=print)
            rc = 0
            for record in records:
                rc = _report_job(record)
            return rc

        elif args.subcommand == "queue":
            work = get_work(cfg, args.work)
            if args.cancel is not None:
                item = cancel_item(cfg, work.work_key, args.cancel)
                print(f"{item['item_id']} cancelled")
                return 0
            items = load_queue(cfg, work.work_key)
            if not items:
                print("The queue is empty.")
            for item in items:
                where = f" {item['source']['finding']}" if item["source"].get("finding") else ""
                job = f" job {item['job_id']}" if item["job_id"] else ""
                note = f" — {item['note']}" if item["note"] else ""
                print(f"{item['item_id']} [{item['status']}] {item['job_type']} {item['chapter_id']}{where}{job}{note}")
            return 0

        elif args.subcommand == "instruct":
            work = get_work(cfg, args.work)
            anchor = None
            if args.anchor is not None:
                anchor = {"text": args.anchor, "before": args.before, "after": args.after}
            record, doc = run_routing_job(cfg, work, args.chapter, args.scope, args.instruction, anchor=anchor,
                                          claude_runner=claude_runner)
            rc = _report_job(record)
            if doc is not None:
                _print_routing(doc)
            return rc

        elif args.subcommand == "approve-routing":
            work = get_work(cfg, args.work)
            items = approve_routing(cfg, work, args.id, discard_branch=args.discard_branch)
            for item in items:
                print(f"Queued {item['item_id']}: {item['job_type']} {item['chapter_id']}")
            print("Run run-queue to execute them.")
            return 0

        elif args.subcommand == "reject-routing":
            work = get_work(cfg, args.work)
            reject_routing(cfg, work, args.id)
            print(f"{args.id} rejected and removed")
            return 0

        elif args.subcommand == "approve-setting":
            work = get_work(cfg, args.work)
            doc = approve_setting(cfg, work, args.id)
            print(f"Setting proposal {args.id} approved (commit {doc['commit']})")
            if doc.get("approval_id"):
                print(f"Approval-Id: {doc['approval_id']}")
            return 0

        elif args.subcommand == "reject-setting":
            work = get_work(cfg, args.work)
            doc = reject_setting(cfg, work, args.id, args.reason)
            print(f"Setting proposal {args.id} rejected")
            return 0

        elif args.subcommand == "setting-proposal":
            work = get_work(cfg, args.work)
            doc = load_setting_proposal(cfg, work.work_key, args.id)
            _show_setting_proposal(doc)
            return 0

        elif args.subcommand == "cancel-job":
            work = get_work(cfg, args.work)
            jpath = jobs_dir(cfg, work.work_key) / f"{args.job}.yaml"
            if jpath.is_file():
                rec = load_job_record(jpath)
                if rec.get("job_type") == "setting_change":
                    if rec.get("state") != JobState.WAITING_HUMAN.name:
                        raise ChapterError(f"{args.job} is {rec.get('state')}, not WAITING_HUMAN")
                    rec = apply_transition(rec, JobEvent.HUMAN_CANCEL, reason="cancelled by Human")
                    save_job_record(jobs_dir(cfg, work.work_key), rec)
                    print(f"Job {rec['job_id']} finished with state: {rec['state']}")
                    return 0
            record = cancel_edit_job(cfg, work, args.job)
            print(f"Job {record['job_id']} finished with state: {record['state']}")
            return 0

        elif args.subcommand == "discard":
            work = get_work(cfg, args.work)
            discard_cycle(cfg, work, args.chapter, force=args.force)
            print(f"Job branch of chapter {args.chapter} discarded")
            return 0

        elif args.subcommand == "export":
            work = get_work(cfg, args.work)
            files = export_chapter(work.path, args.chapter, route=args.route, engine_chapter=args.engine_chapter)
            for path in write_export(files, Path(args.out)):
                print(f"wrote {path}")
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

        elif args.subcommand == "review-done":
            work = get_work(cfg, args.work)
            c_hash = clear_review_required(cfg, work, args.chapter)
            print(f"Review required cleared for chapter {args.chapter} (commit {c_hash})")
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
                    branches = chapter_branches(repo, ch_id)
                    if branches:
                        meta = _read_branch_chapter_meta(repo, branches[-1], ch_id)
                    else:
                        meta = read_chapter_meta(repo, ch_id)
                    title = meta.get("title", "") if meta else ""
                    state = meta.get("state", "UNKNOWN") if meta else "UNKNOWN"
                    line = f"{ch_id}: {title} [{state}]"
                    if meta and meta.get("review_required"):
                        line += " (review required)"
                        print(line)
                        for reason in meta.get("review_reasons", []):
                            print(f"  - {reason['code']}: {reason['detail']} ({reason['at']})")
                    else:
                        print(line)
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
