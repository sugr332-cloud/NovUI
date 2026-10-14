"""Integration tests of settings tied to flags (phase2f-design, 2-F4).

The AGY is fake (the fixture script is returned as the draft); Claude is real. Nothing is adjusted to make a run
pass: when the real output is not what the flow needs, the test prints what it got and stops.

    NOVUI_INTEGRATION=1 .venv/bin/python -m pytest -s -rs -m integration tests/integration/test_flag_integration.py
"""

import copy
import os
from pathlib import Path
from typing import Any

import pytest

from novui.chapters import add_chapter, transition_on_main
from novui.config import Settings
from novui.draftjob import run_chapter_draft_job
from novui.models import save_catalog, select_model
from novui.planjob import approve_plan
from novui.states import ChapterEvent
from novui.stateupdate import run_state_update, show_proposal
from novui.validatejob import decide_validation
from novui.workinit import init_work
from novui.workrepo import commit_all
from novui.works import WorkInfo
from novui.yamlio import dumps_yaml, load_yaml

from test_script_integration import (  # noqa: F401  (env と fake_image は fixture)
    BACKGROUNDS, C002, CH, FAKE_MODEL, FIXTURES, FLAGS, env, fake_agy, fake_image, plan, show, validate,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("NOVUI_INTEGRATION") != "1",
        reason="Requires NOVUI_INTEGRATION=1 environment variable",
    ),
]

SETTING = (
    "# 世界観\n\n"
    "舞台は架空の港町ミナト。港には市場と門がある。\n"
    "カイと美咲は同じ村で育った幼なじみで、二人とも今は港町ミナトで暮らしている。\n"
    "美咲は来月この町を出ることを、まだ誰にも話していない。\n"
)
# 市場を一緒に回ると（market_together）、S2 から美咲を愛称「ミサ」で呼ぶ
C001 = {"id": "C001", "name": "カイ", "display_name": "カイ", "engine_id": "kai",
        "expressions": ["neutral", "happy", "surprised", "angry"],
        "speech": {"first_person": "俺"},
        "address": {"default": "お前", "C002": {"default": "美咲", "changes": [
            {"value": "ミサ", "from": {"chapter": CH, "scene": "S2"},
             "reason": "市場を一緒に回って打ち解け、愛称で呼ぶようになる", "when": "market_together"}]}},
        "knowledge": []}
OUTLINE = (
    "カイが港の市場で美咲に会う。カイは美咲と市場を回るか、一人で船を見に行くかを選ぶ（選択肢。"
    "市場を回るとフラグ market_together が立つ）。市場を回ると二人は打ち解け、カイは美咲を愛称の「ミサ」で呼ぶようになる。"
    "美咲は市場で、来月この町を出ることをカイに打ち明ける。"
    "夕方、二人は門の前で会い、明日また会う約束をする。門の前でのカイの台詞は、市場を一緒に回ったかどうかで変わる。"
)


def flag_plan() -> dict[str, Any]:
    doc = copy.deepcopy(plan())
    doc["scenes"][1]["summary"] = "二人で市場を歩いて打ち解け、カイは美咲をミサと呼ぶ。美咲は来月この町を出ることを打ち明ける"
    return doc


def flag_script(*, s4_last: str = "カイ「ああ、また明日」") -> str:
    return (
        "<!-- scene: S1 -->\n@bg bg_market_day\n@enter C001 left neutral\n@enter C002 right happy\n"
        "カイは港の市場で美咲を見つけた。\n"
        "カイ［surprised］「美咲、こんなところで何してるんだ」\n"
        "美咲「私は魚を買いに来たの。カイこそ、どうしたの」\n"
        "カイ（言えるわけがない）\n"
        "@choice\n- 一緒に市場を回る -> S2 +market_together\n- 船を見に行く -> S3\n\n"
        "<!-- scene: S2 -->\n@bg bg_market_day\n@enter C001 left happy\n@enter C002 right happy\n"
        "二人は市場を歩いた。\n美咲「この魚、おいしそう」\nカイ「ミサ、俺が持つよ」\n"
        "美咲［neutral］「ねえカイ。実は私、来月この町を出るの」\n"
        "カイ（そうだったのか）\n@goto S4\n\n"
        "<!-- scene: S3 -->\n@bg bg_harbor_day\n@enter C001 center neutral\n"
        "カイは一人で船を眺めた。\nカイ（いつか、あの船に乗りたい）\n\n"
        "<!-- scene: S4 -->\n@bg bg_gate_evening\n@enter C001 left neutral\n@enter C002 right neutral\n"
        "夕方、二人は港の門の前で落ち合った。\n"
        "@if market_together\nカイ［happy］「ミサと回れて楽しかった」\n"
        "@else\nカイ「美咲、船はすごかったぞ」\n@endif\n"
        f"美咲「また明日ね」\n{s4_last}\n"
    )


