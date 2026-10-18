"""Tests for Phase 3-A2: Review required flag and final_approve subsequent chapters marking."""

from pathlib import Path
import pytest

from novui.chapters import (
    ChapterError,
    add_chapter,
    clear_review_required,
    mark_review_required,
    read_chapter_meta,
    read_chapters_order,
    review_marked,
    review_only_since,
    subsequent_chapters,
)
from novui.cli import main
from novui.config import Settings
from novui.finalize import final_approve
from novui.gitinspect import run_git
from novui.jobrecord import apply_transition, load_job_record, new_job_record, now_iso, save_job_record
from novui.jobrunner import jobs_dir
from novui.states import ChapterEvent, ChapterState, InvalidTransition, JobEvent, JobState
from novui.workinit import init_work
from novui.workrepo import (
    JobWorktree,
    commit_all,
    create_job_worktree,
    head_commit,
)
from novui.works import WorkInfo
from novui.yamlio import dumps_yaml, load_yaml, write_yaml_atomic


def _make_settings(tmp_path: Path) -> Settings:
    data_dir = tmp_path / "data"
    worktree_root = data_dir / "worktrees"
    jobhome_root = data_dir / "jobhomes"
    token_path = tmp_path / "fake-token"
    token_path.write_text("token", encoding="utf-8")
    return Settings(
        data_dir=data_dir,
        worktree_root=worktree_root,
        jobhome_root=jobhome_root,
        agy_image="test:img",
        agy_token_path=token_path,
        timeouts={"agy_draft": 300},
        git_name="Tester",
        git_email="tester@novui.local",
    )


def _setup_three_chapters(tmp_path: Path) -> tuple[Settings, WorkInfo]:
    settings = _make_settings(tmp_path)
    repo_path = tmp_path / "novel_repo"
    work = init_work(settings, repo_path, "test-novel", "テスト小説")
    add_chapter(settings, work, "ch-001", "第一章", "概要1")
    add_chapter(settings, work, "ch-002", "第二章", "概要2")
    add_chapter(settings, work, "ch-003", "第三章", "概要3")
    return settings, work


def _make_branch_for_chapter(
    settings: Settings,
    work: WorkInfo,
    chapter_id: str,
    job_id: str = "job-0001",
    *,
    chapter_state: str = "DRAFTED",
) -> JobWorktree:
    jw = create_job_worktree(work.path, settings.worktree_root, work.work_key, chapter_id, job_id)
    rec = new_job_record(job_id, "draft", chapter_id=chapter_id)
    rec["branch"] = jw.branch
    rec["worktree"] = str(jw.path)
    rec["base_commit"] = jw.base_commit
    save_job_record(jobs_dir(settings, work.work_key), rec)

    ch_yaml = jw.path / "chapters" / chapter_id / "chapter.yaml"
    meta = load_yaml(ch_yaml)
    meta["state"] = chapter_state
    meta["last_job"] = job_id
    write_yaml_atomic(ch_yaml, meta)
    commit_all(jw.path, f"init {chapter_id} on branch", [("NovUI-Job", job_id)], name="t", email="t@t")
    return jw


# =====================================================================
# review_marked & subsequent_chapters
# =====================================================================

def test_review_marked_pure() -> None:
    meta = {
        "id": "ch-001",
        "title": "第一章",
        "state": "PLAN_APPROVED",
        "review_required": False,
        "review_reasons": [],
        "validation_skipped": False,
        "last_job": None,
    }
    at = "2026-10-18T10:00:00+09:00"
    m1 = review_marked(meta, "setting_changed", "世界観の変更", at)
    assert m1["review_required"] is True
    assert len(m1["review_reasons"]) == 1
    assert m1["review_reasons"][0] == {"code": "setting_changed", "detail": "世界観の変更", "at": at}

    # 元の meta が変更されていないこと
    assert meta["review_required"] is False
    assert meta["review_reasons"] == []

    # 同じ理由を2回足しても1件
    at2 = "2026-10-18T11:00:00+09:00"
    m2 = review_marked(m1, "setting_changed", "世界観の変更", at2)
    assert len(m2["review_reasons"]) == 1
    assert m2["review_reasons"][0]["at"] == at  # at も更新されない

    # 異なる理由なら足される
    m3 = review_marked(m2, "previous_chapter_changed", "前章の変更", at2)
    assert len(m3["review_reasons"]) == 2


