"""Integration tests: real AGY and real Claude through state_update and final approval (Phase 2-C4).

S1: draft (real AGY) -> validate (real Claude) -> state_update (real Claude) -> approve-state -> final-approve.
S2: a draft that needs an undefined setting -> WAITING_HUMAN -> resolve_request -> redraft (real AGY).

Nothing is adjusted to make a run pass: when the real output is not what the flow needs, the test prints what it got
and stops (skip) or fails. Run only on Human's instruction, with the alternate-account AGY token:

    NOVUI_INTEGRATION=1 NOVUI_AGY_TOKEN=<alternate token> NOVUI_AGY_MODEL=gemini-3.8-flash-high \\
        .venv/bin/python -m pytest -s -rs -m integration tests/integration/test_statefinal_integration.py
"""

import os
from pathlib import Path
import secrets
import shutil
import subprocess
from typing import Any, Iterator

import pytest

from novui.chapters import add_chapter, read_chapter_meta, transition_on_main
from novui.config import Settings, load_settings
from novui.draftjob import run_chapter_draft_job
from novui.finalize import final_approve
from novui.gitinspect import get_changes, run_git
from novui.jobrunner import jobs_dir
from novui.jobrecord import load_job_record
from novui.models import fetch_agy_models, save_catalog, select_model
from novui.planjob import approve_plan
from novui.requestflow import ARCHIVE_NAME, redraft, resolve_request, unresolved_requests
from novui.states import ChapterEvent
from novui.stateupdate import approve_state, run_state_update, show_proposal
from novui.validatejob import _read_branch_yaml, decide_validation, run_validate_job
from novui.workinit import init_work
from novui.workrepo import chapter_branches, commit_all, parse_trailers
from novui.works import WorkInfo
from novui.yamlio import dumps_yaml, load_yaml

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("NOVUI_INTEGRATION") != "1",
        reason="Requires NOVUI_INTEGRATION=1 environment variable",
    ),
]

CH = "ch-001"

PLAN_S1 = {
    "type": "plan",
    "chapter_id": CH,
    "pov": "三人称・C001 に寄り添う",
    "style_notes": ["短い文を基本にする"],
    "target_chars": {"min": 800, "max": 2000},
    "context": {"past_summaries": [], "past_drafts": [], "settings": ["world/setting.md"]},
    "scenes": [
        {
            "id": "S1",
            "summary": "カイが港町ミナトに着き、港の門の前に立つ",
            "actions": ["船を降りる", "港の門へ向かう"],
            "characters": ["C001"],
            "settings_used": ["港町ミナト", "港の門"],
            "foreshadowing": [],
        },
        {
            "id": "S2",
            "summary": "門番がカイの持つ紋章に反応し、カイは理由が分からないまま門を通る",
            "actions": ["門番が紋章を見る", "カイが門を通る"],
            "characters": ["C001"],
            "settings_used": ["王家の紋章", "港の門"],
            "foreshadowing": ["F001"],
        },
    ],
    "prohibitions": ["紋章の由来を明かさない", "門番に名前を付けない"],
    "connection": {"from_previous": "冒頭", "to_next": "カイが港町の中へ入っていく"},
}

OUTLINE_S1 = (
    "カイが港町ミナトに着く。門番がカイの持つ紋章に反応する（F001 の初出）。"
    "カイは理由が分からないまま門を通る。"
)

# S2: the plan makes the gatekeeper give a name, which the settings do not define
PLAN_S2 = {
    **PLAN_S1,
    "scenes": [
        PLAN_S1["scenes"][0],
        {
            "id": "S2",
            "summary": "門番がカイに自分の名を名乗り、カイは門を通る",
            "actions": ["門番が名乗る", "カイが門を通る"],
            "characters": ["C001"],
            "settings_used": ["港の門"],
            "foreshadowing": [],
        },
    ],
    "prohibitions": ["紋章の由来を明かさない"],
}
OUTLINE_S2 = "カイが港町ミナトに着く。門番がカイに自分の名前を名乗り、カイは門を通る。"


@pytest.fixture
def itest_env() -> Iterator[tuple[Settings, WorkInfo]]:
    random_hex = secrets.token_hex(4)
    root_dir = Path.home() / ".local/share/novui-itest" / random_hex
    data_dir = root_dir / "data"
    root_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    data_dir.mkdir(mode=0o700)

    base = load_settings()
    settings = Settings(
        data_dir=data_dir,
        worktree_root=data_dir / "worktrees",
        jobhome_root=data_dir / "jobhomes",
        agy_image=base.agy_image,
        agy_token_path=base.agy_token_path,
        timeouts=base.timeouts,
        git_name="NovUI Integration",
        git_email="itest@novui.local",
        claude_model=base.claude_model,
    )
    try:
        work = init_work(settings, root_dir / "work", f"itest-{random_hex}", "結合試験")
        yield settings, work
    finally:
        shutil.rmtree(root_dir, ignore_errors=True)


