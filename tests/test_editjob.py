"""Tests for novui.editjob (range_edit on the job branch; fake AGY, no podman)."""

import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_draftjob import SCRIPT_TEXT, FakeRunner, setup_script_work  # noqa: E402
from test_requestflow import CaptureAgy  # noqa: E402
from test_validatejob import CH, GOOD_TEXT, drafted, ok_claude  # noqa: E402

from novui.anchor import AnchorError
from novui.chapters import ChapterError, read_chapter_meta
from novui.draftjob import run_chapter_draft_job
from novui.editjob import CONTEXT_CHARS, locate_range, run_range_edit_job
from novui.gitinspect import get_changes, run_git
from novui.jobrecord import load_job_record
from novui.jobrunner import jobs_dir
from novui.requestflow import resolve_request
from novui.validatejob import run_validate_job
from novui.workrepo import commit_all, head_commit, parse_trailers
from novui.yamlio import dumps_yaml, load_yaml


@pytest.fixture(autouse=True)
def mock_container_meta(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("novui.jobrunner.image_label", lambda img, key: "1.2.14")
    monkeypatch.setattr(
        "novui.jobrunner.image_id",
        lambda img: "sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
    )


DRAFT = f"chapters/{CH}/draft.md"
REQ = f"chapters/{CH}/requests.yaml"
ANCHOR = {"text": "門番が紋章に目を留めた。", "before": "", "after": ""}
NEW = "門番は紋章を見て、黙って道をあけた。"
HUMAN = {"kind": "human"}


def _edit(settings: Any, work: Any, agy: Any, **kw: Any) -> dict[str, Any]:
    args: dict[str, Any] = {"anchor": ANCHOR, "reason": "門番の反応をはっきりさせる", "source": HUMAN}
    args.update(kw)
    return run_range_edit_job(settings, work, CH, container_runner=agy, **args)


def _wt(rec: dict[str, Any]) -> Path:
    return Path(rec["worktree"])


def _checks(record: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {c["name"]: c for c in record["checks"]}


# ---------- locate_range ----------

def test_locate_range_scene_and_context() -> None:
    text = GOOD_TEXT
    rng = locate_range(text, ANCHOR)
    assert rng.scene_id == "S1"
    assert rng.range_text == "門番が紋章に目を留めた。"
    assert rng.before_text == "カイは港町ミナトに着いた。"
    assert rng.after_text == "\n\n"  # the scene ends before the next marker line
    data = text.encode("utf-8")
    assert data[rng.start:rng.end].decode("utf-8") == rng.range_text

    rng2 = locate_range(text, {"text": "門を通った。"})
    assert rng2.scene_id == "S2" and rng2.before_text == "カイは理由が分からないまま"


def test_locate_range_context_is_limited() -> None:
    long = "あ" * (CONTEXT_CHARS + 50)
    text = f"<!-- scene: S1 -->\n{long}対象。{long}\n<!-- scene: S2 -->\n終わり。\n"
    rng = locate_range(text, {"text": "対象。"})
    assert len(rng.before_text) == CONTEXT_CHARS and len(rng.after_text) == CONTEXT_CHARS


def test_locate_range_errors() -> None:
    with pytest.raises(AnchorError) as e0:
        locate_range(GOOD_TEXT, {"text": "存在しない文"})
    assert e0.value.count == 0
    with pytest.raises(AnchorError) as e2:
        locate_range(GOOD_TEXT, {"text": "カイは"})
    assert e2.value.count == 2
    # before/after make it unique
    assert locate_range(GOOD_TEXT, {"text": "カイは", "before": "\n", "after": "理由"}).scene_id == "S2"
    with pytest.raises(ChapterError, match="scene marker"):
        locate_range(GOOD_TEXT, {"text": "\n\n<!-- scene: S2 -->\nカイは理由"})
    with pytest.raises(ChapterError, match="not inside a scene"):
        locate_range("前書き。\n<!-- scene: S1 -->\n本文。\n", {"text": "前書き。"})


# ---------- run_range_edit_job ----------

def test_range_edit_success(tmp_path: Path) -> None:
    settings, work, draft_rec = drafted(tmp_path)
    wt = _wt(draft_rec)
    head0 = head_commit(wt, "HEAD")
    main0 = head_commit(work.path, "main")
    agy = CaptureAgy(NEW + "\n")
    rec = _edit(settings, work, agy)
    assert rec["state"] == "COMPLETED", rec["checks"]
    assert rec["job_type"] == "range_edit"
    assert rec["branch"] == draft_rec["branch"] and rec["worktree"] == draft_rec["worktree"]
    assert rec["base_commit"] == head0
    assert rec["edit"]["anchor"] == ANCHOR and rec["edit"]["source"] == HUMAN
    assert rec["edit"]["reason"] == "門番の反応をはっきりさせる"
    assert rec["context"] == draft_rec["context"]
    assert rec["cli"]["name"] == "agy"

    text = (wt / DRAFT).read_text(encoding="utf-8")
    assert text == GOOD_TEXT.replace("門番が紋章に目を留めた。", NEW)
    meta = read_chapter_meta(wt, CH)
    assert meta["state"] == "DRAFTED" and meta["last_job"] == rec["job_id"]
    # one commit with the job trailer; main is not changed; the worktree is clean
    assert run_git(wt, "rev-list", "--count", f"{head0}..HEAD").decode().strip() == "1"
    msg = run_git(wt, "log", "-1", "--format=%B").decode()
    assert parse_trailers(msg)["NovUI-Job"] == [rec["job_id"]]
    assert head_commit(work.path, "main") == main0
    assert get_changes(wt) == []
    checks = _checks(rec)
    assert checks["prefix_suffix"]["status"] == "PASS"
    assert checks["scene_markers"]["status"] == "PASS"
    assert checks["allowed_paths"]["status"] == "PASS"

    prompt = agy.prompt()
    assert "場面 S1 の本文のうち" in prompt
    assert "門番の反応をはっきりさせる" in prompt
    assert "書き直す範囲：\n\n```text\n門番が紋章に目を留めた。\n```" in prompt
    assert "```text\nカイは港町ミナトに着いた。\n```" in prompt
    assert "（場面の終わり）" in prompt
    assert "character: C001" in prompt
    assert "台本の形式" not in prompt
    assert "{{" not in prompt


def test_range_edit_after_validation_returns_to_drafted(tmp_path: Path) -> None:
    settings, work, draft_rec = drafted(tmp_path)
    v = run_validate_job(settings, work, CH, claude_runner=ok_claude())
    assert v["state"] == "COMPLETED"
    assert read_chapter_meta(_wt(draft_rec), CH)["state"] == "AI_VALIDATED"
    rec = _edit(settings, work, CaptureAgy(NEW))
    assert rec["state"] == "COMPLETED"
    assert read_chapter_meta(_wt(draft_rec), CH)["state"] == "DRAFTED"
    # validate can run again on the edited text
    v2 = run_validate_job(settings, work, CH, claude_runner=ok_claude())
    assert v2["state"] == "COMPLETED"


def test_range_edit_anchor_not_unique_waits(tmp_path: Path) -> None:
    settings, work, draft_rec = drafted(tmp_path)
    wt = _wt(draft_rec)
    head0 = head_commit(wt, "HEAD")
    agy = CaptureAgy(NEW)
    rec = _edit(settings, work, agy, anchor={"text": "カイは"})
    assert rec["state"] == "WAITING_HUMAN"
    assert rec["history"][-1]["reason"] == "anchor_not_unique: 2"
    assert rec["edit"]["range_bytes"] is None
    assert agy.commands == []
    assert head_commit(wt, "HEAD") == head0
    # a waiting Job blocks the next edit
    with pytest.raises(ChapterError, match="waiting for a Human decision"):
        _edit(settings, work, agy)


def test_range_edit_marker_keeps_text_and_retry(tmp_path: Path) -> None:
    settings, work, draft_rec = drafted(tmp_path)
    wt = _wt(draft_rec)
    head0 = head_commit(wt, "HEAD")
    first = _edit(settings, work, CaptureAgy("門番は【要確認：門番の名前】と名乗った。"))
    assert first["state"] == "WAITING_HUMAN"
    assert first["history"][-1]["reason"] == "undefined_settings: 1"
    assert (wt / DRAFT).read_text(encoding="utf-8") == GOOD_TEXT
    assert read_chapter_meta(wt, CH)["state"] == "DRAFTED"
    assert run_git(wt, "diff", "--name-only", head0, "HEAD").decode().split() == [REQ]
    assert get_changes(wt) == []
    reqs = load_yaml(wt / REQ)
    assert reqs[0]["job_id"] == first["job_id"] and reqs[0]["message"] == "門番の名前"

    with pytest.raises(ChapterError, match="not resolved"):
        _edit(settings, work, CaptureAgy(NEW), retry_of=first["job_id"])
    resolve_request(settings, work, CH, 0, "門番に名前は付けない")

    agy = CaptureAgy(NEW)
    second = _edit(settings, work, agy, retry_of=first["job_id"])
    assert second["state"] == "COMPLETED"
    old = load_job_record(jobs_dir(settings, work.work_key) / f"{first['job_id']}.yaml")
    assert old["state"] == "CANCELLED" and old["history"][-1]["reason"] == f"retried as {second['job_id']}"
    prompt = agy.prompt()
    assert "【Human の判断】" in prompt
    assert "* 確認事項：門番の名前\n  判断：門番に名前は付けない" in prompt
    assert (wt / DRAFT).read_text(encoding="utf-8") == GOOD_TEXT.replace("門番が紋章に目を留めた。", NEW)


def test_range_edit_retry_requires_waiting_range_edit(tmp_path: Path) -> None:
    settings, work, draft_rec = drafted(tmp_path)
    with pytest.raises(ChapterError, match="not a range_edit"):
        _edit(settings, work, CaptureAgy(NEW), retry_of=draft_rec["job_id"])
    done = _edit(settings, work, CaptureAgy(NEW))
    with pytest.raises(ChapterError, match="not WAITING_HUMAN"):
        _edit(settings, work, CaptureAgy(NEW), retry_of=done["job_id"], anchor={"text": NEW})


@pytest.mark.parametrize("output, check", [
    ("<!-- scene: S1 -->\n" + NEW, "range_scene_marker"),
    ("", "agy_output"),
])
def test_range_edit_bad_output_fails_without_change(tmp_path: Path, output: str, check: str) -> None:
    settings, work, draft_rec = drafted(tmp_path)
    wt = _wt(draft_rec)
    head0 = head_commit(wt, "HEAD")
    rec = _edit(settings, work, CaptureAgy(output))
    assert rec["state"] == "FAILED"
    assert _checks(rec)[check]["status"] == "FAIL"
    assert head_commit(wt, "HEAD") == head0
    assert get_changes(wt) == []
    assert (wt / DRAFT).read_text(encoding="utf-8") == GOOD_TEXT


def test_range_edit_range_with_marker_makes_no_job(tmp_path: Path) -> None:
    settings, work, _ = drafted(tmp_path)
    jdir = jobs_dir(settings, work.work_key)
    before = sorted(p.name for p in jdir.glob("job-*.yaml"))
    with pytest.raises(ChapterError, match="scene marker"):
        _edit(settings, work, CaptureAgy(NEW), anchor={"text": "\n\n<!-- scene: S2 -->\nカイは理由"})
    assert sorted(p.name for p in jdir.glob("job-*.yaml")) == before


def test_range_edit_requires_editable_state(tmp_path: Path) -> None:
    settings, work, draft_rec = drafted(tmp_path)
    wt = _wt(draft_rec)
    meta = load_yaml(wt / f"chapters/{CH}/chapter.yaml")
    meta["state"] = "HUMAN_APPROVED"
    (wt / f"chapters/{CH}/chapter.yaml").write_text(dumps_yaml(meta), encoding="utf-8")
    commit_all(wt, "test: approved", [("NovUI-Job", draft_rec["job_id"])], name="Tester", email="tester@test")
    with pytest.raises(ChapterError, match="DRAFTED or AI_VALIDATED"):
        _edit(settings, work, CaptureAgy(NEW))


def test_range_edit_requires_branch(tmp_path: Path) -> None:
    from test_validatejob import setup_work
    settings, work = setup_work(tmp_path)
    with pytest.raises(ChapterError):
        _edit(settings, work, CaptureAgy(NEW))


def test_range_edit_blocked_by_waiting_validation(tmp_path: Path) -> None:
    from test_validatejob import integrity
    settings, work, _ = drafted(tmp_path)
    v = run_validate_job(settings, work, CH, claude_runner=ok_claude(integrity("WARNING")))
    assert v["state"] == "WAITING_HUMAN"
    with pytest.raises(ChapterError, match="waiting for a Human decision"):
        _edit(settings, work, CaptureAgy(NEW))


def test_range_edit_reason_required(tmp_path: Path) -> None:
    settings, work, _ = drafted(tmp_path)
    with pytest.raises(ValueError):
        _edit(settings, work, CaptureAgy(NEW), reason="  ")


# ---------- 台本の形式 ----------

def _script_drafted(tmp_path: Path) -> tuple[Any, Any, dict[str, Any]]:
    settings, work = setup_script_work(tmp_path)
    rec = run_chapter_draft_job(settings, work, CH, container_runner=FakeRunner(SCRIPT_TEXT))
    assert rec["state"] == "COMPLETED", rec["checks"]
    return settings, work, rec


def test_script_range_edit_success_and_prompt(tmp_path: Path) -> None:
    settings, work, draft_rec = _script_drafted(tmp_path)
    agy = CaptureAgy("カイ［happy］「やっと着いた」\n")
    rec = _edit(settings, work, agy, anchor={"text": "カイ「わけが分からない」"})
    assert rec["state"] == "COMPLETED", rec["checks"]
    assert _checks(rec)["script_syntax"]["status"] == "PASS"
    prompt = agy.prompt()
    assert "書き直す範囲は台本の形式です" in prompt
    assert "* 登場：`@enter <人物 ID> <left か center か right> <表情>`" in prompt
    assert "display_name: カイ" in prompt
    text = (_wt(draft_rec) / DRAFT).read_text(encoding="utf-8")
    assert text.endswith("@enter C001 center\nカイ［happy］「やっと着いた」\n")


def test_script_range_edit_syntax_error_fails(tmp_path: Path) -> None:
    settings, work, draft_rec = _script_drafted(tmp_path)
    head0 = head_commit(_wt(draft_rec), "HEAD")
    rec = _edit(settings, work, CaptureAgy("@enter C001 top happy\n"), anchor={"text": "@enter C001 center\n"})
    assert rec["state"] == "FAILED"
    assert _checks(rec)["script_syntax"]["status"] == "FAIL"
    assert head_commit(_wt(draft_rec), "HEAD") == head0
    assert get_changes(_wt(draft_rec)) == []
