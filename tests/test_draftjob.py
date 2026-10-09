"""Tests for novui.draftjob module (fake container runner; no podman / agy)."""

import copy
from pathlib import Path
from typing import Any, Callable
import pytest

from novui.chapters import ChapterError, add_chapter, read_chapter_meta, transition_on_main
from novui.config import Settings
from novui.draftjob import (
    build_character_rules_text,
    build_draft_instruction,
    collect_draft_context,
    read_plan,
    run_chapter_draft_job,
)
from novui.gitinspect import run_git
from novui.jobrecord import load_job_record
from novui.jobrunner import jobs_dir
from novui.mechanical import load_mechanical_inputs
from novui.models import save_catalog, select_model
from novui.planjob import approve_plan
from novui.procrun import ProcResult
from novui.states import ChapterEvent
from novui.workinit import init_work
from novui.workrepo import commit_all, head_commit, parse_trailers
from novui.works import WorkInfo
from novui.yamlio import dumps_yaml, load_yaml, loads_yaml

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "valid"
MODEL = "gemini-3.8-flash-high"


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


def make_plan(chapter_id: str = "ch-001", **overrides: Any) -> dict[str, Any]:
    plan = copy.deepcopy(load_yaml(FIXTURES_DIR / "plan.yaml"))
    plan["chapter_id"] = chapter_id
    plan["target_chars"] = {"min": 10, "max": 200}
    plan["context"] = {"past_summaries": [], "past_drafts": [], "settings": ["world/setting.md"]}
    s1 = copy.deepcopy(plan["scenes"][0])
    s1.update({"id": "S1", "characters": ["C001"], "foreshadowing": ["F001"]})
    s2 = copy.deepcopy(plan["scenes"][0])
    s2.update({"id": "S2", "summary": "門を通る", "characters": ["C001"], "foreshadowing": []})
    plan["scenes"] = [s1, s2]
    plan.update(overrides)
    return plan


def setup_work(tmp_path: Path, plan: dict[str, Any] | None = None, *, approve: bool = True) -> tuple[Settings, WorkInfo]:
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

    add_chapter(settings, work, "ch-001", "港へ", "カイが港町に着く。")
    transition_on_main(
        settings, work, "ch-001", ChapterEvent.PLAN_WRITTEN,
        subject="plan ch-001 by job-1",
        trailers=[("NovUI-Job", "job-1")],
        extra_files={"chapters/ch-001/plan.yaml": dumps_yaml(plan or make_plan()).encode("utf-8")},
        last_job="job-1",
    )
    if approve:
        approve_plan(settings, work, "ch-001")

    save_catalog(settings, load_yaml(FIXTURES_DIR / "model_catalog.yaml"))
    select_model(settings, "draft", MODEL)
    return settings, work


class FakeRunner:
    def __init__(self, stdout: str, *, exit_code: int = 0) -> None:
        self.stdout = stdout
        self.exit_code = exit_code
        self.calls: list[dict[str, Any]] = []

    def __call__(self, **kwargs: Any) -> ProcResult:
        self.calls.append(kwargs)
        return ProcResult(
            exit_code=self.exit_code,
            elapsed_seconds=1.0,
            timed_out=False,
            signal_sent=None,
            group_remaining=False,
            stdout=self.stdout.encode("utf-8"),
            stderr=b"",
        )

    @property
    def prompt(self) -> str:
        cmd = self.calls[-1]["command"]
        print_args = [a for a in cmd if a.startswith("--print=")]
        assert len(print_args) == 1
        return print_args[0][len("--print="):]


GOOD_TEXT = (
    "<!-- scene: S1 -->\n"
    "カイは港町ミナトに着いた。門番が紋章に目を留めた。\n"
    "\n"
    "<!-- scene: S2 -->\n"
    "カイは理由が分からないまま門を通った。\n"
)


