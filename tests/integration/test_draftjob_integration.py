"""Integration test for plan-based draft Job using real AGY in Podman (Phase 2-B1)."""

import os
from pathlib import Path
import secrets
import shutil
from typing import Iterator
import pytest

from novui.chapters import add_chapter, read_chapter_meta, transition_on_main
from novui.checks import count_chars
from novui.config import Settings, load_settings
from novui.draftjob import run_chapter_draft_job
from novui.gitinspect import run_git
from novui.jobrunner import jobs_dir
from novui.models import fetch_agy_models, save_catalog, select_model
from novui.planjob import approve_plan
from novui.states import ChapterEvent
from novui.workinit import init_work
from novui.workrepo import commit_all
from novui.works import WorkInfo
from novui.yamlio import dumps_yaml, load_yaml

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
def draft_itest_env() -> Iterator[tuple[Settings, WorkInfo]]:
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


def test_integration_k2_plan_based_draft(draft_itest_env: tuple[Settings, WorkInfo]) -> None:
    """K2: plan.yaml に基づく draft（実際の AGY）。場面の区切り・機械検査・作業ブランチ上の DRAFTED。"""
    settings, work = draft_itest_env
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
    # plan は Claude を使わずに用意する（K2 の対象は draft）
    transition_on_main(
        settings, work, "ch-001", ChapterEvent.PLAN_WRITTEN,
        subject="itest plan ch-001",
        extra_files={"chapters/ch-001/plan.yaml": dumps_yaml(PLAN).encode("utf-8")},
    )
    approve_plan(settings, work, "ch-001")

    main_before = run_git(repo, "rev-parse", "main").decode("utf-8").strip()
    record = run_chapter_draft_job(settings, work, "ch-001")

    # 診断の出力（fixture が終了時に作業領域を削除するため、assert の前にすべて出す）
    run = record.get("run") or {}
    elapsed = run.get("elapsed_seconds")
    elapsed_str = f"{elapsed:.1f}s" if elapsed is not None else "None"
    print(f"\n[K2] state={record['state']} elapsed={elapsed_str} model={(record.get('cli') or {}).get('model')}")
    for c in record.get("checks") or []:
        print(f"[K2] check {c['name']}: {c['status']} {c['details']}")
    saved_path = jobs_dir(settings, work.work_key) / f"{record['job_id']}.yaml"
    print(f"[K2] saved job record ({saved_path.name}):")
    print(saved_path.read_text(encoding="utf-8") if saved_path.is_file() else "  (not found)")
    main_after = run_git(repo, "rev-parse", "main").decode("utf-8").strip()
    print(f"[K2] main before={main_before} after={main_after} unchanged={main_before == main_after}")
    jh_root = settings.jobhome_root
    print("[K2] jobhomes:", sorted(p.name for p in jh_root.iterdir()) if jh_root.is_dir() else "(none)")
    if record.get("branch"):
        log = run_git(repo, "log", "--format=%h %s%n%(trailers:only,unfold)%x00",
                      f"main..{record['branch']}").decode("utf-8")
        print(f"[K2] commits main..{record['branch']}:")
        for entry in (e.strip() for e in log.split("\0")):
            if entry:
                print("  " + entry.replace("\n", " | "))
        diff = run_git(repo, "diff", "--name-only", f"main..{record['branch']}").decode("utf-8").split()
        print("[K2] diff main..branch:", sorted(diff))
    if record.get("worktree"):
        wt = Path(record["worktree"])
        draft_path = wt / "chapters" / "ch-001" / "draft.md"
        if draft_path.is_file():
            text = draft_path.read_text(encoding="utf-8")
            print(f"[K2] chars={count_chars(text)}")
            print("[K2] marker lines:", [ln for ln in text.splitlines() if ln.startswith("<!--")])
            print("[K2] draft.md:\n" + text)
        for name in ("chapter.yaml", "requests.yaml"):
            p = wt / "chapters" / "ch-001" / name
            if p.is_file():
                print(f"[K2] worktree {name}:\n" + p.read_text(encoding="utf-8"))

    if record["state"] == "WAITING_HUMAN":
        # 設定にない事柄で【要確認】を出して止まるのは正しい動き。設定を足して合わせない（S1 と同じ方針）
        assert main_before == main_after
        assert read_chapter_meta(repo, "ch-001")["state"] == "PLAN_APPROVED"
        requests_path = Path(record["worktree"]) / "chapters" / "ch-001" / "requests.yaml"
        requests = load_yaml(requests_path) if requests_path.is_file() else None
        print(f"[K2] requests: {requests}")
        if requests and all(r.get("kind") == "undefined_setting" for r in requests):
            pytest.skip("draft stopped at 【要確認】 (undefined_setting): correct behaviour, nothing was adjusted")

    assert record["state"] == "COMPLETED"
    checks = {c["name"]: c for c in record["checks"]}
    assert checks["scene_markers"]["status"] == "PASS"
    assert checks["ref_ids"]["status"] == "PASS"
    assert load_yaml(Path(record["worktree"]) / "chapters" / "ch-001" / "chapter.yaml")["state"] == "DRAFTED"
    assert read_chapter_meta(repo, "ch-001")["state"] == "PLAN_APPROVED"
    changed = run_git(repo, "diff", "--name-only", f"main..{record['branch']}").decode("utf-8").split()
    assert sorted(changed) == ["chapters/ch-001/chapter.yaml", "chapters/ch-001/draft.md"]
