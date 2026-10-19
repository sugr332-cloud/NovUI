"""setting_change Job, proposals, approval and rejection (3-B2, phase3b-design §3).

Modifies characters/<ID>.yaml and foreshadowing/registry.yaml via Claude in 3 stages:
(1) setting_change_plan (targets and changes),
(2) state_patch per target,
(3) impact_analysis.
Keeps proposals in Controller data until Human approves or rejects.
"""

from __future__ import annotations

import difflib
from pathlib import Path
import re
from typing import Any, Mapping, Sequence

from novui.approvals import (
    approval_rel,
    reserve_approval_id,
    APPROVALS_DIR,
)
from novui.chapters import (
    ChapterError,
    ensure_main_ready,
    mark_review_required,
    read_chapters_order,
    review_marked,
    chapter_branches,
)
from novui.checks import CheckResult, check_allowed_paths
from novui.claude_cli import run_claude
from novui.claudejob import (
    ClaudeCallOutcome,
    ClaudeRunner,
    claude_version,
    run_claude_call,
)
from novui.config import Settings
from novui.gitinspect import get_changes, run_git
from novui.ids import next_job_id
from novui.impact import analyze_impact
from novui.jobrecord import (
    apply_transition,
    new_job_record,
    now_iso,
    save_job_record,
)
from novui.jobrunner import jobs_dir
from novui.prompt import build_claude_prompt, load_prompt_template
from novui.proposals import ProposalError
from novui.schema import SchemaError, validate_or_raise
from novui.semantics import check_approval, check_chapter, check_setting_proposal
from novui.settingcheck import (
    REGISTRY_TARGET,
    check_setting_patch_policy,
    check_setting_result,
    is_setting_target,
)
from novui.statecheck import (
    check_patch_target,
    dry_run_patch,
    next_knowledge_number,
    pointer_hints,
)
from novui.statepatch import (
    apply_patch,
    file_sha256,
    is_noop_patch,
    patch_set_sha256,
    patch_sha256,
)
from novui.states import JobEvent, JobState
from novui.textformat import read_text_format
from novui.validatejob import _claude_check, _restore_paths
from novui.workrepo import commit_all, head_commit
from novui.works import WorkInfo
from novui.yamlio import dumps_yaml, load_yaml, write_yaml_atomic

_F_NUM_RE = re.compile(r"^F([0-9]+)$")


def setting_context(repo: Path) -> list[str]:
    """Return setting context files in the same order as collect_plan_context without chapter summary/outline."""
    candidates: list[str] = [
        "project.yaml",
        "rules/style.md",
        "rules/prohibited.yaml",
        "chapters-order.yaml",
    ]
    world_dir = repo / "world"
    if world_dir.is_dir():
        for p in sorted(world_dir.rglob("*")):
            if p.is_file():
                candidates.append(str(p.relative_to(repo)))

    chars_dir = repo / "characters"
    if chars_dir.is_dir():
        for p in sorted(chars_dir.glob("*.yaml")):
            if p.is_file():
                candidates.append(str(p.relative_to(repo)))

    candidates.append("foreshadowing/registry.yaml")
    candidates.append("plot/timeline.yaml")

    try:
        if read_text_format(repo) == "script":
            candidates += ["assets/backgrounds.yaml", "flags/registry.yaml"]
    except (ChapterError, Exception):
        pass

    result: list[str] = []
    for rel_path in candidates:
        if (repo / rel_path).is_file() and rel_path not in result:
            result.append(rel_path)
    return result


def setting_proposals_dir(settings: Settings, work_key: str) -> Path:
    return settings.data_dir / "works" / work_key / "setting_proposals"


def _setting_proposal_path(settings: Settings, work_key: str, instruction_id: str) -> Path:
    return setting_proposals_dir(settings, work_key) / f"{instruction_id}.yaml"


def find_setting_proposals(settings: Settings, work_key: str) -> list[dict[str, Any]]:
    pdir = setting_proposals_dir(settings, work_key)
    if not pdir.is_dir():
        return []
    results = []
    for p in sorted(pdir.glob("INS-*.yaml")):
        doc = load_yaml(p)
        if isinstance(doc, dict):
            results.append(doc)
    return results


