"""CLI tests for Phase 3-C: edit, reopen, and analyze-impact."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest

from novui.chapters import add_chapter, read_chapter_meta, read_chapters_order, transition_on_main
from novui.cli import main
from novui.config import Settings
from novui.finalize import final_approve
from novui.planjob import approve_plan
from novui.procrun import ProcResult
from novui.states import ChapterEvent
from novui.workrepo import commit_all
from novui.works import WorkInfo
from novui.yamlio import dumps_yaml, load_yaml

from test_draftjob import make_plan
from test_validatejob import GOOD_TEXT, envelope, setup_work


@pytest.fixture(autouse=True)
def mock_container_meta(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("novui.jobrunner.image_label", lambda img, key: "1.2.14")
    monkeypatch.setattr(
        "novui.jobrunner.image_id",
        lambda img: "sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
    )


class FakeClaude:
    def __init__(self, responses: list[Any] | None = None) -> None:
        self.responses = list(responses) if responses else []
        self.prompts: list[str] = []

    def __call__(self, **kwargs: Any) -> ProcResult:
        self.prompts.append(kwargs["prompt"])
        if not self.responses:
            raise RuntimeError(f"FakeClaude ran out of responses! Last prompt: {kwargs.get('prompt')[:200]}")
        resp = self.responses.pop(0)
        body = resp if isinstance(resp, bytes) else envelope(resp)
        return ProcResult(0, 1.0, False, None, False, body, b"")


def setup_novel(tmp_path: Path) -> tuple[Settings, WorkInfo]:
    settings, work = setup_work(tmp_path)
    repo = work.path
    c1 = {
        "id": "C001",
        "name": "カイ",
        "personality": ["冷静"],
        "speech": {"first_person": "俺"},
    }
    (repo / "characters" / "C001.yaml").write_text(dumps_yaml(c1), encoding="utf-8")
    commit_all(repo, "setup C001", [("NovUI-Edit", "human-content")], name="Tester", email="tester@test")
    return settings, work


def setup_final_chapter(settings: Settings, work: WorkInfo, cid: str = "ch-001") -> None:
    repo = work.path
    if cid not in read_chapters_order(repo):
        add_chapter(settings, work, cid, f"章 {cid}", f"{cid}のアウトライン\n")
        plan = make_plan(cid)
        transition_on_main(
            settings, work, cid, ChapterEvent.PLAN_WRITTEN,
            subject=f"plan {cid}",
            trailers=[("NovUI-Job", "job-1")],
            extra_files={f"chapters/{cid}/plan.yaml": dumps_yaml(plan).encode("utf-8")},
            last_job="job-1",
        )
        approve_plan(settings, work, cid)

    # Transition chapter to FINAL on main
    transition_on_main(
        settings, work, cid, ChapterEvent.DRAFT_COMPLETED,
        subject=f"draft {cid}",
        trailers=[("NovUI-Job", "job-10")],
        extra_files={f"chapters/{cid}/draft.md": GOOD_TEXT.encode("utf-8")},
        last_job="job-10",
    )
    transition_on_main(
        settings, work, cid, ChapterEvent.VALIDATION_SKIPPED,
        subject=f"validation skip {cid}",
        trailers=[("NovUI-Job", "job-11")],
        last_job="job-11",
    )
    summary = {
        "chapter_id": cid,
        "summary": f"{cid}のあらすじ要約",
        "characters": [
            {"id": "C001", "state": "通常", "new_knowledge": ["K001"]},
        ],
        "foreshadowing": [],
        "active_flags": [],
    }
    transition_on_main(
        settings, work, cid, ChapterEvent.STATE_PATCH_APPROVED,
        subject=f"state patch {cid}",
        trailers=[("NovUI-Job", "job-12")],
        extra_files={f"chapters/{cid}/summary.yaml": dumps_yaml(summary).encode("utf-8")},
        last_job="job-12",
    )
    transition_on_main(
        settings, work, cid, ChapterEvent.FINAL_APPROVED,
        subject=f"final approve {cid}",
        trailers=[("NovUI-Job", "job-13")],
        last_job="job-13",
    )


def test_cli_edit_typo(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    settings, work = setup_novel(tmp_path)
    setup_final_chapter(settings, work, "ch-001")
    repo = work.path

    # Modify draft.md
    draft_p = repo / "chapters/ch-001/draft.md"
    orig = draft_p.read_text(encoding="utf-8")
    draft_p.write_text(orig.replace("門番", "門番さん"), encoding="utf-8")

    capsys.readouterr()
    rc = main(
        ["edit", "--work", work.work_key, "--kind", "typo", "--note", "fix typo"],
        settings=settings,
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "Committed:" in out
    assert "Modified paths:" in out
    assert "chapters/ch-001/draft.md" in out
    # Chapter state remains FINAL
    meta = read_chapter_meta(repo, "ch-001")
    assert meta["state"] == "FINAL"
    assert meta["review_required"] is False


def test_cli_edit_content(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    settings, work = setup_novel(tmp_path)
    setup_final_chapter(settings, work, "ch-001")
    add_chapter(settings, work, "ch-002", "第二章", "第二章アウトライン\n")
    repo = work.path

    # Modify draft.md
    draft_p = repo / "chapters/ch-001/draft.md"
    orig = draft_p.read_text(encoding="utf-8")
    draft_p.write_text(orig.replace("門を通った。", "門を通った。新規文。"), encoding="utf-8")

    capsys.readouterr()
    rc = main(
        ["edit", "--work", work.work_key, "--kind", "content", "--note", "new scene content"],
        settings=settings,
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "Committed:" in out
    assert "Drafted chapters: ch-001" in out
    assert "Review marked chapters: ch-002" in out

    meta1 = read_chapter_meta(repo, "ch-001")
    assert meta1["state"] == "DRAFTED"
    assert meta1["review_required"] is True

    meta2 = read_chapter_meta(repo, "ch-002")
    assert meta2["review_required"] is True


def test_cli_reopen(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    settings, work = setup_novel(tmp_path)
    setup_final_chapter(settings, work, "ch-001")
    repo = work.path

    # Edit draft to make it DRAFTED
    draft_p = repo / "chapters/ch-001/draft.md"
    orig = draft_p.read_text(encoding="utf-8")
    draft_p.write_text(orig.replace("門を通った。", "門を通った。新規文。"), encoding="utf-8")
    main(["edit", "--work", work.work_key, "--kind", "content"], settings=settings)

    capsys.readouterr()
    rc = main(["reopen", "--work", work.work_key, "--chapter", "ch-001"], settings=settings)
    assert rc == 0
    out = capsys.readouterr().out
    assert "Chapter ch-001 reopened." in out
    assert "Job ID: job-" in out
    assert "Branch: ai/ch-001/job-" in out
    assert "Next: run 'novui validate" in out


def test_cli_impact_analysis_flow(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    settings, work = setup_novel(tmp_path)
    setup_final_chapter(settings, work, "ch-001")
    repo = work.path

    # Edit character C001
    c1_path = repo / "characters/C001.yaml"
    c1 = load_yaml(c1_path)
    c1["personality"] = ["冷静", "勇敢"]
    c1_path.write_text(dumps_yaml(c1), encoding="utf-8")

    # 1. Edit with impact job failure (FakeClaude returns error)
    capsys.readouterr()
    rc = main(
        ["edit", "--work", work.work_key, "--kind", "typo", "--note", "update C001"],
        settings=settings,
        claude_runner=FakeClaude([envelope(None, is_error=True)] * 3),
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "Committed:" in out
    assert "Impact analysis failed: claude unavailable" in out
    assert "You can rerun with: novui analyze-impact --work" in out

    commit_sha = out.split("Committed: ")[1].splitlines()[0].strip()

    # Make another commit on main before running analyze-impact (R2 scenario)
    draft_p = repo / "chapters/ch-001/draft.md"
    orig_d = draft_p.read_text(encoding="utf-8")
    draft_p.write_text(orig_d.replace("門番", "門番さん"), encoding="utf-8")
    rc_typo = main(["edit", "--work", work.work_key, "--kind", "typo", "--note", "fix typo"], settings=settings)
    assert rc_typo == 0

    # 2. Rerun with analyze-impact CLI on earlier ancestor commit
    impact_resp = {
        "type": "impact_analysis",
        "affected": [
            {"chapter_id": "ch-001", "reason": "カイの性格変更による影響"}
        ],
        "notes": ["性格の変更に注意"],
    }
    capsys.readouterr()
    rc_ai = main(
        [
            "analyze-impact",
            "--work",
            work.work_key,
            "--commit",
            commit_sha,
            "--reason",
            "C001 性格更新の分析",
        ],
        settings=settings,
        claude_runner=FakeClaude([impact_resp]),
    )
    assert rc_ai == 0
    out_ai = capsys.readouterr().out
    assert "Impact analysis Job: job-" in out_ai
    assert "[COMPLETED]" in out_ai
    assert "Affected chapters:" in out_ai
    assert "ch-001: カイの性格変更による影響" in out_ai
    assert "Notes:" in out_ai
    assert "性格の変更に注意" in out_ai