def _checks(record: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {c["name"]: c for c in record["checks"]}


def _branch_file(repo: Path, branch: str, rel: str) -> str:
    return run_git(repo, "show", f"{branch}:{rel}").decode("utf-8")


# 1. 正常系 / 8. chapter.yaml の DRAFTED 遷移
def test_draft_job_success(tmp_path: Path) -> None:
    settings, work = setup_work(tmp_path)
    repo = work.path
    main_before = head_commit(repo, "main")
    runner = FakeRunner(GOOD_TEXT)

    record = run_chapter_draft_job(settings, work, "ch-001", container_runner=runner)

    assert record["state"] == "COMPLETED"
    assert record["job_type"] == "draft"
    assert record["cli"]["model"] == MODEL
    checks = _checks(record)
    for name in ("cli_output", "agy_output", "allowed_paths", "char_count",
                 "scene_markers", "ref_ids", "prohibited_phrases", "forbidden_words"):
        assert checks[name]["status"] == "PASS", (name, checks[name])
    assert "mechanical_inputs" not in checks

    # main は変わらない（章状態は main への merge で確定する。§7.1）
    assert head_commit(repo, "main") == main_before
    assert read_chapter_meta(repo, "ch-001")["state"] == "PLAN_APPROVED"

    branch = record["branch"]
    assert branch == f"ai/ch-001/{record['job_id']}"
    assert _branch_file(repo, branch, "chapters/ch-001/draft.md") == GOOD_TEXT
    meta = load_yaml(Path(record["worktree"]) / "chapters" / "ch-001" / "chapter.yaml")
    assert meta["state"] == "DRAFTED"
    assert meta["last_job"] == record["job_id"]

    # 作業ブランチ上の commit：本文と章状態の2つ。どちらも NovUI-Job
    log = run_git(repo, "log", "--format=%B%x00", f"main..{branch}").decode("utf-8")
    messages = [m.strip() for m in log.split("\0") if m.strip()]
    assert len(messages) == 2
    assert messages[0].startswith(f"chapter ch-001 DRAFTED by {record['job_id']}")
    assert messages[1].startswith(f"draft ch-001 by {record['job_id']}")
    for m in messages:
        assert parse_trailers(m)["NovUI-Job"] == [record["job_id"]]
    changed = run_git(repo, "diff", "--name-only", f"main..{branch}").decode("utf-8").split()
    assert sorted(changed) == ["chapters/ch-001/chapter.yaml", "chapters/ch-001/draft.md"]

    # Context と プロンプト
    ctx_paths = [c["path"] for c in record["context"]]
    assert ctx_paths == [
        "world/setting.md",
        "characters/C001.yaml",
        "foreshadowing/registry.yaml",
        "chapters/ch-001/outline.md",
        "chapters/ch-001/plan.yaml",
    ]
    prompt = runner.prompt
    assert "【ファイル：chapters/ch-001/plan.yaml】" in prompt
    assert "【指示】\n章 ch-001 の本文を書いてください。" in prompt
    assert "<!-- scene: S1 -->\n<!-- scene: S2 -->" in prompt
    assert "10 字以上 200 字以下" in prompt

    # Job 記録がファイルにも残っている
    saved = load_job_record(jobs_dir(settings, work.work_key) / f"{record['job_id']}.yaml")
    assert saved["state"] == "COMPLETED"
    assert [c["name"] for c in saved["checks"]] == [c["name"] for c in record["checks"]]


def _run_with_text(tmp_path: Path, text: str, plan: dict[str, Any] | None = None) -> tuple[dict[str, Any], WorkInfo]:
    settings, work = setup_work(tmp_path, plan)
    record = run_chapter_draft_job(settings, work, "ch-001", container_runner=FakeRunner(text))
    return record, work


def _assert_drafted_with_warning(record: dict[str, Any], name: str, details: list[str]) -> None:
    # §11.3：機械検査は WARNING。Job は止めず、章は DRAFTED になる
    assert record["state"] == "COMPLETED"
    chk = _checks(record)[name]
    assert chk["status"] == "WARNING"
    assert chk["details"] == details
    meta = load_yaml(Path(record["worktree"]) / "chapters" / "ch-001" / "chapter.yaml")
    assert meta["state"] == "DRAFTED"


# 2. scene marker の欠落
def test_draft_missing_scene_marker(tmp_path: Path) -> None:
    text = "<!-- scene: S1 -->\nカイは港町に着いた。門番が紋章を見た。\n"
    record, _ = _run_with_text(tmp_path, text)
    _assert_drafted_with_warning(record, "scene_markers", ["missing scene S2"])


# 3. scene marker の重複
def test_draft_duplicate_scene_marker(tmp_path: Path) -> None:
    text = GOOD_TEXT + "<!-- scene: S2 -->\nもう一度門を通った。\n"
    record, _ = _run_with_text(tmp_path, text)
    _assert_drafted_with_warning(record, "scene_markers", ["duplicate scene S2 at lines 4, 6"])


# 4. scene marker の順序違反
def test_draft_scene_order(tmp_path: Path) -> None:
    text = (
        "<!-- scene: S2 -->\nカイは理由が分からないまま門を通った。\n"
        "<!-- scene: S1 -->\nカイは港町ミナトに着いた。\n"
    )
    record, _ = _run_with_text(tmp_path, text)
    _assert_drafted_with_warning(record, "scene_markers", ["scene order mismatch: expected S1, S2; got S2, S1"])


# 5. plan に存在しない scene
def test_draft_scene_not_in_plan(tmp_path: Path) -> None:
    text = GOOD_TEXT + "<!-- scene: S3 -->\n予定にない場面。\n"
    record, _ = _run_with_text(tmp_path, text)
    _assert_drafted_with_warning(record, "scene_markers", ["scene S3 at line 6 is not in plan"])


# 6. 存在しない ID（C999 / F999）
def test_draft_unknown_ids(tmp_path: Path) -> None:
    plan = make_plan()
    plan["scenes"][1]["characters"] = ["C001", "C999"]
    plan["scenes"][1]["foreshadowing"] = ["F999"]
    record, _ = _run_with_text(tmp_path, GOOD_TEXT, plan)
    _assert_drafted_with_warning(record, "ref_ids", [
        "character C999 not found in characters/",
        "foreshadowing F999 not found in foreshadowing/registry.yaml",
    ])
    # 存在しない人物のファイルは Context に入れない
    assert "characters/C999.yaml" not in [c["path"] for c in record["context"]]


def test_draft_prohibited_and_forbidden(tmp_path: Path) -> None:
    text = GOOD_TEXT.replace("門番が紋章に目を留めた。", "僕はまるで嘘のように静かな門を見た。")
    record, _ = _run_with_text(tmp_path, text)
    checks = _checks(record)
    assert checks["prohibited_phrases"]["status"] == "WARNING"
    assert checks["prohibited_phrases"]["details"] == ["'まるで嘘のよう': 1", "'僕': 1"]
    assert checks["forbidden_words"]["status"] == "WARNING"
    assert checks["forbidden_words"]["details"] == ["C001 speech.forbidden '僕': 1"]
    assert record["state"] == "COMPLETED"


# 7. 【要確認】を含む本文
def test_draft_undefined_setting_waits_human(tmp_path: Path) -> None:
    text = GOOD_TEXT.replace("門番が紋章に目を留めた。", "門番の名は【要確認：門番の名前】だった。")
    record, work = _run_with_text(tmp_path, text)
    repo = work.path

    assert record["state"] == "WAITING_HUMAN"
    assert record["history"][-1]["reason"] == "undefined_settings: 1"
    branch = record["branch"]
    reqs = load_yaml(Path(record["worktree"]) / "chapters" / "ch-001" / "requests.yaml")
    assert reqs == [{
        "type": "request", "job_id": record["job_id"], "chapter_id": "ch-001",
        "kind": "undefined_setting", "target": None, "message": "門番の名前",
    }]
    # 不明な設定が残っている間は DRAFTED にしない
    assert load_yaml(Path(record["worktree"]) / "chapters" / "ch-001" / "chapter.yaml")["state"] == "PLAN_APPROVED"
    changed = run_git(repo, "diff", "--name-only", f"main..{branch}").decode("utf-8").split()
    assert sorted(changed) == ["chapters/ch-001/draft.md", "chapters/ch-001/requests.yaml"]


def test_draft_empty_output_fails(tmp_path: Path) -> None:
    record, _ = _run_with_text(tmp_path, "   \n")
    assert record["state"] == "FAILED"
    assert _checks(record)["agy_output"]["status"] == "FAIL"
    # FAILED の作業ブランチは残す（§7.2）。chapter.yaml は変えない
    assert load_yaml(Path(record["worktree"]) / "chapters" / "ch-001" / "chapter.yaml")["state"] == "PLAN_APPROVED"


def test_draft_requires_plan_approved(tmp_path: Path) -> None:
    settings, work = setup_work(tmp_path, approve=False)
    runner = FakeRunner(GOOD_TEXT)
    with pytest.raises(ChapterError, match="PLAN_APPROVED"):
        run_chapter_draft_job(settings, work, "ch-001", container_runner=runner)
    assert runner.calls == []


def test_draft_rejected_when_branch_exists(tmp_path: Path) -> None:
    settings, work = setup_work(tmp_path)
    first = run_chapter_draft_job(settings, work, "ch-001", container_runner=FakeRunner(GOOD_TEXT))
    assert first["state"] == "COMPLETED"
    runner = FakeRunner(GOOD_TEXT)
    with pytest.raises(ChapterError, match="active worktree"):
        run_chapter_draft_job(settings, work, "ch-001", container_runner=runner)
    assert runner.calls == []


def test_draft_model_unavailable_waits_human(tmp_path: Path) -> None:
    settings, work = setup_work(tmp_path)
    (settings.data_dir / "settings.yaml").unlink()
    runner = FakeRunner(GOOD_TEXT)
    record = run_chapter_draft_job(settings, work, "ch-001", container_runner=runner)
    assert record["state"] == "WAITING_HUMAN"
    assert record["history"][-1]["reason"].startswith("model_unavailable:")
    assert runner.calls == []
    assert run_git(work.path, "branch", "--list", "ai/*").strip() == b""


def test_draft_missing_context_file(tmp_path: Path) -> None:
    plan = make_plan()
    plan["context"]["settings"] = ["world/setting.md", "world/missing.md"]
    settings, work = setup_work(tmp_path, plan)
    runner = FakeRunner(GOOD_TEXT)
    with pytest.raises(ChapterError, match="world/missing.md"):
        run_chapter_draft_job(settings, work, "ch-001", container_runner=runner)
    assert runner.calls == []
    assert run_git(work.path, "branch", "--list", "ai/*").strip() == b""
    assert not jobs_dir(settings, work.work_key).exists() or not list(jobs_dir(settings, work.work_key).glob("job-*.yaml"))


def test_collect_draft_context_order(tmp_path: Path) -> None:
    settings, work = setup_work(tmp_path)
    repo = work.path
    # 過去章を2つ置く（chapters-order：ch-001 の前に ch-010、ch-011 を挿入した状態を作る）
    order = ["ch-010", "ch-011", "ch-001"]
    (repo / "chapters-order.yaml").write_text(dumps_yaml(order), encoding="utf-8")
    for ch in ("ch-010", "ch-011"):
        d = repo / "chapters" / ch
        d.mkdir(parents=True)
        (d / "summary.yaml").write_text("x: 1\n", encoding="utf-8")
        (d / "draft.md").write_text("本文\n", encoding="utf-8")
    plan = make_plan(context={
        "past_summaries": ["ch-010", "ch-011"],
        "past_drafts": ["ch-010"],
        "settings": ["world/setting.md", "characters/C001.yaml"],
    })
    paths = collect_draft_context(repo, "ch-001", plan)
    assert paths == [
        "chapters/ch-011/summary.yaml",   # 直前章
        "chapters/ch-010/summary.yaml",
        "chapters/ch-010/draft.md",
        "world/setting.md",
        "characters/C001.yaml",
        "foreshadowing/registry.yaml",
        "chapters/ch-001/outline.md",
        "chapters/ch-001/plan.yaml",
    ]

    future = make_plan(context={"past_summaries": ["ch-099"], "past_drafts": [], "settings": []})
    with pytest.raises(ChapterError, match="not before"):
        collect_draft_context(repo, "ch-001", future)


def test_build_draft_instruction() -> None:
    text = build_draft_instruction("ch-001", make_plan(), character_rules="なし")
    assert text.startswith("章 ch-001 の本文を書いてください。")
    assert "S1、S2 の 2 場面" in text
    assert "<!-- scene: S1 -->\n<!-- scene: S2 -->" in text
    assert "10 字以上 200 字以下" in text
    assert "{{" not in text
    assert not text.endswith("\n")


def test_read_plan_mismatch(tmp_path: Path) -> None:
    settings, work = setup_work(tmp_path)
    assert read_plan(work.path, "ch-001")["chapter_id"] == "ch-001"
    with pytest.raises(ChapterError, match="plan.yaml not found"):
        read_plan(work.path, "ch-002")


# 2-D2：規則表（phase2d-design §3、決定 3）

C001_WITH_CHANGE = {
    "id": "C001", "name": "カイ", "speech": {"first_person": "俺"},
    "address": {"default": "お前", "C002": {"default": "美咲", "changes": [
        {"value": "君", "from": {"chapter": "ch-001", "scene": "S2"}, "reason": "関係性の変化"},
    ]}},
}


def _rules_block(prompt: str) -> Any:
    head = "「場面ごとの人物の規則」"
    assert head in prompt
    body = prompt.split(head, 1)[1].split("```yaml\n", 1)[1].split("\n```", 1)[0]
    return loads_yaml(body)


def test_draft_instruction_has_rule_tables_per_scene(tmp_path: Path) -> None:
    settings, work = setup_work(tmp_path)
    repo = work.path
    (repo / "characters" / "C001.yaml").write_text(dumps_yaml(C001_WITH_CHANGE), encoding="utf-8")
    commit_all(repo, "edit C001", [("NovUI-Edit", "human-content")], name="Tester", email="tester@test")
    runner = FakeRunner(GOOD_TEXT)

    record = run_chapter_draft_job(settings, work, "ch-001", container_runner=runner)

    assert record["state"] == "COMPLETED"
    tables = _rules_block(runner.prompt)
    assert [s["scene"] for s in tables] == ["S1", "S2"]
    assert tables[0]["characters"][0]["address"] == {"default": "お前", "C002": "美咲"}
    assert tables[1]["characters"][0]["address"] == {"default": "お前", "C002": "君"}


def test_character_rules_text_without_characters(tmp_path: Path) -> None:
    plan = make_plan()
    for scene in plan["scenes"]:
        scene["characters"] = []
    settings, work = setup_work(tmp_path, plan)
    repo = work.path
    assert build_character_rules_text(repo, "ch-001", plan, load_mechanical_inputs(repo)) == "なし"
    text = build_draft_instruction("ch-001", plan, character_rules="なし")
    assert "```yaml\nなし\n```" in text
