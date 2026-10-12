"""Integration tests of the novel game script format (phase2e-design, agy-instruction-2e 2-E6).

E1-E5 use a fake AGY (the fixture script is returned as the draft) and the real Claude. E6 uses the real AGY and
runs only with NOVUI_SCRIPT_AGY=1 (and the alternate-account AGY token, like the other AGY integration tests).
Nothing is adjusted to make a run pass: when the real output is not what the flow needs, the test prints what it got
and stops.

    NOVUI_INTEGRATION=1 .venv/bin/python -m pytest -s -rs -m integration tests/integration/test_script_integration.py
"""

import copy
import os
from pathlib import Path
import secrets
import shutil
from typing import Any, Callable, Iterator

import pytest

from novui.chapters import add_chapter, read_chapter_meta, transition_on_main
from novui.config import Settings, load_settings
from novui.draftjob import run_chapter_draft_job
from novui.export import export_chapter, write_export
from novui.finalize import final_approve
from novui.gitinspect import run_git
from novui.models import fetch_agy_models, save_catalog, select_model
from novui.planjob import approve_plan
from novui.procrun import ProcResult
from novui.states import ChapterEvent
from novui.stateupdate import approve_state, run_state_update, show_proposal
from novui.validatejob import _read_branch_yaml, decide_validation, run_validate_job
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

FIXTURES = Path(__file__).parent.parent / "fixtures" / "valid"
CH = "ch-001"
FAKE_MODEL = "gemini-3.8-flash-high"

SETTING = (
    "# 世界観\n\n"
    "舞台は架空の港町ミナト。港には市場と門がある。\n"
    "カイと美咲は同じ村で育った幼なじみで、二人とも今は港町ミナトで暮らしている。\n"
)
C001 = {"id": "C001", "name": "カイ", "display_name": "カイ", "engine_id": "kai",
        "expressions": ["neutral", "happy", "surprised", "angry"],
        "speech": {"first_person": "俺"}, "address": {"default": "お前", "C002": "美咲"}}
C002 = {"id": "C002", "name": "美咲", "display_name": "美咲", "engine_id": "misaki",
        "expressions": ["neutral", "happy", "angry"],
        "speech": {"first_person": "私"}, "address": {"C001": "カイ"}}
BACKGROUNDS = ["bg_market_day", "bg_harbor_day", "bg_gate_evening"]
FLAGS = [{"name": "market_together", "description": "美咲と市場を回った", "notes": ""}]
OUTLINE = (
    "カイが港の市場で美咲に会う。カイは美咲と市場を回るか、一人で船を見に行くかを選ぶ（選択肢。"
    "市場を回るとフラグ market_together が立つ）。夕方、二人は門の前で会い、明日また会う約束をする。"
    "門の前でのカイの台詞は、市場を一緒に回ったかどうかで変わる。"
)


def plan() -> dict[str, Any]:
    base = {"actions": ["会話する"], "settings_used": ["港町ミナト"], "foreshadowing": []}
    return {
        "type": "plan",
        "chapter_id": CH,
        "pov": "三人称・カイに寄り添う",
        "style_notes": ["台詞を中心に、地の文は短く"],
        "target_chars": {"min": 50, "max": 2000},
        "context": {"past_summaries": [], "past_drafts": [], "settings": ["world/setting.md"]},
        "scenes": [
            {**base, "id": "S1", "title": "市場で", "background": "bg_market_day", "characters": ["C001", "C002"],
             "summary": "カイが港の市場で美咲に会い、市場を回るか船を見に行くかを選ぶ",
             "choice": [{"text": "一緒に市場を回る", "goto": "S2", "flag": "market_together"},
                        {"text": "船を見に行く", "goto": "S3"}]},
            {**base, "id": "S2", "title": "二人で市場を", "background": "bg_market_day", "characters": ["C001", "C002"],
             "summary": "二人で市場を歩く", "next": "S4"},
            {**base, "id": "S3", "title": "船を見る", "background": "bg_harbor_day", "characters": ["C001"],
             "summary": "カイが一人で船を見る"},
            {**base, "id": "S4", "title": "門で", "background": "bg_gate_evening", "characters": ["C001", "C002"],
             "summary": "夕方、二人が門の前で明日また会う約束をする"},
        ],
        "prohibitions": ["新しい人物を出さない"],
        "connection": {"from_previous": "冒頭", "to_next": "翌日、二人が再び会う"},
    }