def test_subsequent_chapters() -> None:
    order = ["ch-001", "ch-002", "ch-003"]
    assert subsequent_chapters(order, "ch-001") == ["ch-002", "ch-003"]
    assert subsequent_chapters(order, "ch-002") == ["ch-003"]
    assert subsequent_chapters(order, "ch-003") == []
    with pytest.raises(ValueError, match="not found"):
        subsequent_chapters(order, "ch-999")


# =====================================================================
# mark_review_required
# =====================================================================

def test_mark_review_required_main_only(tmp_path: Path) -> None:
    settings, work = _setup_three_chapters(tmp_path)
    repo = work.path
    head_before = head_commit(repo, "main")

    # branch_only=True の場合、作業ブランチのない章（main）は変更されない
    commits_bo = mark_review_required(settings, work, ["ch-002", "ch-003"], "previous_chapter_changed", "前章変更", branch_only=True)
    assert commits_bo == []
    assert head_commit(repo, "main") == head_before

    # 通常呼び出し（branch_only=False）: main にまとめて1つの commit
    commits = mark_review_required(settings, work, ["ch-002", "ch-003"], "previous_chapter_changed", "前章変更")
    assert len(commits) == 1
    assert head_commit(repo, "main") == commits[0]

    # メッセージ確認
    msg = run_git(repo, "log", "-1", "--format=%B").decode("utf-8")
    assert "review required ch-002,ch-003 (previous_chapter_changed)" in msg

    # 各章の chapter.yaml を確認
    m2 = read_chapter_meta(repo, "ch-002")
    m3 = read_chapter_meta(repo, "ch-003")
    assert m2["review_required"] is True and m2["review_reasons"][0]["code"] == "previous_chapter_changed"
    assert m3["review_required"] is True and m3["review_reasons"][0]["code"] == "previous_chapter_changed"

    # ch-001 は変更なし
    m1 = read_chapter_meta(repo, "ch-001")
    assert m1["review_required"] is False


def test_mark_review_required_branch_only(tmp_path: Path) -> None:
    settings, work = _setup_three_chapters(tmp_path)
    repo = work.path
    jw2 = _make_branch_for_chapter(settings, work, "ch-002", "job-0002")
    head_main_before = head_commit(repo, "main")

    commits = mark_review_required(settings, work, ["ch-002"], "setting_changed", "設定変更")
    assert len(commits) == 1
    # main は変わっていない
    assert head_commit(repo, "main") == head_main_before

    # worktree の chapter.yaml が更新されている
    wt_meta = load_yaml(jw2.path / "chapters" / "ch-002" / "chapter.yaml")
    assert wt_meta["review_required"] is True
    assert wt_meta["review_reasons"][0]["code"] == "setting_changed"


def test_mark_review_required_mixed(tmp_path: Path) -> None:
    settings, work = _setup_three_chapters(tmp_path)
    repo = work.path
    # ch-002 は作業ブランチあり、ch-003 は main のみ
    jw2 = _make_branch_for_chapter(settings, work, "ch-002", "job-0002")

    commits = mark_review_required(settings, work, ["ch-002", "ch-003"], "setting_changed", "設定変更")
    assert len(commits) == 2  # 1つは ch-002 の worktree、もう1つは main

    # ch-002 の worktree
    wt2_meta = load_yaml(jw2.path / "chapters" / "ch-002" / "chapter.yaml")
    assert wt2_meta["review_required"] is True

    # ch-003 の main
    m3 = read_chapter_meta(repo, "ch-003")
    assert m3["review_required"] is True

    # main の ch-002 は変わっていない
    m2_main = read_chapter_meta(repo, "ch-002")
    assert m2_main["review_required"] is False


def test_mark_review_required_active_job_blocks(tmp_path: Path) -> None:
    settings, work = _setup_three_chapters(tmp_path)
    jw2 = _make_branch_for_chapter(settings, work, "ch-002", "job-0002")

    # job-0002 を RUNNING にする
    rec = load_job_record(jobs_dir(settings, work.work_key) / "job-0002.yaml")
    rec = apply_transition(rec, JobEvent.LOCK_ACQUIRED)
    save_job_record(jobs_dir(settings, work.work_key), rec)

    # 何も書かずに ChapterError
    with pytest.raises(ChapterError, match="Active job"):
        mark_review_required(settings, work, ["ch-002", "ch-003"], "setting_changed", "設定変更")

    # worktree も main も変更されていない
    wt2_meta = load_yaml(jw2.path / "chapters" / "ch-002" / "chapter.yaml")
    assert wt2_meta["review_required"] is False
    m3 = read_chapter_meta(work.path, "ch-003")
    assert m3["review_required"] is False


