"""Integration test: acceptance 3 and 4 of Phase 2 (phase2d-design §6, agy-instruction-2d 2-D4).

The draft text is a fixture with planted violations, returned by a fake AGY (container) runner, so the
result does not depend on AGY. The draft and validate Jobs themselves are real (job branch, Context record,
draft_consistency), and validate calls the real Claude.

If Claude does not report an expected finding, or reports one it should not, the test prints the review and
the rule table and fails. Do not change the test, the prompts or the fixtures to make it pass (2-C4 policy).
"""

import copy
import os
from pathlib import Path
import secrets
import shutil
from typing import Any, Callable, Iterator
import pytest

from novui.chapters import add_chapter, read_chapters_order, transition_on_main
from novui.charrules import render_rule_tables, scene_rule_tables
from novui.config import Settings, load_settings
from novui.draftjob import run_chapter_draft_job
from novui.gitinspect import run_git
from novui.mechanical import load_mechanical_inputs
from novui.models import save_catalog, select_model
from novui.planjob import approve_plan
from novui.positions import scene_number
from novui.procrun import ProcResult
from novui.schema import validate_or_raise
from novui.states import ChapterEvent
from novui.validatejob import run_validate_job
from novui.workinit import init_work
from novui.workrepo import commit_all
from novui.works import WorkInfo
from novui.yamlio import dumps_yaml, load_yaml, loads_yaml

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("NOVUI_INTEGRATION") != "1",
        reason="Requires NOVUI_INTEGRATION=1 environment variable",
    ),
]

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures" / "valid"
MODEL = "gemini-3.8-flash-high"  # 偽の AGY なので、カタログの fixture にあるモデルなら何でもよい

SETTING = (
    "# 世界観\n\n"
    "舞台は架空の港町ミナト。港には市場と門がある。\n"
    "カイと美咲は同じ村で育った幼なじみで、二人とも今は港町ミナトで暮らしている。\n"
    "ロウはカイに剣を教えた先生で、今は旅に出ていて港町にはいない。\n"
)

C002 = {"id": "C002", "name": "美咲", "speech": {"first_person": "私"}, "address": {"C001": "カイ"}}
C003 = {"id": "C003", "name": "ロウ", "speech": {"first_person": "わし"}}


def c001(changes: list[dict[str, Any]] | None = None, *, teacher: bool = False) -> dict[str, Any]:
    c002: Any = {"default": "美咲", "changes": changes} if changes else "美咲"
    address: dict[str, Any] = {"default": "お前", "C002": c002}
    if teacher:
        address["C003"] = "先生"
    return {"id": "C001", "name": "カイ", "speech": {"first_person": "俺"}, "address": address}


CHANGE_AT_S2 = [{"value": "君", "from": {"chapter": "ch-001", "scene": "S2"}, "reason": "関係性の変化"}]


def plan(chapter_id: str) -> dict[str, Any]:
    return {
        "type": "plan",
        "chapter_id": chapter_id,
        "pov": "三人称・カイに寄り添う",
        "style_notes": ["短い文を基本にする"],
        "target_chars": {"min": 50, "max": 2000},
        "context": {"past_summaries": [], "past_drafts": [], "settings": ["world/setting.md"]},
        "scenes": [
            {
                "id": "S1",
                "summary": "カイが港の市場で美咲に会い、言葉を交わす",
                "actions": ["カイが美咲に声をかける", "美咲が魚を買いに来たと答える"],
                "characters": ["C001", "C002"],
                "settings_used": ["港町ミナト", "市場"],
                "foreshadowing": [],
            },
            {
                "id": "S2",
                "summary": "夕方、二人が港の門の前で明日また会う約束をする",
                "actions": ["カイが明日も会えるかと聞く", "美咲が来ると答える"],
                "characters": ["C001", "C002"],
                "settings_used": ["港の門"],
                "foreshadowing": [],
            },
        ],
        "prohibitions": ["新しい人物を出さない"],
        "connection": {"from_previous": "冒頭", "to_next": "翌日、二人が再び会う"},
    }


def draft_text(s1_call: str, s2_call: str, *, first_person: str = "俺", teacher: bool = False) -> str:
    """The fixture draft. Every line of C001 names the speaker in the narration."""
    teacher_line = "カイは海の向こうを見て言った。「先生がいたら、きっと笑うだろうな」\n" if teacher else ""
    return (
        "<!-- scene: S1 -->\n"
        "カイは港の市場で美咲を見つけた。\n"
        f"「{s1_call}、こんなところで何してるんだ」とカイは言った。\n"
        "美咲は籠を持ち上げて笑った。「私は魚を買いに来たの。カイこそ、どうしたの」\n"
        f"「{first_person}は船を見に来ただけだ」とカイは答えた。\n"
        "\n"
        "<!-- scene: S2 -->\n"
        "夕方、二人は港の門の前に立っていた。\n"
        f"「{s2_call}、明日もここで会えるか」とカイは言った。\n"
        "「ええ、私も来るわ」と美咲は答えた。\n"
        f"{teacher_line}"
        "「じゃあ、また明日だ」とカイは言った。\n"
    )


