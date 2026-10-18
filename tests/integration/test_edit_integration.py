"""Integration tests of edits, plan revision and instruction routing (phase2d5-design, 2-D10).

The AGY is fake (it returns fixed text and the test checks what it was asked); Claude is real. Nothing is adjusted to
make a run pass: when the real output is not what the flow needs, the test prints what it got and stops.

    NOVUI_INTEGRATION=1 .venv/bin/python -m pytest -s -rs -m integration tests/integration/test_edit_integration.py
"""

import os
from pathlib import Path
import secrets
import shutil
from typing import Any, Iterator

import pytest

from novui.chapters import add_chapter, read_chapter_meta, transition_on_main
from novui.config import Settings, load_settings
from novui.draftjob import run_chapter_draft_job
from novui.editjob import locate_range
from novui.jobqueue import fix_findings, load_queue, numbered_findings, run_queue
from novui.models import save_catalog, select_model
from novui.planjob import approve_plan, run_plan_revision_job
from novui.procrun import ProcResult
from novui.routingjob import approve_routing, run_routing_job
from novui.states import ChapterEvent
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

SETTING = "# 世界観\n\n舞台は架空の港町ミナト。港には市場と門があり、門には門番がいる。\n"
C001 = {"id": "C001", "name": "カイ", "speech": {"first_person": "俺", "forbidden": ["僕"]},
        "address": {"default": "お前"}, "personality": ["ぶっきらぼうだが親切"]}
OUTLINE = "カイが港町ミナトに着き、市場を抜けて、夕方に門を通る。門番はカイの持つ紋章に目を留めるが、何も言わない。"
GOOD = (
    "<!-- scene: S1 -->\n"
    "カイは港町ミナトに着いた。潮の匂いがした。\n"
    "市場は人であふれていた。カイは「俺には関係ない」とつぶやき、人混みを抜けた。\n"
    "\n"
    "<!-- scene: S2 -->\n"
    "夕方、カイは門の前に立った。\n"
    "門番がカイの胸元の紋章に目を留めた。だが、何も言わなかった。\n"
    "カイは黙って門を通った。\n"
)
BAD_LINE = "カイは「僕には関係ない」とつぶやき、人混みを抜けた。"
BAD = GOOD.replace("カイは「俺には関係ない」とつぶやき、人混みを抜けた。", BAD_LINE)


def plan() -> dict[str, Any]:
    base = {"actions": ["歩く"], "settings_used": ["港町ミナト"], "foreshadowing": []}
    return {
        "type": "plan", "chapter_id": CH, "pov": "三人称・カイに寄り添う",
        "style_notes": ["地の文は短く"], "target_chars": {"min": 50, "max": 2000},
        "context": {"past_summaries": [], "past_drafts": [], "settings": ["world/setting.md"]},
        "scenes": [
            {**base, "id": "S1", "characters": ["C001"], "summary": "カイが港町ミナトに着き、市場を抜ける"},
            {**base, "id": "S2", "characters": ["C001"],
             "summary": "夕方、門番がカイの紋章に目を留めるが何も言わず、カイは門を通る"},
        ],
        "prohibitions": ["紋章の由来を明かさない"],
        "connection": {"from_previous": "冒頭", "to_next": "門の内側の町へ"},
    }


class FakeAgy:
    """Returns the given texts in order and keeps the prompts."""

    def __init__(self, *texts: str) -> None:
        self.texts = list(texts)
        self.prompts: list[str] = []

    def __call__(self, **kwargs: Any) -> ProcResult:
        self.prompts.append(kwargs["command"][-1].removeprefix("--print="))
        text = self.texts.pop(0)
        return ProcResult(exit_code=0, elapsed_seconds=0.1, timed_out=False, signal_sent=None,
                          group_remaining=False, stdout=text.encode("utf-8"), stderr=b"")


@pytest.fixture
def env() -> Iterator[tuple[Settings, Path]]:
    root_dir = Path.home() / ".local/share/novui-itest" / secrets.token_hex(4)
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