def make_work(env: tuple[Settings, Path], label: str) -> WorkInfo:
    settings, root_dir = env
    work = init_work(settings, root_dir / f"work-{label}", f"itest-{label.lower()}-{root_dir.name}", "結合試験")
    repo = work.path
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
    commit_all(repo, "itest setup", [("NovUI-Edit", "human-content")], name=settings.git_name, email=settings.git_email)
    add_chapter(settings, work, CH, "港町", OUTLINE)
    transition_on_main(
        settings, work, CH, ChapterEvent.PLAN_WRITTEN, subject="itest plan",
        extra_files={f"chapters/{CH}/plan.yaml": dumps_yaml(flag_plan()).encode("utf-8")},
    )
    approve_plan(settings, work, CH)
    return work


def address_findings(review: dict[str, Any]) -> list[dict[str, Any]]:
    return [f["character"] for f in review["integrity"]["checks"]["character"]["findings"]
            if f["character"] and f["character"]["rule"] == "address.C002"]


# ---------- G1：フラグごとの呼び方を @if で書き分けた台本は、呼び方の指摘が無い ----------

def test_g1_flag_rows_respected(env: tuple[Settings, Path], fake_image: None) -> None:
    settings, _ = env
    work = make_work(env, "G1")
    draft = run_chapter_draft_job(settings, work, CH, container_runner=fake_agy(flag_script()))
    assert draft["state"] == "COMPLETED", draft["checks"]
    rec, review = validate(settings, work, "G1", draft)
    mech = {c["name"]: c for c in review["mechanical"]}
    assert mech["flag_conditions"]["status"] == "PASS", mech["flag_conditions"]
    found = address_findings(review)
    if found:
        print("[G1] integrity (full):\n" + dumps_yaml(review["integrity"]))
        pytest.fail(f"address.C002 was reported although the script follows the flag rows: {found}")


# ---------- G2：@if の外で一方の道の呼び方を使うと、Claude が指摘する ----------

def test_g2_flag_value_outside_if(env: tuple[Settings, Path], fake_image: None) -> None:
    settings, _ = env
    work = make_work(env, "G2")
    text = flag_script(s4_last="カイ「ああ、ミサ、また明日」")
    draft = run_chapter_draft_job(settings, work, CH, container_runner=fake_agy(text))
    assert draft["state"] == "COMPLETED", draft["checks"]
    rec, review = validate(settings, work, "G2", draft)
    hit = [c for c in address_findings(review) if c["scene"] == "S4" and "ミサ" in c["actual"]]
    if not hit:
        print("[G2] integrity (full):\n" + dumps_yaml(review["integrity"]))
        pytest.fail("address.C002 ミサ outside @if in S4 was not reported")


# ---------- G3：分岐の場面で知ったことに、state_update が when を付ける ----------

def test_g3_state_update_adds_when(env: tuple[Settings, Path], fake_image: None) -> None:
    settings, _ = env
    work = make_work(env, "G3")
    draft = run_chapter_draft_job(settings, work, CH, container_runner=fake_agy(flag_script()))
    assert draft["state"] == "COMPLETED", draft["checks"]
    rec, review = validate(settings, work, "G3", draft)
    if rec["state"] == "WAITING_HUMAN":
        if review["integrity"]["result"] == "STOP" or review["writing"] is None:
            pytest.skip("validate stopped (STOP or no writing review): reported, nothing was adjusted")
        decide_validation(settings, work, CH, "CONTINUE")
    su = run_state_update(settings, work, CH)
    show("G3", su)
    proposal = show_proposal(settings, work, CH)
    if proposal is None:
        pytest.fail(f"no proposal: {su['history'][-1]}")
    print("[G3] --- summary ---\n" + dumps_yaml(proposal["summary"]))
    for e in proposal["patches"]:
        print(f"[G3] --- patch {e['patch']['target']} ---\n" + dumps_yaml(e["patch"]))

    branches = proposal["summary"].get("branches") or []
    assert any("S2" in b["scenes"] and b["when"] == "market_together" for b in branches), branches
    knowledge = [op["value"] for e in proposal["patches"] if e["patch"]["target"] == "characters/C001.yaml"
                 for op in e["patch"]["operations"] if op["op"] == "add" and op["path"] == "/knowledge/-"]
    leaving = [k for k in knowledge if "町を出る" in k["fact"] or "来月" in k["fact"]]
    assert leaving, f"C001 knowledge of 美咲 leaving was not proposed: {knowledge}"
    assert all(k.get("when") == "market_together" for k in leaving), leaving
    flag_checks = [c for c in su["checks"] if c["name"] == "flag_conditions"]
    assert flag_checks and all(c["status"] == "PASS" for c in flag_checks), flag_checks