def _prepare(settings: Settings, work: WorkInfo, plan: dict[str, Any], outline: str) -> None:
    repo = work.path
    model = os.environ.get("NOVUI_AGY_MODEL", "gemini-3.8-flash-high")
    save_catalog(settings, fetch_agy_models(settings))
    select_model(settings, "draft", model)

    (repo / "world" / "setting.md").write_text(
        "# 世界観\n\n舞台は架空の港町ミナト。港には門があり、門番が出入りを見張っている。"
        "王家の紋章を持つ者は港の門を自由に通れる。"
        "カイは王家の紋章が刻まれた銀の指輪を、紐で首から下げている。\n",
        encoding="utf-8",
    )
    (repo / "characters" / "C001.yaml").write_text(dumps_yaml({
        "id": "C001", "name": "カイ", "speech": {"first_person": "俺"},
        "knowledge": [], "relationships": [], "address": {"default": "お前"},
    }), encoding="utf-8")
    (repo / "foreshadowing" / "registry.yaml").write_text(dumps_yaml([{
        "id": "F001", "name": "王家の紋章", "status": "planned", "importance": "major",
        "introduced": [], "hints": [], "developments": [],
        "planned_resolution": None, "resolved": None, "notes": "",
    }]), encoding="utf-8")
    commit_all(repo, "itest setup", [("NovUI-Edit", "human-content")],
               name=settings.git_name, email=settings.git_email)

    add_chapter(settings, work, CH, "港へ", outline)
    transition_on_main(
        settings, work, CH, ChapterEvent.PLAN_WRITTEN,
        subject="itest plan ch-001",
        extra_files={f"chapters/{CH}/plan.yaml": dumps_yaml(plan).encode("utf-8")},
    )
    approve_plan(settings, work, CH)


def _print_job(label: str, rec: dict[str, Any]) -> None:
    run = rec.get("run") or {}
    cli = rec.get("cli") or {}
    print(f"\n[{label}] job={rec['job_id']} type={rec['job_type']} state={rec['state']} "
          f"elapsed={run.get('elapsed_seconds')} exit_code={run.get('exit_code')}")
    print(f"[{label}] cli={cli.get('name')} version={cli.get('version')} model={cli.get('model')} "
          f"actual_model={cli.get('actual_model')} container={rec.get('container')}")
    print(f"[{label}] history={[(h['state'], h['event'], h['reason']) for h in rec['history']]}")
    for c in rec.get("checks") or []:
        print(f"[{label}] check {c['name']}: {c['status']} {c['details']}")


def _containers(work: WorkInfo) -> str:
    out = subprocess.run(
        ["podman", "ps", "-a", "--filter", f"name=novui-{work.work_key}", "--format", "{{.Names}}"],
        capture_output=True, text=True, check=False,
    )
    return out.stdout.strip()