REGISTRY_OK = [{
    "id": "F001", "name": "王家の紋章", "status": "planned", "importance": "major",
    "introduced": [], "hints": [], "developments": [],
    "planned_resolution": None, "resolved": None, "notes": "",
}]


def fake_agy(text: str) -> Callable[..., ProcResult]:
    def run(**kwargs: Any) -> ProcResult:
        return ProcResult(
            exit_code=0, elapsed_seconds=0.1, timed_out=False, signal_sent=None,
            group_remaining=False, stdout=text.encode("utf-8"), stderr=b"",
        )
    return run


@pytest.fixture
def itest_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[Settings, Path]]:
    # 偽の AGY を使うので、podman のイメージを調べない
    monkeypatch.setattr("novui.jobrunner.image_label", lambda img, key: "itest")
    monkeypatch.setattr(
        "novui.jobrunner.image_id",
        lambda img: "sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
    )
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
        yield settings, root_dir
    finally:
        shutil.rmtree(root_dir, ignore_errors=True)


def run_case(
    env: tuple[Settings, Path],
    label: str,
    *,
    c1: dict[str, Any],
    text: str,
    registry: list[dict[str, Any]] = REGISTRY_OK,
    chapters: tuple[str, ...] = ("ch-001",),
) -> tuple[dict[str, Any], dict[str, Any], str]:
    """Set up a work, draft the last chapter with the fixture text and validate it with the real Claude.

    Returns (validate record, review, rule table text)."""
    settings, root_dir = env
    work: WorkInfo = init_work(settings, root_dir / f"work-{label}", f"itest-{label.lower()}-{root_dir.name}", "結合試験")
    repo = work.path
    save_catalog(settings, load_yaml(FIXTURES_DIR / "model_catalog.yaml"))
    select_model(settings, "draft", MODEL)

    (repo / "world" / "setting.md").write_text(SETTING, encoding="utf-8")
    for doc in (c1, C002, C003):
        (repo / "characters" / f"{doc['id']}.yaml").write_text(dumps_yaml(doc), encoding="utf-8")
    (repo / "foreshadowing" / "registry.yaml").write_text(dumps_yaml(registry), encoding="utf-8")
    commit_all(repo, "itest setup", [("NovUI-Edit", "human-content")], name=settings.git_name, email=settings.git_email)

    for ch in chapters:
        add_chapter(settings, work, ch, f"{ch} の章", "カイが港町で美咲に会い、明日また会う約束をする。")
    target = chapters[-1]
    transition_on_main(
        settings, work, target, ChapterEvent.PLAN_WRITTEN,
        subject=f"itest plan {target}",
        extra_files={f"chapters/{target}/plan.yaml": dumps_yaml(plan(target)).encode("utf-8")},
    )
    approve_plan(settings, work, target)

    draft = run_chapter_draft_job(settings, work, target, container_runner=fake_agy(text))
    assert draft["state"] == "COMPLETED", f"draft {draft['state']}: {draft['checks']}"

    rec = run_validate_job(settings, work, target)

    rules = render_rule_tables(scene_rule_tables(
        read_chapters_order(repo), target, plan(target), load_mechanical_inputs(repo).characters
    ))
    branch = draft["branch"]
    review = loads_yaml(run_git(repo, "show", f"{branch}:chapters/{target}/review.yaml").decode("utf-8"))
    validate_or_raise(review, "review")

    run = rec.get("run") or {}
    print(f"\n[{label}] validate state={rec['state']} elapsed={run.get('elapsed_seconds')} "
          f"actual_model={(rec.get('cli') or {}).get('actual_model')}")
    print(f"[{label}] reason={rec['history'][-1]['reason']}")
    for c in review["mechanical"]:
        print(f"[{label}] mechanical {c['name']}: {c['status']} {c['details']}")
    for c in rec["checks"]:
        if c["name"] == "prompt_tables":
            print(f"[{label}] prompt_tables {c['details']}")
    integ = review["integrity"]
    assert integ is not None, "integrity_review was not obtained"
    print(f"[{label}] integrity result={integ['result']}")
    print(f"[{label}] checks.character:\n" + dumps_yaml(integ["checks"]["character"]))
    print(f"[{label}] checks.foreshadowing:\n" + dumps_yaml(integ["checks"]["foreshadowing"]))
    return rec, review, rules


def character_findings(review: dict[str, Any]) -> list[dict[str, Any]]:
    return [f["character"] for f in review["integrity"]["checks"]["character"]["findings"] if f["character"]]


