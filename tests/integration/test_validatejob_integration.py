"""Integration test: real AGY draft followed by real Claude validate (Phase 2-B2)."""

import os
from pathlib import Path
import secrets
import shutil
import subprocess
from typing import Iterator
import pytest

from novui.chapters import add_chapter, read_chapter_meta, transition_on_main
from novui.config import Settings, load_settings
from novui.draftjob import run_chapter_draft_job
from novui.gitinspect import run_git
from novui.jobrunner import jobs_dir
from novui.models import fetch_agy_models, save_catalog, select_model
from novui.planjob import approve_plan
from novui.protected import is_protected
from novui.schema import validate_or_raise
from novui.states import ChapterEvent
from novui.validatejob import run_validate_job
from novui.workinit import init_work
from novui.workrepo import commit_all, parse_trailers
from novui.works import WorkInfo
from novui.yamlio import dumps_yaml, loads_yaml

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("NOVUI_INTEGRATION") != "1",
        reason="Requires NOVUI_INTEGRATION=1 environment variable",
    ),
]

PLAN = {
    "type": "plan",
    "chapter_id": "ch-001",
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


@pytest.fixture
def validate_itest_env() -> Iterator[tuple[Settings, WorkInfo]]:
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


def test_integration_k3_draft_then_validate(validate_itest_env: tuple[Settings, WorkInfo]) -> None:
    """K3: 実際の AGY で draft → 実際の Claude で validate。Claude の判定は PASS に固定しない。"""
    settings, work = validate_itest_env
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
    (repo / "characters" / "C001.yaml").write_text(
        dumps_yaml({"id": "C001", "name": "カイ", "speech": {"first_person": "俺"}}), encoding="utf-8"
    )
    (repo / "foreshadowing" / "registry.yaml").write_text(dumps_yaml([{
        "id": "F001", "name": "王家の紋章", "status": "planned", "importance": "major",
        "introduced": [], "hints": [], "developments": [],
        "planned_resolution": None, "resolved": None, "notes": "",
    }]), encoding="utf-8")
    commit_all(repo, "itest setup", [("NovUI-Edit", "human-content")],
               name=settings.git_name, email=settings.git_email)

    add_chapter(settings, work, "ch-001", "港へ",
                "カイが港町ミナトに着く。門番がカイの持つ紋章に反応する（F001 の初出）。"
                "カイは理由が分からないまま門を通る。")
    transition_on_main(
        settings, work, "ch-001", ChapterEvent.PLAN_WRITTEN,
        subject="itest plan ch-001",
        extra_files={"chapters/ch-001/plan.yaml": dumps_yaml(PLAN).encode("utf-8")},
    )
    approve_plan(settings, work, "ch-001")

    main_before = run_git(repo, "rev-parse", "main").decode("utf-8").strip()
    main_chapter_before = (repo / "chapters" / "ch-001" / "chapter.yaml").read_bytes()

    # 1. 実際の AGY で draft
    draft = run_chapter_draft_job(settings, work, "ch-001")
    d_run = draft.get("run") or {}
    print(f"\n[K3] draft state={draft['state']} elapsed={d_run.get('elapsed_seconds')} "
          f"agy_version={(draft.get('cli') or {}).get('version')} model={(draft.get('cli') or {}).get('model')}")
    for c in draft.get("checks") or []:
        print(f"[K3] draft check {c['name']}: {c['status']} {c['details']}")
    assert draft["state"] == "COMPLETED"
    branch = draft["branch"]
    draft_tip = run_git(repo, "rev-parse", branch).decode("utf-8").strip()

    # 2. 実際の Claude で validate
    rec = run_validate_job(settings, work, "ch-001")

    run = rec.get("run") or {}
    cli_info = rec.get("cli") or {}
    print(f"[K3] validate state={rec['state']} model={cli_info.get('model')} "
          f"actual_model={cli_info.get('actual_model')} claude_version={cli_info.get('version')} "
          f"last_call_elapsed={run.get('elapsed_seconds')}")
    for h in rec["history"]:
        print(f"[K3] history {h['state']} event={h['event']} reason={h['reason']}")
    for c in rec.get("checks") or []:
        print(f"[K3] validate check {c['name']}: {c['status']} {c['details']}")
    review = None
    if run_git(repo, "ls-tree", "--name-only", branch, "chapters/ch-001/review.yaml").strip():
        review_text = run_git(repo, "show", f"{branch}:chapters/ch-001/review.yaml").decode("utf-8")
        print("[K3] review.yaml:\n" + review_text)
        review = loads_yaml(review_text)
    branch_meta = loads_yaml(run_git(repo, "show", f"{branch}:chapters/ch-001/chapter.yaml").decode("utf-8"))
    print(f"[K3] branch chapter.yaml state={branch_meta['state']} validation_skipped={branch_meta['validation_skipped']}")
    log = run_git(repo, "log", "--format=%h %s%n%(trailers:only,unfold)%x00", f"{draft_tip}..{branch}").decode("utf-8")
    for entry in (e.strip() for e in log.split("\0")):
        if entry:
            print("[K3] validate commit: " + entry.replace("\n", " | "))
    main_after = run_git(repo, "rev-parse", "main").decode("utf-8").strip()
    print(f"[K3] main before={main_before} after={main_after}")
    jh = settings.jobhome_root
    print("[K3] jobhomes:", sorted(p.name for p in jh.iterdir()) if jh.is_dir() else "(none)")
    cwd_root = settings.data_dir / "claude-cwd"
    print("[K3] claude-cwd:", sorted(p.name for p in cwd_root.iterdir()) if cwd_root.is_dir() else "(none)")

    # Claude の判定によらず成り立つこと
    assert rec["state"] in ("COMPLETED", "WAITING_HUMAN")
    assert cli_info.get("actual_model")
    assert review is not None
    validate_or_raise(review, "review")
    assert review["job_id"] == rec["job_id"]
    assert review["base_commit"] == draft["base_commit"]
    assert review["integrity"] is not None, "integrity_review was not obtained"
    assert review["writing"] is not None, "writing_review was not obtained"
    assert review["decision"] is None

    # 判定に応じた遷移（§11.4）
    mech_pass = all(c["status"] == "PASS" for c in review["mechanical"])
    if mech_pass and review["integrity"]["result"] == "PASS":
        assert rec["state"] == "COMPLETED"
        assert branch_meta["state"] == "AI_VALIDATED"
        assert branch_meta["validation_skipped"] is False
    else:
        assert rec["state"] == "WAITING_HUMAN"
        assert branch_meta["state"] == "DRAFTED"

    # 作業ブランチ：validate の commit は1つ、変更は review.yaml と chapter.yaml（chapter.yaml は AI_VALIDATED の時だけ）
    assert run_git(repo, "rev-list", "--count", f"{draft_tip}..{branch}").decode().strip() == "1"
    changed = run_git(repo, "diff", "--name-only", draft_tip, branch).decode("utf-8").split()
    assert "chapters/ch-001/review.yaml" in changed
    assert set(changed) <= {"chapters/ch-001/review.yaml", "chapters/ch-001/chapter.yaml"}
    assert not any(is_protected(p) for p in changed)
    msg = run_git(repo, "log", "-1", "--format=%B", branch).decode("utf-8")
    assert parse_trailers(msg) == {"NovUI-Job": [rec["job_id"]]}

    # main は変えない
    assert main_after == main_before
    assert (repo / "chapters" / "ch-001" / "chapter.yaml").read_bytes() == main_chapter_before
    assert read_chapter_meta(repo, "ch-001")["state"] == "PLAN_APPROVED"

    # 後片付け：Job用HOME、Claude の作業ディレクトリ、コンテナ
    assert not jh.is_dir() or list(jh.iterdir()) == []
    assert not cwd_root.is_dir() or list(cwd_root.iterdir()) == []
    ps = subprocess.run(
        ["podman", "ps", "-a", "--filter", f"name=novui-{work.work_key}", "--format", "{{.Names}}"],
        capture_output=True, text=True, check=False,
    )
    assert ps.stdout.strip() == ""
    assert jobs_dir(settings, work.work_key).is_dir()