def test_mark_review_required_order_error(tmp_path: Path) -> None:
    settings, work = _setup_three_chapters(tmp_path)
    with pytest.raises(ChapterError, match="not found in chapters-order.yaml"):
        mark_review_required(settings, work, ["ch-002", "ch-999"], "setting_changed", "設定変更")


# =====================================================================
# clear_review_required
# =====================================================================

def test_clear_review_required_main(tmp_path: Path) -> None:
    settings, work = _setup_three_chapters(tmp_path)
    repo = work.path

    # 最初は False なので ChapterError
    with pytest.raises(ChapterError, match="does not have review_required set"):
        clear_review_required(settings, work, "ch-002")

    # mark してから clear
    mark_review_required(settings, work, ["ch-002"], "setting_changed", "設定変更")
    assert read_chapter_meta(repo, "ch-002")["review_required"] is True

    c_hash = clear_review_required(settings, work, "ch-002")
    assert c_hash == head_commit(repo, "main")
    meta = read_chapter_meta(repo, "ch-002")
    assert meta["review_required"] is False
    assert meta["review_reasons"] == []

    # メッセージ確認
    msg = run_git(repo, "log", "-1", "--format=%B").decode("utf-8")
    assert "review done ch-002" in msg


def test_clear_review_required_branch(tmp_path: Path) -> None:
    settings, work = _setup_three_chapters(tmp_path)
    jw2 = _make_branch_for_chapter(settings, work, "ch-002", "job-0002")

    # worktree で mark
    mark_review_required(settings, work, ["ch-002"], "setting_changed", "設定変更")
    assert load_yaml(jw2.path / "chapters" / "ch-002" / "chapter.yaml")["review_required"] is True

    # active job がある場合は ChapterError
    rec = load_job_record(jobs_dir(settings, work.work_key) / "job-0002.yaml")
    rec = apply_transition(rec, JobEvent.LOCK_ACQUIRED)
    save_job_record(jobs_dir(settings, work.work_key), rec)
    with pytest.raises(ChapterError, match="Active job"):
        clear_review_required(settings, work, "ch-002")

    # job を終了させてから clear
    rec = apply_transition(rec, JobEvent.HUMAN_STOP)
    save_job_record(jobs_dir(settings, work.work_key), rec)

    # clear
    c_hash = clear_review_required(settings, work, "ch-002")
    assert c_hash == head_commit(jw2.path, "HEAD")
    meta = load_yaml(jw2.path / "chapters" / "ch-002" / "chapter.yaml")
    assert meta["review_required"] is False
    assert meta["review_reasons"] == []

    # main は変わっていない
    assert read_chapter_meta(work.path, "ch-002")["review_required"] is False


# =====================================================================
# final_approve & subsequent review required
# =====================================================================

def test_final_approve_first_final_no_review_marked(tmp_path: Path) -> None:
    # (a) 初回の FINAL では後続に何も立たない
    settings, work = _setup_three_chapters(tmp_path)
    repo = work.path
    # ch-001 を作業ブランチで HUMAN_APPROVED にする
    jw1 = _make_branch_for_chapter(settings, work, "ch-001", "job-0001", chapter_state="HUMAN_APPROVED")

    commit = final_approve(settings, work, "ch-001")
    assert commit == head_commit(repo, "main")
    assert read_chapter_meta(repo, "ch-001")["state"] == "FINAL"

    # 後続章（ch-002, ch-003）には何も立たない
    assert read_chapter_meta(repo, "ch-002")["review_required"] is False
    assert read_chapter_meta(repo, "ch-003")["review_required"] is False