def has(found: list[dict[str, Any]], *, rule: str, expected: str, actual: str, scene: str, cid: str = "C001") -> bool:
    return any(
        c["character_id"] == cid and c["rule"] == rule and c["expected"] == expected and c["actual"] == actual
        and scene_number(c["scene"]) == scene_number(scene)
        for c in found
    )


def fail_with(label: str, review: dict[str, Any], rules: str, message: str) -> None:
    print(f"[{label}] integrity (full):\n" + dumps_yaml(review["integrity"]))
    print(f"[{label}] rule table given to Claude:\n" + rules)
    pytest.fail(f"[{label}] {message}")


# ---------- 受入3：キャラクター一貫性 ----------

def test_d1_first_person(itest_env: tuple[Settings, Path]) -> None:
    label = "D1"
    rec, review, rules = run_case(itest_env, label, c1=c001(), text=draft_text("美咲", "美咲", first_person="僕"))
    assert rec["state"] == "WAITING_HUMAN"
    found = character_findings(review)
    if not has(found, rule="speech.first_person", expected="俺", actual="僕", scene="S1"):
        fail_with(label, review, rules, "speech.first_person 俺/僕 at S1 was not reported")


def test_d2_address(itest_env: tuple[Settings, Path]) -> None:
    label = "D2"
    rec, review, rules = run_case(itest_env, label, c1=c001(), text=draft_text("美咲ちゃん", "美咲"))
    assert rec["state"] == "WAITING_HUMAN"
    found = character_findings(review)
    if not has(found, rule="address.C002", expected="美咲", actual="美咲ちゃん", scene="S1"):
        fail_with(label, review, rules, "address.C002 美咲/美咲ちゃん at S1 was not reported")


def test_d3a_address_change_violated(itest_env: tuple[Settings, Path]) -> None:
    label = "D3a"
    rec, review, rules = run_case(itest_env, label, c1=c001(CHANGE_AT_S2), text=draft_text("君", "美咲"))
    assert rec["state"] == "WAITING_HUMAN"
    found = character_findings(review)
    missing = []
    if not has(found, rule="address.C002", expected="美咲", actual="君", scene="S1"):
        missing.append("S1 美咲/君")
    if not has(found, rule="address.C002", expected="君", actual="美咲", scene="S2"):
        missing.append("S2 君/美咲")
    if missing:
        fail_with(label, review, rules, f"address.C002 not reported: {missing}")


def test_d3b_address_change_followed_and_absent_character(itest_env: tuple[Settings, Path]) -> None:
    label = "D3b"
    rec, review, rules = run_case(
        itest_env, label, c1=c001(CHANGE_AT_S2, teacher=True), text=draft_text("美咲", "君", teacher=True)
    )
    assert rec["state"] in ("COMPLETED", "WAITING_HUMAN")
    extra = [c for c in character_findings(review) if c["rule"] in ("address.C002", "address.C003", "address.default")]
    if extra:
        fail_with(label, review, rules, f"unexpected address findings: {extra}")


# ---------- 受入4：伏線（機械検査。Claude の結果は確かめない） ----------

def test_d4_foreshadow_order(itest_env: tuple[Settings, Path]) -> None:
    label = "D4"
    registry = copy.deepcopy(REGISTRY_OK)
    registry[0].update({
        "status": "active",
        "introduced": [{"chapter": "ch-001", "scene": "S2"}],
        "hints": [{"chapter": "ch-001", "scene": "S1"}],
    })
    rec, review, _ = run_case(itest_env, label, c1=c001(), text=draft_text("美咲", "美咲"), registry=registry)
    checks = {c["name"]: c for c in review["mechanical"]}
    assert checks["foreshadow_order"]["status"] == "WARNING"
    assert checks["foreshadow_order"]["details"] == ["F001 hints[0] ch-001 S1 is before introduced ch-001 S2"]
    assert rec["state"] == "WAITING_HUMAN"


def test_d5_foreshadow_overdue(itest_env: tuple[Settings, Path]) -> None:
    label = "D5"
    registry = copy.deepcopy(REGISTRY_OK)
    registry.append({
        "id": "F002", "name": "灯台の鍵", "status": "active", "importance": "major",
        "introduced": [{"chapter": "ch-001", "scene": "S1"}], "hints": [], "developments": [],
        "planned_resolution": {"chapter": "ch-001", "scene": "S2"}, "resolved": None, "notes": "",
    })
    rec, review, _ = run_case(
        itest_env, label, c1=c001(), text=draft_text("美咲", "美咲"), registry=registry,
        chapters=("ch-001", "ch-002"),
    )
    checks = {c["name"]: c for c in review["mechanical"]}
    assert checks["foreshadow_overdue"]["status"] == "WARNING"
    assert checks["foreshadow_overdue"]["details"] == ["F002 planned_resolution ch-001 S2 has passed but not resolved"]
    assert rec["state"] == "WAITING_HUMAN"
