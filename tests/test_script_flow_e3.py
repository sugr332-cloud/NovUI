"""2-E3: the script format in validate and plan (phase2e-design §4, §6)."""

import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_draftjob import SCRIPT_TEXT, FakeRunner, setup_script_work  # noqa: E402
from test_planjob import _make_valid_plan, make_envelope, novel_setup  # noqa: E402,F401
from test_validatejob import ok_claude, mock_container_meta  # noqa: E402,F401

from novui.chapters import add_chapter  # noqa: E402
from novui.charrules import character_table  # noqa: E402
from novui.draftjob import run_chapter_draft_job  # noqa: E402
from novui.gitinspect import run_git  # noqa: E402
from novui.planjob import collect_plan_context, run_plan_job  # noqa: E402
from novui.procrun import ProcResult  # noqa: E402
from novui.textformat import format_notes  # noqa: E402
from novui.validatejob import run_validate_job  # noqa: E402
from novui.workrepo import commit_all  # noqa: E402
from novui.yamlio import dumps_yaml, load_yaml, loads_yaml  # noqa: E402

CH = "ch-001"


def _review(repo: Path, branch: str) -> dict[str, Any]:
    return loads_yaml(run_git(repo, "show", f"{branch}:chapters/{CH}/review.yaml").decode("utf-8"))


# --- 規則表に表示名と表情


def test_rule_table_has_display_name_and_expressions() -> None:
    doc = {"id": "C001", "name": "山田太郎", "display_name": "カイ", "expressions": ["neutral", "happy"],
           "speech": {"first_person": "俺"}}
    table, _ = character_table(["ch-001"], doc, {"chapter": "ch-001", "scene": "S1"})
    assert list(table)[:5] == ["character", "name", "display_name", "expressions", "speech"]
    assert table["display_name"] == "カイ" and table["expressions"] == ["neutral", "happy"]


def test_format_notes() -> None:
    assert format_notes("novel") == "" and format_notes("novel", for_plan=True) == ""
    assert "ノベルゲームの台本の形式" in format_notes("script")
    assert "background" in format_notes("script", for_plan=True)
    assert format_notes("script") != format_notes("script", for_plan=True)


# --- validate（台本の形式）


def _drafted_script(tmp_path: Path, text: str = SCRIPT_TEXT) -> tuple[Any, Any, dict[str, Any]]:
    settings, work = setup_script_work(tmp_path)
    rec = run_chapter_draft_job(settings, work, CH, container_runner=FakeRunner(text))
    assert rec["state"] == "COMPLETED", rec["checks"]
    return settings, work, rec


def test_validate_script_mechanical_and_prompts(tmp_path: Path) -> None:
    settings, work, draft = _drafted_script(tmp_path)
    fake = ok_claude()
    rec = run_validate_job(settings, work, CH, claude_runner=fake)
    assert rec["state"] == "COMPLETED", rec["history"][-1]
    names = [c["name"] for c in _review(work.path, draft["branch"])["mechanical"]]
    assert names[0] == "char_count"
    assert [n for n in names if n.startswith("script_")] == [
        "script_syntax", "script_flow", "script_speakers", "script_expressions", "script_stage",
        "script_backgrounds", "script_speaker_words", "script_line_length", "script_suspicious_lines",
    ]
    assert names.index("script_suspicious_lines") < names.index("character_rules")
    integ_prompt, writ_prompt = fake.calls[0]["prompt"], fake.calls[1]["prompt"]
    for prompt in (integ_prompt, writ_prompt):
        assert "ノベルゲームの台本の形式で書かれています" in prompt
        assert "{{" not in prompt
    assert "display_name: カイ" in integ_prompt


def test_validate_script_problems_are_warnings(tmp_path: Path) -> None:
    text = SCRIPT_TEXT.replace("カイ「わけが分からない」", "カイ［angry］「僕には分からない」")
    settings, work, draft = _drafted_script(tmp_path, text)
    rec = run_validate_job(settings, work, CH, claude_runner=ok_claude())
    assert rec["state"] == "WAITING_HUMAN"
    mech = {c["name"]: c["status"] for c in _review(work.path, draft["branch"])["mechanical"]}
    assert mech["script_expressions"] == "WARNING" and mech["script_speaker_words"] == "WARNING"
    assert "FAIL" not in mech.values()


def test_validate_novel_prompts_have_no_script_notes(tmp_path: Path) -> None:
    from test_validatejob import drafted
    settings, work, _ = drafted(tmp_path)
    fake = ok_claude()
    run_validate_job(settings, work, CH, claude_runner=fake)
    assert all("台本の形式" not in c["prompt"] for c in fake.calls)


# --- plan（台本の形式）


def _make_script(repo: Path) -> None:
    project = load_yaml(repo / "project.yaml")
    project["text_format"] = "script"
    (repo / "project.yaml").write_text(dumps_yaml(project), encoding="utf-8")
    (repo / "assets").mkdir(exist_ok=True)
    (repo / "assets" / "backgrounds.yaml").write_text(dumps_yaml(["bg_market_day"]), encoding="utf-8")
    (repo / "flags").mkdir(exist_ok=True)
    (repo / "flags" / "registry.yaml").write_text(
        dumps_yaml([{"name": "passed_gate", "description": "門を通った", "notes": ""}]), encoding="utf-8")
    commit_all(repo, "script work", [("NovUI-Edit", "human-content")], name="Tester", email="tester@test")


def test_plan_context_and_prompt_for_script(novel_setup: Any) -> None:
    settings, work = novel_setup
    repo = work.path
    _make_script(repo)
    add_chapter(settings, work, CH, "第一章", "アウトライン本文")
    ctx = collect_plan_context(repo, CH)
    assert ctx[-3:] == ["assets/backgrounds.yaml", "flags/registry.yaml", f"chapters/{CH}/outline.md"]

    prompts: list[str] = []

    def fake_runner(*args: Any, **kwargs: Any) -> ProcResult:
        prompts.append(kwargs["prompt"])
        return ProcResult(0, 1.0, False, None, False, make_envelope(_make_valid_plan(CH)), b"")

    record = run_plan_job(settings, work, CH, claude_runner=fake_runner)
    assert record["state"] == "COMPLETED"
    assert "plan の各場面には、上の項目に加えて次を書いてください" in prompts[0]
    assert "{{" not in prompts[0]


def test_plan_prompt_for_novel_unchanged(novel_setup: Any) -> None:
    settings, work = novel_setup
    add_chapter(settings, work, CH, "第一章", "アウトライン本文")
    assert "assets/backgrounds.yaml" not in collect_plan_context(work.path, CH)
    prompts: list[str] = []

    def fake_runner(*args: Any, **kwargs: Any) -> ProcResult:
        prompts.append(kwargs["prompt"])
        return ProcResult(0, 1.0, False, None, False, make_envelope(_make_valid_plan(CH)), b"")

    run_plan_job(settings, work, CH, claude_runner=fake_runner)
    assert "台本の形式" not in prompts[0]