def test_final_approve_reopened_cycle_marks_subsequent(tmp_path: Path) -> None:
    # (b) FINAL の章を開き直したサイクルの最終承認で、
    # 作業ブランチの無い後続の章は merge commit の中で、
    # 作業ブランチのある後続の章はそのブランチで、要確認が立つ。
    # (c) 要確認の立った後続の章は、review-done までは final_approve できない（InvalidTransition）。
    # (b) の後、作業ブランチのある後続の章の最終承認が overlap で止まらないこと。
    settings, work = _setup_three_chapters(tmp_path)
    repo = work.path

    # まず ch-001 を最初の FINAL にする
    jw1_init = _make_branch_for_chapter(settings, work, "ch-001", "job-0001", chapter_state="HUMAN_APPROVED")
    final_approve(settings, work, "ch-001")
    assert read_chapter_meta(repo, "ch-001")["state"] == "FINAL"

    # 後続章の設定：
    # ch-002 は作業ブランチ無し（main のみ）
    # ch-003 は作業ブランチあり（job-0003）
    jw3 = _make_branch_for_chapter(settings, work, "ch-003", "job-0003", chapter_state="HUMAN_APPROVED")

    # ch-001 を開き直したサイクル（作業ブランチ job-0010）を作成し、HUMAN_APPROVED にする
    # draft.md を少し変更
    jw1_reopen = _make_branch_for_chapter(settings, work, "ch-001", "job-0010", chapter_state="HUMAN_APPROVED")
    (jw1_reopen.path / "chapters" / "ch-001" / "draft.md").write_text("改訂本文\n", encoding="utf-8")
    commit_all(jw1_reopen.path, "rewrite ch-001", [("NovUI-Job", "job-0010")], name="t", email="t@t")

    # final_approve 実行！
    merge_commit = final_approve(settings, work, "ch-001")
    assert merge_commit == head_commit(repo, "main")
    assert read_chapter_meta(repo, "ch-001")["state"] == "FINAL"

    # (b) の検証：
    # 1. 作業ブランチの無い ch-002: merge commit の中で要確認が立つ
    m2 = read_chapter_meta(repo, "ch-002")
    assert m2["review_required"] is True
    assert len(m2["review_reasons"]) == 1
    assert m2["review_reasons"][0]["code"] == "previous_chapter_changed"
    assert "ch-001 was changed and final-approved again" in m2["review_reasons"][0]["detail"]

    # 2. 作業ブランチのある ch-003: その作業ブランチで要確認が立つ
    wt3_meta = load_yaml(jw3.path / "chapters" / "ch-003" / "chapter.yaml")
    assert wt3_meta["review_required"] is True
    assert len(wt3_meta["review_reasons"]) == 1
    assert wt3_meta["review_reasons"][0]["code"] == "previous_chapter_changed"

    # main の ch-003 はまだ立っていない（作業ブランチのみにコミットされたため）
    m3_main = read_chapter_meta(repo, "ch-003")
    assert m3_main["review_required"] is False

    # (c) の検証：
    # 要確認の立った後続章 ch-003 は review-done までは final_approve できない（InvalidTransition）
    with pytest.raises(InvalidTransition, match="Cannot transition to FINAL when review_required is True"):
        final_approve(settings, work, "ch-003")

    # review-done を実行
    clear_review_required(settings, work, "ch-003")
    wt3_meta_cleared = load_yaml(jw3.path / "chapters" / "ch-003" / "chapter.yaml")
    assert wt3_meta_cleared["review_required"] is False

    # その後、ch-003 の最終承認が overlap で止まらずに成功すること！
    commit3 = final_approve(settings, work, "ch-003")
    assert commit3 == head_commit(repo, "main")
    assert read_chapter_meta(repo, "ch-003")["state"] == "FINAL"


# =====================================================================
# CLI
# =====================================================================

