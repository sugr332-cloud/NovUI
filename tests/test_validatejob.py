"""Tests for novui.validatejob module (fake container / Claude runners; no podman, agy or claude)."""

import copy
import json
from pathlib import Path
from typing import Any, Callable
import pytest

from novui.chapters import ChapterError, add_chapter, read_chapter_meta, transition_on_main
from novui.claudejob import ClaudeJobRequest, run_claude_job
from novui.config import Settings
from novui.draftjob import run_chapter_draft_job
from novui.gitinspect import run_git
from novui.jobrecord import load_job_record
from novui.jobrunner import jobs_dir
from novui.models import save_catalog, select_model
from novui.planjob import approve_plan
from novui.procrun import ProcResult
from novui.schema import validate_or_raise
from novui.states import ChapterEvent
from novui.validatejob import (
    _write_and_commit,
    decide_validation,
    run_validate_job,
    skip_validation,
)
from novui.workinit import init_work
from novui.workrepo import commit_all, head_commit, parse_trailers
from novui.works import WorkInfo
from novui.yamlio import dumps_yaml, load_yaml, loads_yaml
from novui import cli

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "valid"
MODEL = "gemini-3.8-flash-high"
CH = "ch-001"
CHECK_KEYS = ("character", "world", "timeline", "plot", "foreshadowing", "plan_compliance", "undefined_setting")

GOOD_TEXT = (
    "<!-- scene: S1 -->\n"
    "カイは港町ミナトに着いた。門番が紋章に目を留めた。\n"
    "\n"
    "<!-- scene: S2 -->\n"
    "カイは理由が分からないまま門を通った。\n"
)
WARN_TEXT = GOOD_TEXT.replace("門を通った。", "門を通った。まるで嘘のようだった。")


def _make_settings(tmp_path: Path) -> Settings:
    data_dir = tmp_path / "data"
    token_path = tmp_path / "fake-token"
    token_path.write_text("token", encoding="utf-8")
    return Settings(
        data_dir=data_dir,
        worktree_root=data_dir / "worktrees",
        jobhome_root=data_dir / "jobhomes",
        agy_image="test:img",
        agy_token_path=token_path,
        timeouts={"agy_draft": 300, "claude": 300},
        git_name="Tester",
        git_email="tester@novui.local",
    )


