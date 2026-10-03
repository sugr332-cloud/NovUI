"""Integration test for plan Job using real Claude CLI."""

import os
from pathlib import Path
import secrets
import shutil
from typing import Iterator
import pytest

from novui.chapters import add_chapter, read_chapter_meta
from novui.config import Settings, load_settings
from novui.jobrunner import jobs_dir
from novui.planjob import approve_plan, run_plan_job
from novui.schema import validate_or_raise
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


@pytest.fixture
def plan_itest_env() -> Iterator[tuple[Settings, WorkInfo, Path]]:
    random_hex = secrets.token_hex(4)
    root_dir = Path.home() / ".local/share/novui-itest" / random_hex
    data_dir = root_dir / "data"
    work_dir = root_dir / "work"

    root_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    data_dir.mkdir(mode=0o700)

    base_settings = load_settings()
    settings = Settings(
        data_dir=data_dir,
        worktree_root=data_dir / "worktrees",
        jobhome_root=data_dir / "jobhomes",
        agy_image=base_settings.agy_image,
        agy_token_path=base_settings.agy_token_path,
        timeouts=base_settings.timeouts,
        git_name="NovUI Integration",
        git_email="itest@novui.local",
        claude_model=base_settings.claude_model,
    )

    work_key = f"itest-{random_hex}"
    work = init_work(settings, work_dir, work_key, "結合試験")

    try:
        yield settings, work, root_dir
    finally:
        shutil.rmtree(root_dir, ignore_errors=True)


def test_integration_k1_plan_job(plan_itest_env: tuple[Settings, WorkInfo, Path]) -> None:
    settings, work, root_dir = plan_itest_env
    repo = work.path

    # 1. 作品に世界観、キャラクター、伏線を書き commit する
    setting_text = "# 世界観\n\n舞台は架空の港町ミナト。王家の紋章を持つ者は港の門を自由に通れる。\n"
    (repo / "world" / "setting.md").write_text(setting_text, encoding="utf-8")

    c001_data = {"id": "C001", "name": "カイ", "speech": {"first_person": "俺"}}
    (repo / "characters" / "C001.yaml").write_text(dumps_yaml(c001_data), encoding="utf-8")

    f001_data = [
        {
            "id": "F001",
            "name": "王家の紋章",
            "status": "planned",
            "importance": "major",
            "introduced": [],
            "hints": [],
            "developments": [],
            "planned_resolution": None,
            "resolved": None,
            "notes": "",
        }
    ]
    (repo / "foreshadowing" / "registry.yaml").write_text(dumps_yaml(f001_data), encoding="utf-8")

    commit_all(
        repo,
        "setup initial setting, character C001, and foreshadowing F001",
        [("NovUI-Edit", "human-content")],
        name=settings.git_name,
        email=settings.git_email,
    )

    # 2. add_chapter
    outline_text = "カイが港町ミナトに着く。門番がカイの持つ紋章に反応する（F001 の初出）。カイは理由が分からないまま門を通る。"
    add_chapter(settings, work, "ch-001", "港へ", outline_text)

    # 3. run_plan_job (実際の Claude)
    record = run_plan_job(settings, work, "ch-001")

    # 診断出力
    print("\n=== DIAGNOSTIC OUTPUT ===")
    print(f"record['state']: {record.get('state')}")
    print("record['checks']:")
    for chk in record.get("checks", []):
        print(f"  name={chk.get('name')}, status={chk.get('status')}, details={chk.get('details')}")
    print("record['history']:")
    for h in record.get("history", []):
        print(f"  state={h.get('state')}, event={h.get('event')}, reason={h.get('reason')}")
    print(f"record['run']: {record.get('run')}")
    print(f"record['cli']: {record.get('cli')}")
    print(f"record['attempt']: {record.get('attempt')}")
    log_dir = jobs_dir(settings, work.work_key) / f"{record['job_id']}-logs"
    print(f"log_dir: {log_dir}")
    if log_dir.is_dir():
        for log_file in sorted(log_dir.iterdir()):
            print(f"File: {log_file.name}")
            if "stderr" in log_file.name:
                content = log_file.read_text(encoding="utf-8", errors="replace")
                print(f"--- stderr head 2000 chars ---\n{content[:2000]}")
            if "stdout" in log_file.name:
                content = log_file.read_text(encoding="utf-8", errors="replace")
                print(f"--- stdout head 3000 chars ---\n{content[:3000]}")
    else:
        print(f"Log dir not found: {log_dir}")
    print("=== END DIAGNOSTIC OUTPUT ===\n")

    # 4. 期待値の検証
    assert record["state"] == "COMPLETED"

    plan_path = repo / "chapters" / "ch-001" / "plan.yaml"
    assert plan_path.is_file()
    plan_data = load_yaml(plan_path)
    validate_or_raise(plan_data, "plan")

    meta = read_chapter_meta(repo, "ch-001")
    assert meta is not None
    assert meta["state"] == "PLANNED"

    scenes = plan_data.get("scenes", [])
    chars_in_scenes = [c for s in scenes for c in s.get("characters", [])]
    fs_in_scenes = [f for s in scenes for f in s.get("foreshadowing", [])]
    assert "C001" in chars_in_scenes
    assert "F001" in fs_in_scenes

    actual_model = record["cli"]["actual_model"]
    assert actual_model is not None

    # plan の scenes の数、actual_model、Job 記録の run.elapsed_seconds を print する
    print(f"\n[K1 Plan Job Results]")
    print(f"Plan scenes count: {len(scenes)}")
    print(f"Actual model: {actual_model}")
    print(f"Run elapsed seconds: {record['run']['elapsed_seconds']}")

    # 5. approve_plan
    approve_plan(settings, work, "ch-001")
    meta_approved = read_chapter_meta(repo, "ch-001")
    assert meta_approved is not None
    assert meta_approved["state"] == "PLAN_APPROVED"