def test_cli_review_done_and_show(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    settings, work = _setup_three_chapters(tmp_path)
    repo = work.path

    # ch-002 に要確認を立てる
    mark_review_required(settings, work, ["ch-002"], "setting_changed", "設定の更新")

    # show コマンドの出力確認
    capsys.readouterr()
    rc = main(["show", "--work", work.work_key], settings=settings)
    assert rc == 0
    out = capsys.readouterr().out
    assert "ch-002: 第二章 [OUTLINED] (review required)" in out
    assert "- setting_changed: 設定の更新 (" in out
    assert "ch-001: 第一章 [OUTLINED]" in out
    assert "(review required)" not in out.splitlines()[0]  # ch-001 has no flag

    # review-done コマンドの実行
    rc_done = main(["review-done", "--work", work.work_key, "--chapter", "ch-002"], settings=settings)
    assert rc_done == 0
    out_done = capsys.readouterr().out
    assert "Review required cleared for chapter ch-002" in out_done

    # 再度 show で要確認が消えていることを確認
    rc_show2 = main(["show", "--work", work.work_key], settings=settings)
    assert rc_show2 == 0
    out_show2 = capsys.readouterr().out
    assert "(review required)" not in out_show2


# =====================================================================
# review_only_since & approve_state 連携
# =====================================================================

def test_review_only_since_unit(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    run_git(repo, "init", "-b", "main")
    ch_yaml = repo / "chapters" / "ch-001" / "chapter.yaml"
    ch_yaml.parent.mkdir(parents=True)
    meta = {
        "id": "ch-001",
        "title": "第一章",
        "state": "AI_VALIDATED",
        "review_required": False,
        "review_reasons": [],
        "validation_skipped": False,
        "last_job": "job-1",
    }
    write_yaml_atomic(ch_yaml, meta)
    run_git(repo, "add", ".")
    run_git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-m", "init")
    base = head_commit(repo, "HEAD")

    # base == head
    assert review_only_since(repo, base, base, "ch-001") is True

    # 1件目：要確認の commit (review_required を True に)
    meta["review_required"] = True
    meta["review_reasons"] = [{"code": "setting_changed", "detail": "d", "at": "2026-10-18T10:00:00+09:00"}]
    write_yaml_atomic(ch_yaml, meta)
    run_git(repo, "add", ".")
    run_git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-m", "mark 1")
    c1 = head_commit(repo, "HEAD")
    assert review_only_since(repo, base, c1, "ch-001") is True

    # 2件目：要確認の commit (review_required を False に)
    meta["review_required"] = False
    meta["review_reasons"] = []
    write_yaml_atomic(ch_yaml, meta)
    run_git(repo, "add", ".")
    run_git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-m", "clear 1")
    c2 = head_commit(repo, "HEAD")
    assert review_only_since(repo, base, c2, "ch-001") is True

    # state も変えた commit -> False
    meta["state"] = "HUMAN_APPROVED"
    write_yaml_atomic(ch_yaml, meta)
    run_git(repo, "add", ".")
    run_git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-m", "state change")
    c_state = head_commit(repo, "HEAD")
    assert review_only_since(repo, base, c_state, "ch-001") is False

    # 他のファイルを変えた commit -> False
    (repo / "other.txt").write_text("hello", encoding="utf-8")
    run_git(repo, "add", ".")
    run_git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-m", "other file")
    c_other = head_commit(repo, "HEAD")
    assert review_only_since(repo, base, c_other, "ch-001") is False

    # base が祖先でない場合 -> False
    run_git(repo, "checkout", "-b", "other_branch", base)
    (repo / "new.txt").write_text("branch", encoding="utf-8")
    run_git(repo, "add", ".")
    run_git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-m", "branch commit")
    c_branch = head_commit(repo, "HEAD")
    assert review_only_since(repo, c_branch, c1, "ch-001") is False


def test_approve_state_with_review_required(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("novui.jobrunner.image_label", lambda img, key: "1.2.14")
    monkeypatch.setattr(
        "novui.jobrunner.image_id",
        lambda img: "sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
    )
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from test_stateupdate import _proposed, CH
    from novui.stateupdate import approve_state
    from novui.validatejob import _read_branch_chapter_meta, find_chapter_branch

    # 1. mark_review_required の後
    case1 = tmp_path / "case1"
    case1.mkdir(parents=True, exist_ok=True)
    settings, work, rec = _proposed(case1)
    mark_review_required(settings, work, [CH], "previous_chapter_changed", "前章の変更")
    done = approve_state(settings, work, CH)
    assert done["state"] == "COMPLETED"
    branch = find_chapter_branch(work.path, CH)
    b_meta = _read_branch_chapter_meta(work.path, branch, CH)
    assert b_meta["state"] == "HUMAN_APPROVED"

    # 2. clear_review_required の後
    case2 = tmp_path / "case2"
    case2.mkdir(parents=True, exist_ok=True)
    settings2, work2, rec2 = _proposed(case2)
    mark_review_required(settings2, work2, [CH], "previous_chapter_changed", "前章の変更")
    clear_review_required(settings2, work2, CH)
    done2 = approve_state(settings2, work2, CH)
    assert done2["state"] == "COMPLETED"
    branch2 = find_chapter_branch(work2.path, CH)
    b_meta2 = _read_branch_chapter_meta(work2.path, branch2, CH)
    assert b_meta2["state"] == "HUMAN_APPROVED"