@pytest.fixture(autouse=True)
def mock_container_meta(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("novui.jobrunner.image_label", lambda img, key: "1.2.14")
    monkeypatch.setattr(
        "novui.jobrunner.image_id",
        lambda img: "sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
    )


def make_plan() -> dict[str, Any]:
    plan = copy.deepcopy(load_yaml(FIXTURES_DIR / "plan.yaml"))
    plan["chapter_id"] = CH
    plan["target_chars"] = {"min": 10, "max": 200}
    plan["context"] = {"past_summaries": [], "past_drafts": [], "settings": ["world/setting.md"]}
    s1 = copy.deepcopy(plan["scenes"][0])
    s1.update({"id": "S1", "characters": ["C001"], "foreshadowing": ["F001"]})
    s2 = copy.deepcopy(plan["scenes"][0])
    s2.update({"id": "S2", "summary": "門を通る", "characters": ["C001"], "foreshadowing": []})
    plan["scenes"] = [s1, s2]
    return plan


def setup_work(tmp_path: Path) -> tuple[Settings, WorkInfo]:
    settings = _make_settings(tmp_path)
    work = init_work(settings, tmp_path / "novel", "test-novel", "テスト小説")
    repo = work.path
    (repo / "world" / "setting.md").write_text("# 世界観\n\n港町ミナト。\n", encoding="utf-8")
    c1 = {"id": "C001", "name": "カイ", "speech": {"first_person": "俺", "forbidden": ["僕"]}}
    (repo / "characters" / "C001.yaml").write_text(dumps_yaml(c1), encoding="utf-8")
    reg = [{
        "id": "F001", "name": "王家の紋章", "status": "planned", "importance": "major",
        "introduced": [], "hints": [], "developments": [],
        "planned_resolution": None, "resolved": None, "notes": "",
    }]
    (repo / "foreshadowing" / "registry.yaml").write_text(dumps_yaml(reg), encoding="utf-8")
    (repo / "rules" / "prohibited.yaml").write_text(
        dumps_yaml({"phrases": ["まるで嘘のよう", "僕"], "repeated_ending_threshold": 3}), encoding="utf-8"
    )
    commit_all(repo, "setup", [("NovUI-Edit", "human-content")], name="Tester", email="tester@test")
    add_chapter(settings, work, CH, "港へ", "カイが港町に着く。")
    transition_on_main(
        settings, work, CH, ChapterEvent.PLAN_WRITTEN,
        subject="plan ch-001 by job-1",
        trailers=[("NovUI-Job", "job-1")],
        extra_files={"chapters/ch-001/plan.yaml": dumps_yaml(make_plan()).encode("utf-8")},
        last_job="job-1",
    )
    approve_plan(settings, work, CH)
    save_catalog(settings, load_yaml(FIXTURES_DIR / "model_catalog.yaml"))
    select_model(settings, "draft", MODEL)
    return settings, work


def _agy_runner(text: str) -> Callable[..., ProcResult]:
    def run(**kwargs: Any) -> ProcResult:
        return ProcResult(
            exit_code=0, elapsed_seconds=1.0, timed_out=False, signal_sent=None,
            group_remaining=False, stdout=text.encode("utf-8"), stderr=b"",
        )
    return run


def drafted(tmp_path: Path, text: str = GOOD_TEXT) -> tuple[Settings, WorkInfo, dict[str, Any]]:
    settings, work = setup_work(tmp_path)
    rec = run_chapter_draft_job(settings, work, CH, container_runner=_agy_runner(text))
    assert rec["state"] == "COMPLETED"
    return settings, work, rec


# --- Claude の出力 ---

def integrity(result: str = "PASS", check: str = "character") -> dict[str, Any]:
    checks = {k: {"result": "PASS", "findings": []} for k in CHECK_KEYS}
    if result != "PASS":
        checks[check] = {
            "result": result,
            "findings": [{
                "severity": result,
                "anchor": {"text": "門番が紋章に目を留めた。", "before": "", "after": ""},
                "message": "C001 speech.first_person: 期待 俺、本文 僕（S1）",
                "evidence": ["characters/C001.yaml speech.first_person: 俺"],
            }],
        }
    return {"type": "integrity_review", "chapter_id": CH, "result": result, "checks": checks}


def writing(findings: int = 0) -> dict[str, Any]:
    return {
        "type": "writing_review",
        "chapter_id": CH,
        "findings": [
            {
                "id": f"W{i + 1}", "category": "redundant",
                "anchor": {"text": "カイは港町ミナトに着いた。", "before": "", "after": ""},
                "message": "冗長", "suggestion": None,
            }
            for i in range(findings)
        ],
    }


def envelope(out: Any, *, is_error: bool = False) -> bytes:
    env = {
        "type": "result",
        "is_error": is_error,
        "result": "" if out is None else json.dumps(out, ensure_ascii=False),
        "structured_output": out,
        "modelUsage": {"claude-opus-5-5": {}},
        "total_cost_usd": 0.01,
        "permission_denials": [],
    }
    return json.dumps(env, ensure_ascii=False).encode("utf-8")


class FakeClaude:
    """Returns queued stdout per review type, routed by the task text in the prompt."""

    def __init__(self, integrity_out: list[bytes], writing_out: list[bytes] | None = None, *, timed_out: bool = False) -> None:
        self.queues = {"integrity_review": list(integrity_out), "writing_review": list(writing_out or [])}
        self.timed_out = timed_out
        self.calls: list[dict[str, Any]] = []

    def __call__(self, **kwargs: Any) -> ProcResult:
        assert list(kwargs["cwd"].iterdir()) == []
        kind = "integrity_review" if "type：`integrity_review`" in kwargs["prompt"] else "writing_review"
        self.calls.append({"kind": kind, **kwargs})
        out = self.queues[kind].pop(0) if self.queues[kind] else b""
        return ProcResult(
            exit_code=0, elapsed_seconds=2.0, timed_out=self.timed_out, signal_sent=None,
            group_remaining=False, stdout=out, stderr=b"",
        )

    def kinds(self) -> list[str]:
        return [c["kind"] for c in self.calls]


def ok_claude(integ: dict[str, Any] | None = None, writ: dict[str, Any] | None = None) -> FakeClaude:
    return FakeClaude([envelope(integ or integrity())], [envelope(writ or writing())])


# --- 確認用 ---

def _branch(repo: Path) -> str:
    out = run_git(repo, "for-each-ref", "--format=%(refname:short)", "refs/heads/ai/ch-001/").decode("utf-8").split()
    assert len(out) == 1
    return out[0]


def _branch_yaml(repo: Path, rel: str) -> Any:
    return loads_yaml(run_git(repo, "show", f"{_branch(repo)}:{rel}").decode("utf-8"))


def _branch_has(repo: Path, rel: str) -> bool:
    out = run_git(repo, "ls-tree", "--name-only", _branch(repo), rel).decode("utf-8").strip()
    return bool(out)


def _main_snapshot(repo: Path) -> tuple[str, bytes]:
    return head_commit(repo, "main"), (repo / "chapters" / CH / "chapter.yaml").read_bytes()


def _checks(record: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {c["name"]: c for c in record["checks"]}


def _saved(settings: Settings, work: WorkInfo, record: dict[str, Any]) -> dict[str, Any]:
    return load_job_record(jobs_dir(settings, work.work_key) / f"{record['job_id']}.yaml")


# 1. 正常系：機械検査 PASS、integrity PASS、writing 取得 → AI_VALIDATED
def test_validate_pass(tmp_path: Path) -> None:
    settings, work, draft = drafted(tmp_path)
    repo = work.path
    main_before = _main_snapshot(repo)
    branch_before = head_commit(repo, _branch(repo))
    draft_before = run_git(repo, "show", f"{_branch(repo)}:chapters/ch-001/draft.md")

    fake = ok_claude()
    rec = run_validate_job(settings, work, CH, claude_runner=fake)

    assert rec["state"] == "COMPLETED"
    assert [h["state"] for h in rec["history"]] == ["QUEUED", "RUNNING", "CHECKING", "VALIDATING", "COMPLETED"]
    assert fake.kinds() == ["integrity_review", "writing_review"]
    assert rec["job_type"] == "validate"
    assert rec["base_commit"] == draft["base_commit"]
    assert rec["branch"] == draft["branch"]
    assert rec["cli"]["actual_model"] == "claude-opus-5-5"
    assert _saved(settings, work, rec)["state"] == "COMPLETED"
    ctx_paths = [c["path"] for c in rec["context"]]
    assert ctx_paths == [c["path"] for c in draft["context"]] + ["chapters/ch-001/draft.md"]

    review = _branch_yaml(repo, "chapters/ch-001/review.yaml")
    validate_or_raise(review, "review")
    assert review["job_id"] == rec["job_id"]
    assert review["base_commit"] == draft["base_commit"]
    assert review["integrity"]["result"] == "PASS"
    assert review["writing"]["type"] == "writing_review"
    assert review["decision"] is None
    assert all(c["status"] == "PASS" for c in review["mechanical"])
    assert {c["name"] for c in review["mechanical"]} >= {"char_count", "scene_markers", "ref_ids", "prohibited_phrases"}

    meta = _branch_yaml(repo, "chapters/ch-001/chapter.yaml")
    assert meta["state"] == "AI_VALIDATED"
    assert meta["validation_skipped"] is False
    assert meta["last_job"] == rec["job_id"]

    # 作業ブランチに1 commit、変更は review.yaml と chapter.yaml だけ、draft.md は変えない
    branch_after = head_commit(repo, _branch(repo))
    assert run_git(repo, "rev-list", "--count", f"{branch_before}..{branch_after}").decode().strip() == "1"
    changed = run_git(repo, "diff", "--name-only", branch_before, branch_after).decode().split()
    assert sorted(changed) == ["chapters/ch-001/chapter.yaml", "chapters/ch-001/review.yaml"]
    msg = run_git(repo, "log", "-1", "--format=%B", branch_after).decode()
    assert parse_trailers(msg) == {"NovUI-Job": [rec["job_id"]]}
    assert run_git(repo, "show", f"{_branch(repo)}:chapters/ch-001/draft.md") == draft_before

    # main は変えない
    assert _main_snapshot(repo) == main_before
    assert read_chapter_meta(repo, CH)["state"] == "PLAN_APPROVED"
    # Claude の作業ディレクトリは残らない
    assert not (settings.data_dir / "claude-cwd" / rec["job_id"]).exists()


# 2. 機械検査の WARNING → WAITING_HUMAN
def test_validate_mechanical_warning(tmp_path: Path) -> None:
    settings, work, _ = drafted(tmp_path, WARN_TEXT)
    rec = run_validate_job(settings, work, CH, claude_runner=ok_claude())
    assert rec["state"] == "WAITING_HUMAN"
    assert [h["state"] for h in rec["history"]][-2:] == ["VALIDATING", "WAITING_HUMAN"]
    assert "mechanical: prohibited_phrases" in rec["history"][-1]["reason"]
    review = _branch_yaml(work.path, "chapters/ch-001/review.yaml")
    assert {c["name"]: c["status"] for c in review["mechanical"]}["prohibited_phrases"] == "WARNING"
    assert review["integrity"]["result"] == "PASS"
    assert _branch_yaml(work.path, "chapters/ch-001/chapter.yaml")["state"] == "DRAFTED"


# 3. integrity WARNING → WAITING_HUMAN
def test_validate_integrity_warning(tmp_path: Path) -> None:
    settings, work, _ = drafted(tmp_path)
    rec = run_validate_job(settings, work, CH, claude_runner=ok_claude(integrity("WARNING")))
    assert rec["state"] == "WAITING_HUMAN"
    assert rec["history"][-1]["reason"] == "integrity: WARNING"
    assert _branch_yaml(work.path, "chapters/ch-001/chapter.yaml")["state"] == "DRAFTED"
    assert _branch_yaml(work.path, "chapters/ch-001/review.yaml")["decision"] is None


# 4. integrity STOP → WAITING_HUMAN。CONTINUE / OVERRIDE で突破できない
def test_validate_integrity_stop_blocks_continue_override(tmp_path: Path) -> None:
    settings, work, _ = drafted(tmp_path)
    rec = run_validate_job(settings, work, CH, claude_runner=ok_claude(integrity("STOP", "plan_compliance")))
    assert rec["state"] == "WAITING_HUMAN"
    assert rec["history"][-1]["reason"] == "integrity: STOP"
    branch_head = head_commit(work.path, _branch(work.path))

    with pytest.raises(ChapterError, match="STOP"):
        decide_validation(settings, work, CH, "CONTINUE")
    with pytest.raises(ChapterError, match="STOP"):
        decide_validation(settings, work, CH, "OVERRIDE", "どうしても進めたい")
    with pytest.raises(ChapterError):
        skip_validation(settings, work, CH, "省略したい")

    assert head_commit(work.path, _branch(work.path)) == branch_head
    assert _saved(settings, work, rec)["state"] == "WAITING_HUMAN"


@pytest.mark.parametrize("action,reason,job_state", [
    ("REQUEST_FIX", "S1 の一人称を直す", "COMPLETED"),
    ("CANCEL", None, "CANCELLED"),
])
def test_validate_stop_allows_request_fix_and_cancel(tmp_path: Path, action: str, reason: str | None, job_state: str) -> None:
    settings, work, _ = drafted(tmp_path)
    run_validate_job(settings, work, CH, claude_runner=ok_claude(integrity("STOP")))
    rec = decide_validation(settings, work, CH, action, reason)
    assert rec["state"] == job_state
    assert _branch_yaml(work.path, "chapters/ch-001/chapter.yaml")["state"] == "DRAFTED"
    assert _branch_yaml(work.path, "chapters/ch-001/review.yaml")["decision"]["action"] == action


# 5. writing の指摘だけでは止めない（§11.2）
def test_validate_writing_findings_do_not_stop(tmp_path: Path) -> None:
    settings, work, _ = drafted(tmp_path)
    rec = run_validate_job(settings, work, CH, claude_runner=ok_claude(writ=writing(2)))
    assert rec["state"] == "COMPLETED"
    assert len(_branch_yaml(work.path, "chapters/ch-001/review.yaml")["writing"]["findings"]) == 2
    assert _branch_yaml(work.path, "chapters/ch-001/chapter.yaml")["state"] == "AI_VALIDATED"


# 6. schema 不適合 → 再要求で成功
def test_validate_schema_retry_success(tmp_path: Path) -> None:
    settings, work, _ = drafted(tmp_path)
    bad = integrity()
    del bad["checks"]["world"]
    fake = FakeClaude([envelope(bad), envelope(integrity())], [envelope(writing())])
    rec = run_validate_job(settings, work, CH, claude_runner=fake)
    assert rec["state"] == "COMPLETED"
    assert fake.kinds() == ["integrity_review", "integrity_review", "writing_review"]
    chk = _checks(rec)["claude_integrity_review"]
    assert chk["status"] == "PASS"
    assert chk["details"] and "schema" in chk["details"][0]


# 7. 再要求後も schema 不適合 → FAILED、作業ブランチは変えない
@pytest.mark.parametrize("kind", ["integrity_review", "writing_review"])
def test_validate_schema_exhausted_fails(tmp_path: Path, kind: str) -> None:
    settings, work, _ = drafted(tmp_path)
    repo = work.path
    branch_head = head_commit(repo, _branch(repo))
    bad = {"type": kind, "chapter_id": CH}
    if kind == "integrity_review":
        fake = FakeClaude([envelope(bad)] * 3)
    else:
        fake = FakeClaude([envelope(integrity())], [envelope(bad)] * 3)
    rec = run_validate_job(settings, work, CH, claude_runner=fake)
    assert rec["state"] == "FAILED"
    assert fake.kinds().count(kind) == 3
    chk = _checks(rec)[f"claude_{kind}"]
    assert chk["status"] == "FAIL"
    assert len(chk["details"]) == 3
    assert head_commit(repo, _branch(repo)) == branch_head
    assert not _branch_has(repo, "chapters/ch-001/review.yaml")
    assert _branch_yaml(repo, "chapters/ch-001/chapter.yaml")["state"] == "DRAFTED"


# 8. 意味検査の違反（全体の result が観点の result と合わない）→ 再要求 → FAILED
def test_validate_semantic_invalid_fails(tmp_path: Path) -> None:
    settings, work, _ = drafted(tmp_path)
    bad = integrity("WARNING")
    bad["result"] = "PASS"
    rec = run_validate_job(settings, work, CH, claude_runner=FakeClaude([envelope(bad)] * 3))
    assert rec["state"] == "FAILED"
    assert all("semantic" in d for d in _checks(rec)["claude_integrity_review"]["details"])
    assert not _branch_has(work.path, "chapters/ch-001/review.yaml")


# 対象章の不一致 → FAILED
def test_validate_review_chapter_mismatch_fails(tmp_path: Path) -> None:
    settings, work, _ = drafted(tmp_path)
    other = integrity()
    other["chapter_id"] = "ch-002"
    rec = run_validate_job(settings, work, CH, claude_runner=ok_claude(other))
    assert rec["state"] == "FAILED"
    assert _checks(rec)["review_refs"]["status"] == "FAIL"
    assert not _branch_has(work.path, "chapters/ch-001/review.yaml")


# Claude の timeout → FAILED
def test_validate_timeout_fails(tmp_path: Path) -> None:
    settings, work, _ = drafted(tmp_path)
    rec = run_validate_job(settings, work, CH, claude_runner=FakeClaude([b""], timed_out=True))
    assert rec["state"] == "FAILED"
    assert rec["history"][-1]["event"] == "TIMED_OUT"
    assert not _branch_has(work.path, "chapters/ch-001/review.yaml")


# 9. Context のハッシュ不一致 → FAILED（Claude を呼ばない）
def test_validate_context_hash_mismatch_fails(tmp_path: Path) -> None:
    settings, work, draft = drafted(tmp_path)
    wt = Path(draft["worktree"])
    plan_path = wt / "chapters" / CH / "plan.yaml"
    plan_path.write_text(plan_path.read_text(encoding="utf-8") + "# changed\n", encoding="utf-8")
    commit_all(wt, "tamper plan", [("NovUI-Job", draft["job_id"])], name="T", email="t@t")

    fake = ok_claude()
    rec = run_validate_job(settings, work, CH, claude_runner=fake)
    assert rec["state"] == "FAILED"
    assert fake.calls == []
    chk = _checks(rec)["draft_consistency"]
    assert chk["status"] == "FAIL"
    assert "context hash mismatch: chapters/ch-001/plan.yaml" in chk["details"]


def test_validate_dirty_worktree_fails(tmp_path: Path) -> None:
    settings, work, draft = drafted(tmp_path)
    (Path(draft["worktree"]) / "chapters" / CH / "draft.md").write_text("改変\n", encoding="utf-8")
    fake = ok_claude()
    rec = run_validate_job(settings, work, CH, claude_runner=fake)
    assert rec["state"] == "FAILED"
    assert fake.calls == []
    assert any("uncommitted" in d for d in _checks(rec)["draft_consistency"]["details"])


def test_validate_missing_draft_record_fails(tmp_path: Path) -> None:
    settings, work, draft = drafted(tmp_path)
    (jobs_dir(settings, work.work_key) / f"{draft['job_id']}.yaml").unlink()
    rec = run_validate_job(settings, work, CH, job_id="job-50", claude_runner=ok_claude())
    assert rec["state"] == "FAILED"
    assert _checks(rec)["draft_consistency"]["details"] == [f"draft job record not found: {draft['job_id']}"]


# 10. 作業ブランチがない
def test_validate_no_branch(tmp_path: Path) -> None:
    settings, work = setup_work(tmp_path)
    with pytest.raises(ChapterError, match="no job branch"):
        run_validate_job(settings, work, CH, claude_runner=ok_claude())


# 11. 作業ブランチが複数
def test_validate_multiple_branches(tmp_path: Path) -> None:
    settings, work, _ = drafted(tmp_path)
    run_git(work.path, "branch", "ai/ch-001/job-99", "main")
    with pytest.raises(ChapterError, match="multiple job branches"):
        run_validate_job(settings, work, CH, claude_runner=ok_claude())


# 12. 章の状態が DRAFTED でない（【要確認】で WAITING_HUMAN の draft）
def test_validate_requires_drafted(tmp_path: Path) -> None:
    settings, work = setup_work(tmp_path)
    rec = run_chapter_draft_job(
        settings, work, CH, container_runner=_agy_runner(GOOD_TEXT.replace("門を通った。", "【要確認：門の名前】門を通った。"))
    )
    assert rec["state"] == "WAITING_HUMAN"
    fake = ok_claude()
    with pytest.raises(ChapterError, match="must be DRAFTED"):
        run_validate_job(settings, work, CH, claude_runner=fake)
    assert fake.calls == []


# 13. CONTINUE
def test_decide_continue(tmp_path: Path) -> None:
    settings, work, _ = drafted(tmp_path)
    v = run_validate_job(settings, work, CH, claude_runner=ok_claude(integrity("WARNING")))
    main_before = _main_snapshot(work.path)
    rec = decide_validation(settings, work, CH, "CONTINUE")
    assert rec["job_id"] == v["job_id"]
    assert rec["state"] == "COMPLETED"
    assert rec["history"][-1]["event"] == "HUMAN_CONTINUE"
    review = _branch_yaml(work.path, "chapters/ch-001/review.yaml")
    validate_or_raise(review, "review")
    assert review["decision"]["action"] == "CONTINUE"
    assert review["decision"]["reason"] is None
    meta = _branch_yaml(work.path, "chapters/ch-001/chapter.yaml")
    assert meta["state"] == "AI_VALIDATED"
    assert meta["validation_skipped"] is False
    msg = run_git(work.path, "log", "-1", "--format=%B", _branch(work.path)).decode()
    assert parse_trailers(msg) == {"NovUI-Job": [v["job_id"]]}
    assert _main_snapshot(work.path) == main_before


# 14. OVERRIDE（理由が必須。理由は review.yaml と Job 記録に残る）
def test_decide_override(tmp_path: Path) -> None:
    settings, work, _ = drafted(tmp_path, WARN_TEXT)
    run_validate_job(settings, work, CH, claude_runner=ok_claude())
    with pytest.raises(ValueError, match="reason"):
        decide_validation(settings, work, CH, "OVERRIDE")
    with pytest.raises(ValueError, match="reason"):
        decide_validation(settings, work, CH, "OVERRIDE", "   ")
    rec = decide_validation(settings, work, CH, "OVERRIDE", "定型表現は意図的")
    assert rec["state"] == "COMPLETED"
    assert rec["history"][-1]["event"] == "HUMAN_OVERRIDE"
    assert rec["history"][-1]["reason"] == "定型表現は意図的"
    review = _branch_yaml(work.path, "chapters/ch-001/review.yaml")
    assert review["decision"]["action"] == "OVERRIDE"
    assert review["decision"]["reason"] == "定型表現は意図的"
    meta = _branch_yaml(work.path, "chapters/ch-001/chapter.yaml")
    assert meta["state"] == "AI_VALIDATED"
    assert meta["validation_skipped"] is False


# 15. REQUEST_FIX（理由が必須。修正 Job は作らない。章は DRAFTED のまま）
def test_decide_request_fix(tmp_path: Path) -> None:
    settings, work, _ = drafted(tmp_path)
    v = run_validate_job(settings, work, CH, claude_runner=ok_claude(integrity("WARNING")))
    with pytest.raises(ValueError, match="reason"):
        decide_validation(settings, work, CH, "REQUEST_FIX")
    jobs_before = sorted(p.name for p in jobs_dir(settings, work.work_key).glob("job-*.yaml"))
    rec = decide_validation(settings, work, CH, "REQUEST_FIX", "S1 の呼び方を直す")
    assert rec["state"] == "COMPLETED"
    assert rec["history"][-1]["event"] == "HUMAN_REQUEST_FIX"
    assert sorted(p.name for p in jobs_dir(settings, work.work_key).glob("job-*.yaml")) == jobs_before
    assert _branch_yaml(work.path, "chapters/ch-001/chapter.yaml")["state"] == "DRAFTED"
    assert _branch_yaml(work.path, "chapters/ch-001/review.yaml")["decision"]["reason"] == "S1 の呼び方を直す"
    assert rec["job_id"] == v["job_id"]


# 16. CANCEL
def test_decide_cancel(tmp_path: Path) -> None:
    settings, work, _ = drafted(tmp_path)
    run_validate_job(settings, work, CH, claude_runner=ok_claude(integrity("WARNING")))
    rec = decide_validation(settings, work, CH, "CANCEL")
    assert rec["state"] == "CANCELLED"
    assert rec["finished_at"] is not None
    assert _branch_yaml(work.path, "chapters/ch-001/chapter.yaml")["state"] == "DRAFTED"
    assert _branch_yaml(work.path, "chapters/ch-001/review.yaml")["decision"]["action"] == "CANCEL"


def test_decide_invalid_action(tmp_path: Path) -> None:
    settings, work, _ = drafted(tmp_path)
    run_validate_job(settings, work, CH, claude_runner=ok_claude(integrity("WARNING")))
    with pytest.raises(ValueError, match="Invalid action"):
        decide_validation(settings, work, CH, "SKIP", "x")


# 同じ Job の decision は1回だけ。再確定を拒否する
def test_decide_twice_rejected(tmp_path: Path) -> None:
    settings, work, _ = drafted(tmp_path)
    run_validate_job(settings, work, CH, claude_runner=ok_claude(integrity("WARNING")))
    decide_validation(settings, work, CH, "REQUEST_FIX", "直す")
    head = head_commit(work.path, _branch(work.path))
    with pytest.raises(ChapterError, match="already decided"):
        decide_validation(settings, work, CH, "CONTINUE")
    assert head_commit(work.path, _branch(work.path)) == head


def test_decide_without_review(tmp_path: Path) -> None:
    settings, work, _ = drafted(tmp_path)
    with pytest.raises(ChapterError, match="review.yaml not found"):
        decide_validation(settings, work, CH, "CONTINUE")


# 判断待ちの review があるうちは新しい validate を受け付けない
def test_validate_rejected_while_waiting(tmp_path: Path) -> None:
    settings, work, _ = drafted(tmp_path)
    run_validate_job(settings, work, CH, claude_runner=ok_claude(integrity("WARNING")))
    with pytest.raises(ChapterError, match="waiting for a Human decision"):
        run_validate_job(settings, work, CH, claude_runner=ok_claude())


# decision 済みなら新しい validate Job が新しい review.yaml を書ける（前の結果は Git の履歴に残る）
def test_new_validate_after_request_fix(tmp_path: Path) -> None:
    settings, work, _ = drafted(tmp_path)
    v1 = run_validate_job(settings, work, CH, claude_runner=ok_claude(integrity("WARNING")))
    decide_validation(settings, work, CH, "REQUEST_FIX", "直す")
    decided_commit = head_commit(work.path, _branch(work.path))
    v2 = run_validate_job(settings, work, CH, claude_runner=ok_claude())
    assert v2["job_id"] != v1["job_id"]
    assert v2["state"] == "COMPLETED"
    review = _branch_yaml(work.path, "chapters/ch-001/review.yaml")
    assert review["job_id"] == v2["job_id"]
    assert review["decision"] is None
    old = loads_yaml(run_git(work.path, "show", f"{decided_commit}:chapters/ch-001/review.yaml").decode("utf-8"))
    assert old["job_id"] == v1["job_id"]
    assert old["decision"]["action"] == "REQUEST_FIX"


# 17. Claude を使えない（全試行が is_error: true）→ WAITING_HUMAN、省略で AI_VALIDATED（validation_skipped）
def test_validate_claude_unavailable_and_skip(tmp_path: Path) -> None:
    settings, work, _ = drafted(tmp_path)
    fake = FakeClaude([envelope(None, is_error=True)] * 3)
    rec = run_validate_job(settings, work, CH, claude_runner=fake)
    assert rec["state"] == "WAITING_HUMAN"
    assert fake.kinds() == ["integrity_review"] * 3
    assert rec["history"][-1]["reason"] == "claude_unavailable"
    assert _checks(rec)["claude_integrity_review"]["status"] == "WARNING"
    review = _branch_yaml(work.path, "chapters/ch-001/review.yaml")
    assert review["integrity"] is None and review["writing"] is None
    assert all(c["status"] == "PASS" for c in review["mechanical"])
    assert _branch_yaml(work.path, "chapters/ch-001/chapter.yaml")["state"] == "DRAFTED"

    with pytest.raises(ChapterError, match="not obtained"):
        decide_validation(settings, work, CH, "CONTINUE")
    with pytest.raises(ChapterError, match="not obtained"):
        decide_validation(settings, work, CH, "OVERRIDE", "x")
    with pytest.raises(ValueError, match="reason"):
        skip_validation(settings, work, CH, "")

    done = skip_validation(settings, work, CH, "Claude のレート制限")
    assert done["state"] == "COMPLETED"
    assert done["history"][-1]["event"] == "HUMAN_OVERRIDE"
    assert done["history"][-1]["reason"] == "validation skipped: Claude のレート制限"
    review = _branch_yaml(work.path, "chapters/ch-001/review.yaml")
    validate_or_raise(review, "review")
    assert review["decision"]["action"] == "OVERRIDE"
    assert review["decision"]["reason"] == "validation skipped: Claude のレート制限"
    assert review["integrity"] is None and review["writing"] is None
    meta = _branch_yaml(work.path, "chapters/ch-001/chapter.yaml")
    assert meta["state"] == "AI_VALIDATED"
    assert meta["validation_skipped"] is True


def test_validate_writing_unavailable(tmp_path: Path) -> None:
    settings, work, _ = drafted(tmp_path)
    fake = FakeClaude([envelope(integrity())], [envelope(None, is_error=True)] * 3)
    rec = run_validate_job(settings, work, CH, claude_runner=fake)
    assert rec["state"] == "WAITING_HUMAN"
    review = _branch_yaml(work.path, "chapters/ch-001/review.yaml")
    assert review["integrity"]["result"] == "PASS"
    assert review["writing"] is None
    assert skip_validation(settings, work, CH, "writing を省略")["state"] == "COMPLETED"
    assert _branch_yaml(work.path, "chapters/ch-001/chapter.yaml")["validation_skipped"] is True


# is_error と schema 不適合が混ざる場合は「使えない」と扱わない → FAILED
def test_validate_mixed_errors_fail(tmp_path: Path) -> None:
    settings, work, _ = drafted(tmp_path)
    fake = FakeClaude([envelope(None, is_error=True), envelope({"type": "integrity_review"}), envelope(None, is_error=True)])
    rec = run_validate_job(settings, work, CH, claude_runner=fake)
    assert rec["state"] == "FAILED"


# 省略は Claude のレビューを得られなかった場合だけ
def test_skip_rejected_when_review_obtained(tmp_path: Path) -> None:
    settings, work, _ = drafted(tmp_path)
    run_validate_job(settings, work, CH, claude_runner=ok_claude(integrity("WARNING")))
    with pytest.raises(ChapterError, match="§11.6"):
        skip_validation(settings, work, CH, "省略")


# 再試行は新しい validate Job で行う（CANCEL してから）
def test_unavailable_retry_with_new_job(tmp_path: Path) -> None:
    settings, work, _ = drafted(tmp_path)
    v1 = run_validate_job(settings, work, CH, claude_runner=FakeClaude([envelope(None, is_error=True)] * 3))
    # メッセージは CANCEL してから validate を再実行する手順を示す
    for action, reason in (("CONTINUE", None), ("OVERRIDE", "x")):
        with pytest.raises(ChapterError) as ei:
            decide_validation(settings, work, CH, action, reason)
        msg = str(ei.value)
        assert f"{action} is not allowed" in msg
        assert "first CANCEL this validate Job (decide --action CANCEL), then run validate again" in msg
        assert "skip-validation --reason" in msg
    # CANCEL せずに validate を再実行することはできず、メッセージは先に CANCEL するよう示す
    with pytest.raises(ChapterError) as ei:
        run_validate_job(settings, work, CH, claude_runner=ok_claude())
    assert "waiting for a Human decision" in str(ei.value)
    assert "first CANCEL that validate Job (decide --action CANCEL)" in str(ei.value)
    assert decide_validation(settings, work, CH, "CANCEL")["state"] == "CANCELLED"
    v2 = run_validate_job(settings, work, CH, claude_runner=ok_claude())
    assert v2["job_id"] != v1["job_id"]
    assert v2["state"] == "COMPLETED"
    meta = _branch_yaml(work.path, "chapters/ch-001/chapter.yaml")
    assert meta["state"] == "AI_VALIDATED"
    assert meta["validation_skipped"] is False


# 19・20. 許可範囲外（保護対象を含む）の変更があれば commit せず、書いたファイルを戻す
def test_write_and_commit_rejects_protected_change(tmp_path: Path) -> None:
    settings, work, draft = drafted(tmp_path)
    wt = Path(draft["worktree"])
    head = head_commit(wt, "HEAD")
    (wt / "characters" / "C001.yaml").write_text("id: C001\nname: 改変\n", encoding="utf-8")
    review = {
        "chapter_id": CH, "job_id": "job-9", "base_commit": draft["base_commit"],
        "mechanical": [], "integrity": None, "writing": None, "decision": None,
    }
    with pytest.raises(ChapterError, match="allowed paths"):
        _write_and_commit(settings, wt, CH, "job-9", "validate ch-001 by job-9", review, None)
    assert head_commit(wt, "HEAD") == head
    assert not (wt / "chapters" / CH / "review.yaml").exists()


def test_write_and_commit_uses_commit_guard(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings, work, draft = drafted(tmp_path)
    wt = Path(draft["worktree"])
    head = head_commit(wt, "HEAD")
    calls: list[Any] = []
    import novui.validatejob as vj
    real = vj.commit_all

    def spy(*args: Any, **kwargs: Any) -> Any:
        calls.append(args)
        return real(*args, **kwargs)

    monkeypatch.setattr(vj, "commit_all", spy)
    review = {
        "chapter_id": CH, "job_id": "job-9", "base_commit": draft["base_commit"],
        "mechanical": [], "integrity": None, "writing": None, "decision": None,
    }
    _write_and_commit(settings, wt, CH, "job-9", "validate ch-001 by job-9", review, None)
    assert len(calls) == 1
    assert calls[0][2] == [("NovUI-Job", "job-9")]
    assert head_commit(wt, "HEAD") != head


# 21. Claude の起動引数が run_claude_job と同じ
def test_claude_launch_matches_run_claude_job(tmp_path: Path) -> None:
    settings, work, _ = drafted(tmp_path)
    fake = ok_claude()
    rec = run_validate_job(settings, work, CH, claude_runner=fake)

    received: dict[str, Any] = {}

    def ref_runner(**kwargs: Any) -> ProcResult:
        received.update(kwargs)
        return ProcResult(0, 1.0, False, None, False, envelope(integrity()), b"")

    root = tmp_path / "ref"
    root.mkdir()
    (root / "a.md").write_text("x\n", encoding="utf-8")
    run_claude_job(
        settings,
        ClaudeJobRequest(
            work_key=work.work_key, job_id="job-90", job_type="validate", chapter_id=CH, root=root,
            context_paths=("a.md",), task_text="【作業】x", expected_type="integrity_review", model=settings.claude_model,
        ),
        claude_runner=ref_runner,
    )
    ours = fake.calls[0]
    assert set(ours) - {"kind"} == set(received)
    assert ours["json_schema"] == received["json_schema"]
    assert "$schema" not in json.loads(ours["json_schema"])
    assert ours["model"] == received["model"]
    assert ours["timeout_seconds"] == received["timeout_seconds"]
    assert ours["cwd"] == settings.data_dir / "claude-cwd" / rec["job_id"]
    assert received["cwd"] == settings.data_dir / "claude-cwd" / "job-90"
    assert ours["log_dir"] == jobs_dir(settings, work.work_key) / f"{rec['job_id']}-logs"
    assert fake.calls[1]["json_schema"] != ours["json_schema"]


# CLI
def test_cli_validate_decide_skip(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    settings, work, _ = drafted(tmp_path)
    code = cli.main(["validate", "--work", work.work_key, "--chapter", CH],
                    settings=settings, claude_runner=ok_claude(integrity("WARNING")))
    assert code == 3
    assert "WAITING_HUMAN" in capsys.readouterr().out
    assert cli.main(["decide", "--work", work.work_key, "--chapter", CH, "--action", "SKIP"], settings=settings) == 1
    assert cli.main(["decide", "--work", work.work_key, "--chapter", CH, "--action", "OVERRIDE"], settings=settings) == 1
    code = cli.main(["decide", "--work", work.work_key, "--chapter", CH, "--action", "CONTINUE"], settings=settings)
    assert code == 0
    assert "COMPLETED" in capsys.readouterr().out
    assert _branch_yaml(work.path, "chapters/ch-001/chapter.yaml")["state"] == "AI_VALIDATED"


def test_cli_skip_validation(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    settings, work, _ = drafted(tmp_path)
    code = cli.main(["validate", "--work", work.work_key, "--chapter", CH],
                    settings=settings, claude_runner=FakeClaude([envelope(None, is_error=True)] * 3))
    assert code == 3
    assert cli.main(["skip-validation", "--work", work.work_key, "--chapter", CH], settings=settings) == 1
    code = cli.main(["skip-validation", "--work", work.work_key, "--chapter", CH, "--reason", "障害"], settings=settings)
    assert code == 0
    assert _branch_yaml(work.path, "chapters/ch-001/chapter.yaml")["validation_skipped"] is True


def test_cli_validate_pass_exit_zero(tmp_path: Path) -> None:
    settings, work, _ = drafted(tmp_path)
    assert cli.main(["validate", "--work", work.work_key, "--chapter", CH], settings=settings, claude_runner=ok_claude()) == 0