def script(*, s1_line: str = "カイ［surprised］「美咲、こんなところで何してるんだ」",
           s3_extra: str = "", enter_c001: str = "@enter C001 left neutral") -> str:
    return (
        "<!-- scene: S1 -->\n@bg bg_market_day\n"
        f"{enter_c001}\n@enter C002 right happy\n"
        "カイは港の市場で美咲を見つけた。\n"
        f"{s1_line}\n"
        "美咲「私は魚を買いに来たの。カイこそ、どうしたの」\n"
        "カイ（言えるわけがない）\n"
        "@choice\n- 一緒に市場を回る -> S2 +market_together\n- 船を見に行く -> S3\n\n"
        "<!-- scene: S2 -->\n@bg bg_market_day\n@enter C001 left happy\n@enter C002 right happy\n"
        "二人は市場を歩いた。\n美咲「この魚、おいしそう」\nカイ「俺が持つよ」\n@goto S4\n\n"
        "<!-- scene: S3 -->\n@bg bg_harbor_day\n@enter C001 center neutral\n"
        f"カイは一人で船を眺めた。\n{s3_extra}"
        "カイ（いつか、あの船に乗りたい）\n\n"
        "<!-- scene: S4 -->\n@bg bg_gate_evening\n@enter C001 left neutral\n@enter C002 right neutral\n"
        "夕方、二人は港の門の前で落ち合った。\n"
        "@if market_together\nカイ［happy］「美咲と回れて楽しかった」\n"
        "@else\nカイ「美咲、船はすごかったぞ」\n@endif\n"
        "美咲「また明日ね」\nカイ「ああ、また明日」\n"
    )


def fake_agy(text: str) -> Callable[..., ProcResult]:
    def run(**kwargs: Any) -> ProcResult:
        return ProcResult(exit_code=0, elapsed_seconds=0.1, timed_out=False, signal_sent=None,
                          group_remaining=False, stdout=text.encode("utf-8"), stderr=b"")
    return run


@pytest.fixture
def env() -> Iterator[tuple[Settings, Path]]:
    random_hex = secrets.token_hex(4)
    root_dir = Path.home() / ".local/share/novui-itest" / random_hex
    data_dir = root_dir / "data"
    root_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    data_dir.mkdir(mode=0o700)
    base = load_settings()
    settings = Settings(
        data_dir=data_dir, worktree_root=data_dir / "worktrees", jobhome_root=data_dir / "jobhomes",
        agy_image=base.agy_image, agy_token_path=base.agy_token_path, timeouts=base.timeouts,
        git_name="NovUI Integration", git_email="itest@novui.local", claude_model=base.claude_model,
    )
    try:
        yield settings, root_dir
    finally:
        shutil.rmtree(root_dir, ignore_errors=True)