@pytest.fixture(autouse=True)
def fake_image(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("novui.jobrunner.image_label", lambda img, key: "itest")
    monkeypatch.setattr(
        "novui.jobrunner.image_id",
        lambda img: "sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
    )


def make_work(env: tuple[Settings, Path], label: str, *, draft: str | None = GOOD) -> tuple[WorkInfo, dict | None]:
    settings, root_dir = env
    work = init_work(settings, root_dir / f"work-{label}", f"itest-{label.lower()}-{root_dir.name}", "結合試験")
    repo = work.path
    save_catalog(settings, load_yaml(FIXTURES / "model_catalog.yaml"))
    select_model(settings, "draft", FAKE_MODEL)
    (repo / "world" / "setting.md").write_text(SETTING, encoding="utf-8")
    (repo / "characters" / "C001.yaml").write_text(dumps_yaml(C001), encoding="utf-8")
    commit_all(repo, "itest setup", [("NovUI-Edit", "human-content")], name=settings.git_name, email=settings.git_email)
    add_chapter(settings, work, CH, "港町", OUTLINE)
    transition_on_main(
        settings, work, CH, ChapterEvent.PLAN_WRITTEN, subject="itest plan",
        extra_files={f"chapters/{CH}/plan.yaml": dumps_yaml(plan()).encode("utf-8")},
    )
    approve_plan(settings, work, CH)
    if draft is None:
        return work, None
    rec = run_chapter_draft_job(settings, work, CH, container_runner=FakeAgy(draft))
    assert rec["state"] == "COMPLETED", rec["checks"]
    return work, rec


def show_routing(label: str, rec: dict[str, Any], doc: dict[str, Any] | None) -> None:
    print(f"\n[{label}] routing state={rec['state']} reason={rec['history'][-1]['reason']}")
    for c in rec["checks"]:
        if c["status"] != "PASS":
            print(f"[{label}] check {c['name']}: {c['status']} {c['details']}")
    if doc is None:
        return
    r = doc["routing"]
    print(f"[{label}] status={doc['status']} scope_mismatch={r['scope_mismatch']} {r['scope_mismatch_reason']}")
    for i, job in enumerate(r["proposed_jobs"]):
        print(f"[{label}] [{i}] {job['job_type']} dep={job['depends_on']} summary={job['summary']}")
        print(f"[{label}]     instruction={job['instruction']}")
        if job["anchor"]:
            print(f"[{label}]     anchor={job['anchor']}")
    for q in r["questions"]:
        print(f"[{label}] question: {q}")


def show_review(label: str, review: dict[str, Any]) -> None:
    for fid, f in numbered_findings(review).items():
        print(f"[{label}] {fid} {f['severity']} {f['check']}: {f['message']} anchor={f['anchor']}")


# ---------- R1：範囲を選んだ指示 → range_edit ----------

def test_r1_range_instruction(env: tuple[Settings, Path]) -> None:
    settings, _ = env
    work, draft = make_work(env, "R1")
    assert draft is not None
    rec, doc = run_routing_job(settings, work, CH, "range", "門番の様子を、もう少し具体的に描いて",
                               anchor={"text": "門番がカイの胸元の紋章に目を留めた。"})
    show_routing("R1", rec, doc)
    assert rec["state"] == "COMPLETED" and doc is not None
    jobs = doc["routing"]["proposed_jobs"]
    assert [j["job_type"] for j in jobs] == ["range_edit"]
    text = (Path(draft["worktree"]) / f"chapters/{CH}/draft.md").read_text(encoding="utf-8")
    rng = locate_range(text, jobs[0]["anchor"])  # Claude's anchor matches exactly once
    print(f"[R1] range={rng.range_text!r} scene={rng.scene_id}")
    assert rng.scene_id == "S2"

    approve_routing(settings, work, doc["instruction_id"])
    agy = FakeAgy("門番は槍を持ち直し、カイの胸元の紋章をじっと見た。")
    records = run_queue(settings, work, container_runner=agy)
    assert [r["state"] for r in records] == ["COMPLETED"]
    assert "門番の様子を、もう少し具体的に描いて" in agy.prompts[0]


# ---------- R2：章全体への指示 → chapter_rewrite ----------

def test_r2_chapter_instruction(env: tuple[Settings, Path]) -> None:
    settings, _ = env
    work, draft = make_work(env, "R2")
    rec, doc = run_routing_job(settings, work, CH, "chapter",
                               "章全体の文体を、もう少し情景描写を増やした落ち着いた調子に書き直して")
    show_routing("R2", rec, doc)
    assert rec["state"] == "COMPLETED" and doc is not None
    jobs = doc["routing"]["proposed_jobs"]
    assert [j["job_type"] for j in jobs] == ["chapter_rewrite"], "expected one chapter_rewrite (plan unchanged)"
    approve_routing(settings, work, doc["instruction_id"])
    agy = FakeAgy(GOOD.replace("潮の匂いがした。", "潮の匂いが、ゆっくりと胸に満ちた。"))
    records = run_queue(settings, work, container_runner=agy)
    assert [r["state"] for r in records] == ["COMPLETED"]
    assert "【章の書き直し】" in agy.prompts[0]


# ---------- R3：曖昧な指示 → questions ----------

def test_r3_vague_instruction_asks(env: tuple[Settings, Path]) -> None:
    settings, _ = env
    work, _ = make_work(env, "R3")
    rec, doc = run_routing_job(settings, work, CH, "chapter", "なんか違う。直して")
    show_routing("R3", rec, doc)
    assert rec["state"] == "WAITING_HUMAN" and doc is not None and doc["status"] == "questions"


# ---------- R4：plan の改訂 ----------

def test_r4_plan_revision(env: tuple[Settings, Path]) -> None:
    settings, _ = env
    work, _ = make_work(env, "R4", draft=None)
    rec = run_plan_revision_job(settings, work, CH, "S2 で、カイが門番に軽く一礼してから門を通るようにする")
    print(f"\n[R4] plan_revision state={rec['state']} reason={rec['history'][-1]['reason']}")
    for c in rec["checks"]:
        if c["status"] != "PASS":
            print(f"[R4] check {c['name']}: {c['status']} {c['details']}")
    assert rec["state"] == "COMPLETED"
    assert read_chapter_meta(work.path, CH)["state"] == "PLANNED"
    new_plan = load_yaml(work.path / "chapters" / CH / "plan.yaml")
    for s in new_plan["scenes"]:
        print(f"[R4] {s['id']}: {s['summary']} / {s['actions']}")
    assert [s["id"] for s in new_plan["scenes"]] == ["S1", "S2"]
    s2 = new_plan["scenes"][1]
    assert "一礼" in s2["summary"] + "".join(s2["actions"])
    assert new_plan["scenes"][0]["summary"] == plan()["scenes"][0]["summary"], "S1 should be kept as it was"


# ---------- R5：検証の指摘 → fix → 再検証 ----------

def test_r5_fix_from_finding(env: tuple[Settings, Path]) -> None:
    settings, _ = env
    work, draft = make_work(env, "R5", draft=BAD)
    assert draft is not None
    v = run_validate_job(settings, work, CH)
    review = _read_branch_yaml(work.path, draft["branch"], f"chapters/{CH}/review.yaml")
    show_review("R5", review)
    assert v["state"] == "WAITING_HUMAN"
    found = numbered_findings(review)
    target = [fid for fid, f in found.items()
              if f["check"] == "character" and f["anchor"] and "僕" in f["anchor"]["text"] + f["message"]]
    assert target, "the first-person violation was not reported with an anchor"
    decide_validation(settings, work, CH, "REQUEST_FIX", "一人称を俺に直す")
    fix_findings(settings, work, CH, target[:1])
    text = (Path(draft["worktree"]) / f"chapters/{CH}/draft.md").read_text(encoding="utf-8")
    anchor = load_queue(settings, work.work_key)[0]["anchor"]
    rng = locate_range(text, anchor)
    print(f"[R5] range={rng.range_text!r}")
    replacement = rng.range_text.replace("僕", "俺")
    records = run_queue(settings, work, container_runner=FakeAgy(replacement))
    assert [r["state"] for r in records] == ["COMPLETED"]
    assert "僕" not in (Path(draft["worktree"]) / f"chapters/{CH}/draft.md").read_text(encoding="utf-8")

    v2 = run_validate_job(settings, work, CH)
    review2 = _read_branch_yaml(work.path, draft["branch"], f"chapters/{CH}/review.yaml")
    show_review("R5b", review2)
    character = review2["integrity"]["checks"]["character"]
    print(f"[R5b] validate state={v2['state']} character={character['result']}")
    assert not any("僕" in f["message"] for f in character["findings"])
