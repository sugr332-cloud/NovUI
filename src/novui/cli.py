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
from novui.jobrecord import load_job_record
from novui.jobrunner import jobs_dir
from novui.models import (
    fetch_agy_models,
    load_catalog,
    new_model_ids,
    save_catalog,
    select_model,
)
from novui.planjob import approve_plan, reject_plan, run_plan_job
from novui.workinit import init_work
from novui.works import get_work
from novui.yamlio import dumps_yaml


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


def main(
    argv: Sequence[str] | None = None,
    *,
    settings: Settings | None = None,
    claude_runner: ClaudeRunner = run_claude,
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
            state = record.get("state")
            print(f"Job {record.get('job_id')} finished with state: {state}")
            checks = record.get("checks", [])
            for chk in checks:
                name = chk.get("name")
                status = chk.get("status")
                print(f"Check {name}: {status}")
                for d in chk.get("details", []):
                    print(f"  - {d}")
            if state == "FAILED":
                return 2
            elif state == "WAITING_HUMAN":
                return 3
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