def load_setting_proposal(settings: Settings, work_key: str, instruction_id: str) -> dict[str, Any]:
    path = _setting_proposal_path(settings, work_key, instruction_id)
    if not path.is_file():
        raise ChapterError(f"setting proposal not found: {instruction_id}")
    doc = load_yaml(path)
    if not isinstance(doc, dict):
        raise ChapterError(f"setting proposal {instruction_id} is not a mapping")
    return doc


def save_setting_proposal(settings: Settings, work_key: str, doc: Mapping[str, Any]) -> None:
    data = dict(doc)
    validate_or_raise(data, "setting_proposal")
    sem_errs = check_setting_proposal(data)
    if sem_errs:
        raise SchemaError(f"Semantic validation failed for setting_proposal: {sem_errs}", errors=sem_errs)
    path = _setting_proposal_path(settings, work_key, data["instruction_id"])
    path.parent.mkdir(parents=True, exist_ok=True)
    write_yaml_atomic(path, data)


def build_instruction_approval(
    approval_id: str,
    *,
    instruction_id: str,
    patches: Sequence[Mapping[str, Any]],
    approved_at: str | None = None,
) -> dict[str, Any]:
    """Build approval record for an instruction setting change (source: instruction)."""
    if not patches:
        raise ValueError("patches must not be empty: setting change without patches has no approval record")
    approval_rel(approval_id)
    doc: dict[str, Any] = {
        "approval_id": approval_id,
        "source": "instruction",
        "instruction_id": instruction_id,
        "job_id": None,
        "patch_sha256": patch_set_sha256(patches),
        "targets": sorted(p["target"] for p in patches),
        "approved_by": "human",
        "approved_at": approved_at if approved_at is not None else now_iso(),
    }
    validate_or_raise(doc, "approval")
    sem_errors = check_approval(doc)
    if sem_errors:
        raise SchemaError(f"Semantic validation failed for approval: {sem_errors}", errors=sem_errors)
    return doc