def test_integration_s1_outline_to_final(itest_env: tuple[Settings, WorkInfo]) -> None:
    """S1 (acceptance 1): draft -> validate -> state_update -> approve-state -> final-approve with the real tools."""
    settings, work = itest_env
    repo = work.path
    _prepare(settings, work, PLAN_S1, OUTLINE_S1)
    main_before = run_git(repo, "rev-parse", "main").decode().strip()

    # 1. real AGY draft
    draft = run_chapter_draft_job(settings, work, CH)
    _print_job("S1 draft", draft)
    if draft["state"] == "WAITING_HUMAN":
        branch = draft["branch"]
        print(f"[S1] requests: {_read_branch_yaml(repo, branch, f'chapters/{CH}/requests.yaml')}")
        pytest.skip("draft stopped at 【要確認】: S2 covers that path; nothing was adjusted")
    assert draft["state"] == "COMPLETED"
    branch = draft["branch"]
    assert run_git(repo, "rev-parse", "main").decode().strip() == main_before

    # 2. real Claude validate
    val = run_validate_job(settings, work, CH)
    _print_job("S1 validate", val)
    assert val["state"] in ("COMPLETED", "WAITING_HUMAN"), f"validate did not finish: {val['history'][-1]}"
    if val["state"] == "WAITING_HUMAN":
        review = _read_branch_yaml(repo, branch, f"chapters/{CH}/review.yaml")
        integ = review["integrity"]
        print(f"[S1] mechanical={[(c['name'], c['status']) for c in review['mechanical']]}")
        if integ is None or review["writing"] is None:
            pytest.skip("validate did not get the Claude review: stopped, nothing was adjusted")
        print(f"[S1] integrity result={integ['result']}")
        for key, chk in integ["checks"].items():
            for f in chk["findings"]:
                print(f"[S1] integrity {key} {f['severity']}: {f['message']}")
        if integ["result"] == "STOP":
            pytest.skip("integrity_review is STOP: stopped here and reported (STOP cannot be continued or overridden)")
        decided = decide_validation(settings, work, CH, "CONTINUE")
        _print_job("S1 decide CONTINUE", decided)
    assert _read_branch_yaml(repo, branch, f"chapters/{CH}/chapter.yaml")["state"] == "AI_VALIDATED"

    # 3. real Claude state_update (the main thing under test)
    su = run_state_update(settings, work, CH)
    _print_job("S1 state_update", su)
    proposal = show_proposal(settings, work, CH)
    if proposal is not None:
        print("[S1] --- summary ---")
        print(dumps_yaml(proposal["summary"]))
        for e in proposal["patches"]:
            print(f"[S1] --- patch {e['patch']['target']} ---")
            print(dumps_yaml(e["patch"]))
    assert su["state"] == "WAITING_HUMAN", f"state_update did not reach WAITING_HUMAN: {su['history'][-1]}"
    assert all(c["status"] != "FAIL" for c in su["checks"])
    wt = Path(draft["worktree"])
    assert get_changes(wt) == []

    # 4. approve-state
    approved = approve_state(settings, work, CH)
    _print_job("S1 approve-state", approved)
    assert approved["state"] == "COMPLETED"
    meta = _read_branch_yaml(repo, branch, f"chapters/{CH}/chapter.yaml")
    assert meta["state"] == "HUMAN_APPROVED"
    print(f"[S1] approval_id={approved['approval_id']}")
    if approved["approval_id"]:
        print(dumps_yaml(_read_branch_yaml(repo, branch, f".novui/approvals/{approved['approval_id']}.yaml")))
    assert (wt / f"chapters/{CH}/summary.yaml").is_file()

    # 5. final-approve
    before_final = run_git(repo, "rev-parse", "main").decode().strip()
    commit = final_approve(settings, work, CH)
    print(f"[S1] merge commit {commit}")
    print(run_git(repo, "log", "--graph", "--oneline", "-8").decode())
    print(run_git(repo, "log", "-1", "--format=%B", commit).decode())
    print(run_git(
        repo, "diff", before_final, commit, "--", "characters", "foreshadowing", ".novui", f"chapters/{CH}/summary.yaml",
    ).decode())

    assert read_chapter_meta(repo, CH)["state"] == "FINAL"
    assert (repo / "chapters" / CH / "summary.yaml").is_file()
    trailers = parse_trailers(run_git(repo, "log", "-1", "--format=%B", commit).decode())
    assert draft["job_id"] in trailers["NovUI-Job"] and su["job_id"] in trailers["NovUI-Job"]
    if approved["approval_id"]:
        assert trailers["Approval-Id"] == [approved["approval_id"]]
    assert chapter_branches(repo, CH) == [] and not wt.exists()
    assert run_git(repo, "status", "--porcelain").decode() == ""
    assert _containers(work) == ""


def test_integration_s2_undefined_setting_resolve_and_redraft(itest_env: tuple[Settings, WorkInfo]) -> None:
    """S2 (acceptance 2): an undefined setting stops the draft; resolve_request and redraft continue it."""
    settings, work = itest_env
    repo = work.path
    _prepare(settings, work, PLAN_S2, OUTLINE_S2)

    draft = run_chapter_draft_job(settings, work, CH)
    _print_job("S2 draft", draft)
    branch = draft["branch"]
    if draft["state"] != "WAITING_HUMAN":
        print(f"[S2] draft text:\n{(Path(draft['worktree']) / 'chapters' / CH / 'draft.md').read_text(encoding='utf-8')}")
        pytest.skip("AGY did not write 【要確認】: stopped here and reported, nothing was adjusted")

    requests = _read_branch_yaml(repo, branch, f"chapters/{CH}/requests.yaml")
    print(f"[S2] requests: {requests}")
    assert unresolved_requests(requests)
    assert _read_branch_yaml(repo, branch, f"chapters/{CH}/chapter.yaml")["state"] == "PLAN_APPROVED"

    for index in list(unresolved_requests(requests)):
        rec = resolve_request(settings, work, CH, index, "門番の名前は出さず、ただ「門番」とだけ書く")
        _print_job(f"S2 resolve {index}", rec)
        assert rec["state"] == "COMPLETED"
    resolved = _read_branch_yaml(repo, branch, f"chapters/{CH}/requests.yaml")
    assert unresolved_requests(resolved) == []

    new = redraft(settings, work, CH)
    _print_job("S2 redraft", new)
    print(f"[S2] decisions in the new prompt are not printed; branches now: {chapter_branches(repo, CH)}")
    assert new["job_type"] == "draft" and new["job_id"] != draft["job_id"]
    assert new["state"] in ("COMPLETED", "WAITING_HUMAN")
    assert chapter_branches(repo, CH) == [new["branch"]]
    old = load_job_record(jobs_dir(settings, work.work_key) / f"{draft['job_id']}.yaml")
    assert old["state"] == "CANCELLED" and old["history"][-1]["reason"] == f"redrafted by {new['job_id']}"
    archive = jobs_dir(settings, work.work_key) / f"{draft['job_id']}-logs" / ARCHIVE_NAME
    assert [r["type"] for r in load_yaml(archive)][-len(requests):] == ["resolution"] * len(requests)
    assert not Path(draft["worktree"]).exists()
    assert _containers(work) == ""