@pytest.fixture
def fake_image(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("novui.jobrunner.image_label", lambda img, key: "itest")
    monkeypatch.setattr(
        "novui.jobrunner.image_id",
        lambda img: "sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
    )


def make_work(env: tuple[Settings, Path], label: str, *, real_agy: bool = False) -> WorkInfo:
    settings, root_dir = env
    work = init_work(settings, root_dir / f"work-{label}", f"itest-{label.lower()}-{root_dir.name}", "結合試験")
    repo = work.path
    if real_agy:
        save_catalog(settings, fetch_agy_models(settings))
        select_model(settings, "draft", os.environ.get("NOVUI_AGY_MODEL", FAKE_MODEL))
    else:
        save_catalog(settings, load_yaml(FIXTURES / "model_catalog.yaml"))
        select_model(settings, "draft", FAKE_MODEL)
    project = load_yaml(repo / "project.yaml")
    project["text_format"] = "script"
    (repo / "project.yaml").write_text(dumps_yaml(project), encoding="utf-8")
    (repo / "world" / "setting.md").write_text(SETTING, encoding="utf-8")
    for doc in (C001, C002):
        (repo / "characters" / f"{doc['id']}.yaml").write_text(dumps_yaml(doc), encoding="utf-8")
    (repo / "assets").mkdir(exist_ok=True)
    (repo / "assets" / "backgrounds.yaml").write_text(dumps_yaml(BACKGROUNDS), encoding="utf-8")
    (repo / "flags").mkdir(exist_ok=True)
    (repo / "flags" / "registry.yaml").write_text(dumps_yaml(FLAGS), encoding="utf-8")
    (repo / "rules" / "script.yaml").write_text(dumps_yaml({"max_line_chars": 60}), encoding="utf-8")
    commit_all(repo, "itest setup", [("NovUI-Edit", "human-content")], name=settings.git_name, email=settings.git_email)
    add_chapter(settings, work, CH, "港町", OUTLINE)
    transition_on_main(
        settings, work, CH, ChapterEvent.PLAN_WRITTEN, subject="itest plan",
        extra_files={f"chapters/{CH}/plan.yaml": dumps_yaml(plan()).encode("utf-8")},
    )
    approve_plan(settings, work, CH)
    return work


def checks(record: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {c["name"]: c for c in record["checks"]}


def show(label: str, rec: dict[str, Any], review: dict[str, Any] | None = None) -> None:
    run = rec.get("run") or {}
    print(f"\n[{label}] {rec['job_type']} state={rec['state']} elapsed={run.get('elapsed_seconds')} "
          f"actual_model={(rec.get('cli') or {}).get('actual_model')} reason={rec['history'][-1]['reason']}")
    for c in rec["checks"]:
        if c["status"] != "PASS" or c["name"].startswith("script_"):
            print(f"[{label}] check {c['name']}: {c['status']} {c['details']}")
    if review is not None:
        for c in review["mechanical"]:
            print(f"[{label}] mechanical {c['name']}: {c['status']} {c['details']}")
        integ = review["integrity"]
        if integ is not None:
            print(f"[{label}] integrity result={integ['result']}")
            for key, chk in integ["checks"].items():
                for f in chk["findings"]:
                    print(f"[{label}] integrity {key} {f['severity']}: {f['message']} {f.get('character')}")


def validate(settings: Settings, work: WorkInfo, label: str, draft: dict[str, Any]) -> tuple[dict, dict]:
    rec = run_validate_job(settings, work, CH)
    review = _read_branch_yaml(work.path, draft["branch"], f"chapters/{CH}/review.yaml")
    show(label, rec, review)
    assert review["integrity"] is not None, "integrity_review was not obtained"
    return rec, review


# ---------- E1：正しい台本 ----------

def test_e1_valid_script(env: tuple[Settings, Path], fake_image: None) -> None:
    settings, _ = env
    work = make_work(env, "E1")
    draft = run_chapter_draft_job(settings, work, CH, container_runner=fake_agy(script()))
    show("E1", draft)
    assert draft["state"] == "COMPLETED"
    rec, review = validate(settings, work, "E1", draft)
    mech = {c["name"]: c["status"] for c in review["mechanical"]}
    assert all(s == "PASS" for n, s in mech.items() if n.startswith("script_")), mech


# ---------- E2：解析の誤りは draft で止まり、Claude を呼ばない ----------

def test_e2_syntax_error_fails_draft(env: tuple[Settings, Path], fake_image: None) -> None:
    settings, _ = env
    work = make_work(env, "E2")
    draft = run_chapter_draft_job(settings, work, CH, container_runner=fake_agy(script(enter_c001="@enter C001 top")))
    show("E2", draft)
    assert draft["state"] == "FAILED"
    assert checks(draft)["script_syntax"]["status"] == "FAIL"


# ---------- E3：演出の問題は validate の WARNING ----------

def test_e3_stage_problems_are_warnings(env: tuple[Settings, Path], fake_image: None) -> None:
    settings, _ = env
    work = make_work(env, "E3")
    long_line = "美咲「" + "あ" * 70 + "」\n"  # 登場していない人物の台詞（画面外の声）で、長すぎる台詞
    text = script(s1_line="カイ［sad］「美咲、こんなところで何してるんだ」", s3_extra=long_line)
    draft = run_chapter_draft_job(settings, work, CH, container_runner=fake_agy(text))
    show("E3", draft)
    assert draft["state"] == "COMPLETED"
    rec, review = validate(settings, work, "E3", draft)
    assert rec["state"] == "WAITING_HUMAN"
    mech = {c["name"]: c for c in review["mechanical"]}
    assert mech["script_expressions"]["status"] == "WARNING"
    assert mech["script_stage"]["status"] == "WARNING"
    assert mech["script_line_length"]["status"] == "WARNING"


# ---------- E4：台本の形式でも一人称の違反を Claude が検出する ----------

def test_e4_first_person_in_script(env: tuple[Settings, Path], fake_image: None) -> None:
    settings, _ = env
    work = make_work(env, "E4")
    text = script().replace("カイ「俺が持つよ」", "カイ「僕が持つよ」")
    draft = run_chapter_draft_job(settings, work, CH, container_runner=fake_agy(text))
    assert draft["state"] == "COMPLETED"
    rec, review = validate(settings, work, "E4", draft)
    found = [f["character"] for f in review["integrity"]["checks"]["character"]["findings"] if f["character"]]
    hit = [c for c in found if c["character_id"] == "C001" and c["rule"] == "speech.first_person"
           and c["expected"] == "俺" and c["actual"] == "僕"]
    if not hit:
        print("[E4] integrity (full):\n" + dumps_yaml(review["integrity"]))
        pytest.fail("speech.first_person 俺/僕 was not reported in the script")


# ---------- E5：FINAL まで進めて書き出す ----------

def test_e5_final_and_export(env: tuple[Settings, Path], fake_image: None) -> None:
    settings, root_dir = env
    work = make_work(env, "E5")
    repo = work.path
    draft = run_chapter_draft_job(settings, work, CH, container_runner=fake_agy(script()))
    assert draft["state"] == "COMPLETED"
    rec, review = validate(settings, work, "E5", draft)
    if rec["state"] == "WAITING_HUMAN":
        if review["integrity"]["result"] == "STOP" or review["writing"] is None:
            pytest.skip("validate stopped (STOP or no writing review): reported, nothing was adjusted")
        decide_validation(settings, work, CH, "CONTINUE")
    su = run_state_update(settings, work, CH)
    show("E5", su)
    proposal = show_proposal(settings, work, CH)
    if proposal is not None:
        print("[E5] --- summary ---\n" + dumps_yaml(proposal["summary"]))
        for e in proposal["patches"]:
            print(f"[E5] --- patch {e['patch']['target']} ---\n" + dumps_yaml(e["patch"]))
    if su["state"] != "WAITING_HUMAN":
        pytest.skip(f"state_update did not reach WAITING_HUMAN: {su['history'][-1]}")
    approve_state(settings, work, CH)
    commit = final_approve(settings, work, CH)
    print(f"[E5] merge commit {commit}")
    assert read_chapter_meta(repo, CH)["state"] == "FINAL"

    files = export_chapter(repo, CH)
    out = root_dir / "export"
    written = write_export(files, out)
    for p in written:
        print(f"[E5] ===== {p.relative_to(out)}\n" + p.read_text(encoding="utf-8"))
    assert sorted(files) == [
        "common/chapter_01/_meta.yaml", "common/chapter_01/scene_00.yaml", "common/chapter_01/scene_01.yaml",
        "common/chapter_01/scene_02.yaml", "common/chapter_01/scene_03.yaml",
    ]
    s4 = files["common/chapter_01/scene_03.yaml"]["dialogues"]
    assert [d.get("condition") for d in s4[1:3]] == ["market_together", "!market_together"]


# ---------- E6：実際の AGY が台本の形式を書けるか（確認だけ。失敗にはしない） ----------

@pytest.mark.skipif(os.environ.get("NOVUI_SCRIPT_AGY") != "1", reason="Requires NOVUI_SCRIPT_AGY=1 (real AGY)")
def test_e6_real_agy_writes_script(env: tuple[Settings, Path]) -> None:
    settings, _ = env
    work = make_work(env, "E6", real_agy=True)
    draft = run_chapter_draft_job(settings, work, CH)
    show("E6", draft)
    if draft.get("branch"):
        try:
            text = run_git(work.path, "show", f"{draft['branch']}:chapters/{CH}/draft.md").decode("utf-8")
            print("[E6] ===== draft.md\n" + text)
        except Exception as exc:  # noqa: BLE001
            print(f"[E6] draft.md not readable: {exc}")
    if draft["state"] != "COMPLETED":
        pytest.skip(f"real AGY draft ended {draft['state']}: printed for the report, nothing was adjusted")