def run_setting_change_job(
    settings: Settings,
    work: WorkInfo,
    *,
    instruction_id: str,
    instruction: str,
    chapter_id: str | None = None,
    job_id: str | None = None,
    claude_runner: ClaudeRunner = run_claude,
) -> dict[str, Any]:
    """Execute a setting_change Job and save a setting_proposal (phase3b-design §3).

    Always reads main. Never writes to main. If questions arise, WAITING_HUMAN and no proposal.
    On normal completion, proposal is saved and Job is COMPLETED.
    """
    repo = work.path
    ensure_main_ready(repo)
    base_commit = head_commit(repo, "main")

    actual_job_id = job_id or next_job_id(settings, work.work_key)
    jdir = jobs_dir(settings, work.work_key)
    jdir.mkdir(parents=True, exist_ok=True)

    record = new_job_record(actual_job_id, "setting_change", chapter_id=chapter_id)
    record["instruction_id"] = instruction_id
    record["base_commit"] = base_commit
    record = apply_transition(record, JobEvent.LOCK_ACQUIRED)
    save_job_record(jdir, record)

    def fail(reason: str) -> dict[str, Any]:
        nonlocal record
        if record["state"] == JobState.RUNNING.name:
            record = apply_transition(record, JobEvent.CLI_EXITED)
        record = apply_transition(record, JobEvent.CHECK_FAILED, reason=reason)
        save_job_record(jdir, record)
        return record

    log_dir = jdir / f"{actual_job_id}-logs"

    def call(
        expected_type: str,
        task_text: str,
        log_name: str,
        check_name: str,
        *,
        root: Path | None = None,
        ctx_paths: Sequence[str] | None = None,
    ) -> ClaudeCallOutcome:
        nonlocal record
        r = root if root is not None else repo
        cp = ctx_paths if ctx_paths is not None else setting_context(repo)
        built = build_claude_prompt(r, cp, task_text)
        record["context"] = [{"path": e.path, "sha256": e.sha256} for e in built.context]
        actual_log_name = log_name if log_name.startswith(actual_job_id) else f"{actual_job_id}-{log_name}"
        outcome = run_claude_call(
            settings,
            job_id=actual_job_id,
            prompt_text=built.text,
            expected_type=expected_type,
            model=settings.claude_model,
            log_dir=log_dir,
            log_name=actual_log_name,
            claude_runner=claude_runner,
        )
        record["cli"] = {
            "name": "claude",
            "version": claude_version(),
            "model": settings.claude_model,
            "actual_model": outcome.actual_model or ((record.get("cli") or {}).get("actual_model")),
        }
        record["container"] = None
        if outcome.last_result is not None:
            res = outcome.last_result
            record["run"] = {
                "exit_code": res.exit_code,
                "elapsed_seconds": res.elapsed_seconds,
                "timed_out": res.timed_out,
                "signal": res.signal_sent,
                "timeout_seconds": int(settings.timeouts["claude"]),
            }
        record["attempt"] = max(1, len(outcome.attempts))
        chk = _claude_check(check_name, outcome)
        record["checks"].append(chk)
        save_job_record(jdir, record)
        return outcome

    # 1. 方針 (setting_change_plan)
    allowed_targets_list: list[str] = []
    chars_dir = repo / "characters"
    if chars_dir.is_dir():
        for p in sorted(chars_dir.glob("*.yaml")):
            if p.is_file():
                allowed_targets_list.append(str(p.relative_to(repo)))
    if (repo / REGISTRY_TARGET).is_file():
        allowed_targets_list.append(REGISTRY_TARGET)

    allowed_targets_str = "\n".join(f"* {t}" for t in allowed_targets_list)
    plan_task = load_prompt_template(
        "setting_change_plan",
        instruction_id=instruction_id,
        instruction=instruction,
        allowed_targets=allowed_targets_str,
    )

    plan_outcome = call("setting_change_plan", plan_task, "plan", "claude_setting_change_plan")
    if plan_outcome.data is None:
        return fail("claude_setting_change_plan failed")

    plan_data = dict(plan_outcome.data)
    plan_data["instruction_id"] = instruction_id

    # 検査 setting_plan
    plan_problems: list[str] = []
    seen_targets: set[str] = set()
    for item in plan_data.get("targets", []):
        tgt = item.get("target")
        if tgt in seen_targets:
            plan_problems.append(f"duplicate target: {tgt}")
        seen_targets.add(tgt)
        if not is_setting_target(repo, tgt):
            plan_problems.append(f"target {tgt!r} is not an allowed setting target in main")

    new_f_count = plan_data.get("new_foreshadowing", 0)
    if new_f_count > 0 and REGISTRY_TARGET not in seen_targets:
        plan_problems.append(f"new_foreshadowing is {new_f_count} but {REGISTRY_TARGET} is not in targets")

    questions = plan_data.get("questions", [])
    targets = plan_data.get("targets", [])
    if not questions and not targets:
        plan_problems.append("questions is empty but targets is empty")

    record["checks"].append({
        "name": "setting_plan",
        "status": "FAIL" if plan_problems else "PASS",
        "details": plan_problems,
    })
    save_job_record(jdir, record)
    if plan_problems:
        return fail("setting_plan check failed")

    # questions があれば WAITING_HUMAN
    if questions:
        if record["state"] == JobState.RUNNING.name:
            record = apply_transition(record, JobEvent.CLI_EXITED)
        record = apply_transition(record, JobEvent.NEEDS_HUMAN_INPUT, reason=f"questions: {len(questions)}")
        save_job_record(jdir, record)
        # Note: schemas/job_record.schema.json has additionalProperties: false, so setting_questions
        # is kept on the in-memory record return object to avoid schema validation failure on save.
        record["setting_questions"] = {**plan_data, "targets": []}
        return record

    # 2. Patch (targets ごとに1回)
    # Collect all existing characters for next_knowledge_number
    all_char_docs: list[dict[str, Any]] = []
    if chars_dir.is_dir():
        for p in sorted(chars_dir.glob("*.yaml")):
            if p.is_file():
                all_char_docs.append(load_yaml(p))
    base_next_k = next_knowledge_number(all_char_docs)
    accumulated_k_adds = 0

    valid_patches: list[dict[str, Any]] = []
    diff_entries: list[str] = []
    post_docs: dict[str, Any] = {}

    for n, t_item in enumerate(targets, 1):
        target = t_item["target"]
        change = t_item["change"]
        target_path = repo / target
        doc_before = load_yaml(target_path)
        actual_base_hash = file_sha256(target_path)
        hints = pointer_hints(target, doc_before)

        is_char = target.startswith("characters/")
        new_ids: list[str] = []

        if is_char:
            next_k_id = f"K{base_next_k + accumulated_k_adds:03d}"
            target_rules = load_prompt_template("setting_patch_character", next_knowledge_id=next_k_id)
            char_id = doc_before.get("id", target.rsplit("/", 1)[-1].removesuffix(".yaml"))
            noop_example = f'`{{"op": "test", "path": "/id", "value": "{char_id}"}}`'
        else:
            if new_f_count == 0:
                new_ids_rule = "この変更では、新しい伏線は追加しません。"
            else:
                max_f_num = 0
                if isinstance(doc_before, list):
                    for f in doc_before:
                        if isinstance(f, dict) and "id" in f:
                            m = _F_NUM_RE.match(f["id"])
                            if m:
                                max_f_num = max(max_f_num, int(m.group(1)))
                new_ids = [f"F{max_f_num + i:03d}" for i in range(1, new_f_count + 1)]
                ids_str = ", ".join(new_ids)
                new_ids_rule = (
                    f"新しい伏線を {new_f_count} 件、`/-` への `add` で追加します。"
                    f"ID は順に {ids_str} を使ってください（この順に、ほかの ID は使えません）。"
                )
            target_rules = load_prompt_template("setting_patch_registry", new_ids_rule=new_ids_rule)
            if isinstance(doc_before, list) and doc_before and "id" in doc_before[0]:
                noop_example = f'`{{"op": "test", "path": "/0/id", "value": "{doc_before[0]["id"]}"}}`'
            else:
                noop_example = '`{"op": "test", "path": "", "value": []}`'

        patch_task = load_prompt_template(
            "setting_patch",
            target=target,
            instruction_id=instruction_id,
            instruction=instruction,
            change=change,
            base_hash=actual_base_hash,
            pointer_hints=hints,
            target_rules=target_rules,
            noop_example=noop_example,
        )

        patch_outcome = call("state_patch", patch_task, f"patch-{n}", f"claude_setting_patch_{n}")
        if patch_outcome.data is None:
            return fail(f"claude_setting_patch_{n} failed")

        patch = dict(patch_outcome.data)

        # base_hash の写し間違いは直して WARNING base_hash_corrected
        if patch.get("base_hash") != actual_base_hash:
            record["checks"].append({
                "name": "base_hash_corrected",
                "status": "WARNING",
                "details": [f"target {target}: base_hash corrected to {actual_base_hash}"],
            })
            patch["base_hash"] = actual_base_hash
            save_job_record(jdir, record)

        # 検査 check_patch_target, check_setting_patch_policy, dry_run_patch, check_setting_result
        t_chk = check_patch_target(patch, target)
        record["checks"].append({"name": t_chk.name, "status": t_chk.status, "details": list(t_chk.details)})
        if t_chk.status == "FAIL":
            save_job_record(jdir, record)
            return fail(f"check_patch_target failed for {target}")

        pol_chk = check_setting_patch_policy(patch, doc_before, target=target, new_foreshadow_ids=new_ids)
        record["checks"].append({"name": pol_chk.name, "status": pol_chk.status, "details": list(pol_chk.details)})
        if pol_chk.status == "FAIL":
            save_job_record(jdir, record)
            return fail(f"check_setting_patch_policy failed for {target}")

        dry_chk = dry_run_patch(doc_before, patch)
        record["checks"].append({"name": dry_chk.name, "status": dry_chk.status, "details": list(dry_chk.details)})
        if dry_chk.status == "FAIL":
            save_job_record(jdir, record)
            return fail(f"dry_run_patch failed for {target}")

        doc_after = apply_patch(doc_before, patch)
        res_chks = check_setting_result(repo, target, doc_before, doc_after)
        failed_res = False
        for c in res_chks:
            record["checks"].append({"name": c.name, "status": c.status, "details": list(c.details)})
            if c.status == "FAIL":
                failed_res = True
        save_job_record(jdir, record)
        if failed_res:
            return fail(f"check_setting_result failed for {target}")

        # no-op check
        if is_noop_patch(patch):
            record["checks"].append({
                "name": "patch_noop",
                "status": "PASS",
                "details": [f"no changes in {target}"],
            })
            save_job_record(jdir, record)
        else:
            valid_patches.append(patch)
            post_docs[target] = doc_after
            # diff
            before_lines = dumps_yaml(doc_before).splitlines(keepends=True)
            after_lines = dumps_yaml(doc_after).splitlines(keepends=True)
            diff = "".join(
                difflib.unified_diff(before_lines, after_lines, fromfile=f"a/{target}", tofile=f"b/{target}")
            )
            diff_entries.append(diff)
            if is_char:
                for op in patch.get("operations", []):
                    if op.get("op") == "add" and op.get("path") == "/knowledge/-":
                        accumulated_k_adds += 1

    if not valid_patches:
        return fail("no setting change")

    # 3. 影響範囲分析
    unified_diff_text = "\n".join(diff_entries)
    affected, notes, impact_checks = analyze_impact(
        settings,
        repo=repo,
        instruction=instruction,
        diff_text=unified_diff_text,
        call=call,
        post_settings=post_docs,
    )
    for c in impact_checks:
        record["checks"].append({"name": c.name, "status": c.status, "details": list(c.details)})
    save_job_record(jdir, record)

    if any(c.status == "FAIL" for c in impact_checks):
        return fail("impact analysis check failed")

    # 4. 提案の保存
    # Check if main moved during Job
    current_head = head_commit(repo, "main")
    if current_head != base_commit:
        return fail("main moved during setting_change")

    proposal = {
        "type": "setting_proposal",
        "instruction_id": instruction_id,
        "job_id": actual_job_id,
        "instruction": instruction,
        "base_commit": base_commit,
        "plan": plan_data["targets"],
        "patches": [{"patch": p, "sha256": patch_sha256(p)} for p in valid_patches],
        "impact": affected,
        "impact_notes": notes,
        "status": "pending",
        "status_reason": None,
        "created_at": now_iso(),
        "decided_at": None,
        "approval_id": None,
        "commit": None,
    }

    # Supersede older pending proposals on overlapping targets
    new_targets = {p["target"] for p in valid_patches}
    for old_p in find_setting_proposals(settings, work.work_key):
        if old_p["status"] == "pending" and old_p["instruction_id"] != instruction_id:
            old_targets = {e["patch"]["target"] for e in old_p.get("patches", [])}
            if old_targets & new_targets:
                old_p["status"] = "superseded"
                old_p["status_reason"] = f"superseded by {instruction_id}"
                old_p["decided_at"] = now_iso()
                save_setting_proposal(settings, work.work_key, old_p)

    save_setting_proposal(settings, work.work_key, proposal)

    # Check worktree clean
    changes = get_changes(repo)
    if changes:
        _restore_paths(repo, [c.path for c in changes])
        record["checks"].append({
            "name": "worktree_clean",
            "status": "FAIL",
            "details": [f"uncommitted changes in {c.path}" for c in changes],
        })
        save_job_record(jdir, record)
        return fail("worktree_clean failed")
    record["checks"].append({"name": "worktree_clean", "status": "PASS", "details": []})

    # 5. Job COMPLETED
    if record["state"] == JobState.RUNNING.name:
        record = apply_transition(record, JobEvent.CLI_EXITED)
    record = apply_transition(record, JobEvent.CHECK_PASSED, needs_validation=False)
    save_job_record(jdir, record)
    return record


def all_job_branches(repo: Path) -> list[str]:
    """List all existing job branches under refs/heads/ai/."""
    out = run_git(repo, "for-each-ref", "--format=%(refname:short)", "refs/heads/ai/")
    return [line.strip() for line in out.decode("utf-8").splitlines() if line.strip()]


def approve_setting(settings: Settings, work: WorkInfo, instruction_id: str) -> dict[str, Any]:
    """Approve a pending setting proposal: apply patches and commit to main (phase3b-design §3.3)."""
    proposal = load_setting_proposal(settings, work.work_key, instruction_id)
    if proposal["status"] != "pending":
        raise ChapterError(f"setting proposal {instruction_id} is {proposal['status']}; only pending proposals can be approved")

    repo = work.path
    ensure_main_ready(repo)

    patches = [e["patch"] for e in proposal["patches"]]

    # 2. 無効の検査
    for p in patches:
        target = p["target"]
        actual_hash = file_sha256(repo / target)
        if actual_hash != p["base_hash"]:
            reason = f"target {target} base_hash mismatch ({actual_hash} != {p['base_hash']}); run instruct again"
            proposal["status"] = "invalid"
            proposal["status_reason"] = reason
            proposal["decided_at"] = now_iso()
            save_setting_proposal(settings, work.work_key, proposal)
            raise ProposalError(f"proposal {instruction_id} is invalid: {reason}")

    for e in proposal["patches"]:
        p = e["patch"]
        if patch_sha256(p) != e["sha256"]:
            reason = f"patch sha256 mismatch for {p['target']}; run instruct again"
            proposal["status"] = "invalid"
            proposal["status_reason"] = reason
            proposal["decided_at"] = now_iso()
            save_setting_proposal(settings, work.work_key, proposal)
            raise ProposalError(f"proposal {instruction_id} is invalid: {reason}")

    # 3. 作業ブランチの検査
    target_set = {p["target"] for p in patches}
    for branch in all_job_branches(repo):
        mb = run_git(repo, "merge-base", "main", branch).decode("utf-8").strip()
        changed = set(p for p in run_git(repo, "diff", "--name-only", mb, branch).decode("utf-8").splitlines() if p)
        intersect = sorted(changed & target_set)
        if intersect:
            ch = branch.split("/")[1]
            raise ChapterError(f"{branch} already changed {intersect[0]}; final-approve or discard {ch} first")

    # 4. 影響章の振り分け
    targets_str = ", ".join(sorted(target_set))
    detail = f"{instruction_id}: {targets_str} changed"
    affected_cids = [item["chapter_id"] for item in proposal.get("impact", [])]

    branch_chapters_list: list[str] = []
    main_chapters_list: list[str] = []
    for cid in affected_cids:
        if chapter_branches(repo, cid):
            branch_chapters_list.append(cid)
        else:
            main_chapters_list.append(cid)

    if branch_chapters_list:
        mark_review_required(settings, work, branch_chapters_list, code="setting_changed", detail=detail, branch_only=True)

    # 5. main への1つの commit
    approval_id = reserve_approval_id(settings, work)
    app_doc = build_instruction_approval(approval_id, instruction_id=instruction_id, patches=patches)
    app_rel = approval_rel(approval_id)

    allowed_paths = set(target_set) | {app_rel}
    for cid in main_chapters_list:
        allowed_paths.add(f"chapters/{cid}/chapter.yaml")

    at = now_iso()
    try:
        # Patch を当てた設定ファイル
        for p in patches:
            doc_before = load_yaml(repo / p["target"])
            doc_after = apply_patch(doc_before, p)
            write_yaml_atomic(repo / p["target"], doc_after)

        # 承認記録
        app_path = repo / app_rel
        app_path.parent.mkdir(parents=True, exist_ok=True)
        write_yaml_atomic(app_path, app_doc)

        # 作業ブランチの無い影響章の chapter.yaml
        for cid in main_chapters_list:
            ch_path = repo / f"chapters/{cid}/chapter.yaml"
            meta = load_yaml(ch_path)
            new_meta = review_marked(meta, "setting_changed", detail, at)
            validate_or_raise(new_meta, "chapter")
            sem = check_chapter(new_meta)
            if sem:
                raise SchemaError(f"Semantic validation failed for chapter {cid}: {sem}", errors=sem)
            write_yaml_atomic(ch_path, new_meta)

        allowed_check = check_allowed_paths(get_changes(repo), allowed_paths)
        if allowed_check.status != "PASS":
            raise ChapterError(f"Changes outside allowed paths: {list(allowed_check.details)}")

        commit_hash = commit_all(
            repo,
            f"setting change {instruction_id}",
            [("Approval-Id", approval_id)],
            name=settings.git_name,
            email=settings.git_email,
        )
        if commit_hash is None:
            raise ChapterError("No changes to commit for setting change")
    except BaseException:
        _restore_paths(repo, sorted(allowed_paths))
        if (repo / app_rel).is_file():
            (repo / app_rel).unlink()
        raise

    # 6. 提案を approved にする
    proposal["status"] = "approved"
    proposal["approval_id"] = approval_id
    proposal["commit"] = commit_hash
    proposal["decided_at"] = now_iso()
    save_setting_proposal(settings, work.work_key, proposal)
    return proposal


def reject_setting(settings: Settings, work: WorkInfo, instruction_id: str, reason: str) -> dict[str, Any]:
    """Reject a pending setting proposal."""
    if not reason or not reason.strip():
        raise ValueError("reason is required to reject a setting proposal")
    proposal = load_setting_proposal(settings, work.work_key, instruction_id)
    if proposal["status"] != "pending":
        raise ChapterError(f"setting proposal {instruction_id} is {proposal['status']}; only pending proposals can be rejected")

    proposal["status"] = "rejected"
    proposal["status_reason"] = reason.strip()
    proposal["decided_at"] = now_iso()
    save_setting_proposal(settings, work.work_key, proposal)
    return proposal
