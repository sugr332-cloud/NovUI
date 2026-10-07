"""CLI tests for state-update, approve-state, reject-state, final-approve, resolve-request, redraft, discard, show."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_finalize import approved, main_edit, marker_draft  # noqa: E402
from test_requestflow import CaptureAgy  # noqa: E402
from test_stateupdate import CH, CHAR, FakeSU, good_su, validated  # noqa: E402
from test_validatejob import GOOD_TEXT, envelope  # noqa: E402

from novui.cli import main
from novui.gitinspect import run_git
from novui.workrepo import chapter_branches, head_commit
from novui.yamlio import dumps_yaml

KEY = "test-novel"


@pytest.fixture(autouse=True)
def mock_container_meta(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("novui.jobrunner.image_label", lambda img, key: "1.2.14")
    monkeypatch.setattr(
        "novui.jobrunner.image_id",
        lambda img: "sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
    )


def _args(command: str, *extra: str) -> list[str]:
    return [command, "--work", KEY, "--chapter", CH, *extra]


def test_state_update_approve_final_flow(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    settings, work, draft = validated(tmp_path)
    repo = work.path
    capsys.readouterr()

    # state-update: the normal end is WAITING_HUMAN (awaiting approval), exit code 3
    rc = main(_args("state-update"), settings=settings, claude_runner=good_su())
    out = capsys.readouterr().out
    assert rc == 3
    assert "finished with state: WAITING_HUMAN" in out
    assert "Reason: awaiting approval of state update proposal" in out
    assert "[pending]" in out and f"Patch: {CHAR} (3 operations)" in out
    assert "Patch: foreshadowing/registry.yaml (3 operations)" in out
    assert "- C001: knowledge +1, relationship changes 1" in out
    assert "- foreshadowing F001: active" in out
    assert "Awaiting approval: run approve-state or reject-state." in out

    # a second proposal needs --replace (ordinary error: exit code 1)
    rc = main(_args("state-update"), settings=settings, claude_runner=good_su())
    assert rc == 1
    assert "pending" in capsys.readouterr().err

    # show: the job branch, the chapter state on it and the pending proposal
    assert main(_args("show"), settings=settings) == 0
    out = capsys.readouterr().out
    assert f"Job branch: {draft['branch']} (chapter state on the branch: AI_VALIDATED)" in out
    assert "[pending]" in out and f"Patch: {CHAR} (3 operations)" in out

    # reject-state needs a reason (argument error: exit code 1) and then works
    assert main(["reject-state", "--work", KEY, "--chapter", CH], settings=settings) == 1
    capsys.readouterr()
    assert main(_args("reject-state", "--reason", "やり直し"), settings=settings) == 0
    out = capsys.readouterr().out
    assert "finished with state: CANCELLED" in out and "rejected" in out
    assert main(_args("approve-state"), settings=settings) == 1  # nothing pending any more
    assert "no pending" in capsys.readouterr().err

    # --replace is accepted and supersedes (here nothing is pending, so it behaves as a normal run)
    assert main(_args("state-update", "--replace"), settings=settings, claude_runner=good_su()) == 3
    capsys.readouterr()

    # approve-state
    rc = main(_args("approve-state"), settings=settings)
    out = capsys.readouterr().out
    assert rc == 0
    assert "finished with state: COMPLETED" in out and "Approval-Id: A-0001" in out
    branch_head = head_commit(repo, draft["branch"])
    assert f"Chapter {CH} is HUMAN_APPROVED (commit {branch_head})" in out

    assert main(_args("show"), settings=settings) == 0
    out = capsys.readouterr().out
    assert "chapter state on the branch: HUMAN_APPROVED" in out and "[approved]" in out

    # final-approve
    rc = main(_args("final-approve"), settings=settings)
    out = capsys.readouterr().out
    assert rc == 0
    assert f"Chapter {CH} is FINAL (merge commit {head_commit(repo, 'main')})" in out
    assert chapter_branches(repo, CH) == []

    assert main(_args("show"), settings=settings) == 0
    out = capsys.readouterr().out
    assert "state: FINAL" in out and "Job branch:" not in out


def test_state_update_failed_exit_code(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    settings, work, draft = validated(tmp_path)
    capsys.readouterr()
    bad = {"type": "summary", "chapter_id": CH}
    rc = main(_args("state-update"), settings=settings, claude_runner=FakeSU({"summary": [envelope(bad)] * 3}))
    out = capsys.readouterr().out
    assert rc == 2
    assert "finished with state: FAILED" in out and "Check claude_summary: FAIL" in out
    assert "Awaiting approval" not in out


def test_approve_without_patches_and_final(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    settings, work, ctx = approved(tmp_path, patches=False)
    capsys.readouterr()
    assert main(_args("show"), settings=settings) == 0
    assert "[approved]" in capsys.readouterr().out
    assert main(_args("final-approve"), settings=settings) == 0
    assert "is FINAL" in capsys.readouterr().out


def test_final_approve_waiting_exit_code_and_discard(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    settings, work, ctx = approved(tmp_path)
    repo = work.path
    main_edit(work, {CHAR: dumps_yaml({"id": "C001", "name": "カイ（改）", "speech": {"first_person": "俺"}})})
    head = head_commit(repo, "main")
    capsys.readouterr()

    # overlap: waiting for a Human decision (exit code 3), main untouched
    rc = main(_args("final-approve"), settings=settings)
    out = capsys.readouterr().out
    assert rc == 3
    assert "waiting for a Human decision" in out and f"  - {CHAR}" in out
    assert "main was not changed" in out
    assert head_commit(repo, "main") == head
    assert run_git(repo, "status", "--porcelain").decode() == ""

    # a HUMAN_APPROVED job branch needs --force to be discarded (ordinary error: exit code 1)
    rc = main(_args("discard"), settings=settings)
    assert rc == 1 and "force" in capsys.readouterr().err
    assert len(chapter_branches(repo, CH)) == 1

    assert main(_args("discard", "--force"), settings=settings) == 0
    assert f"Job branch of chapter {CH} discarded" in capsys.readouterr().out
    assert chapter_branches(repo, CH) == []
    assert head_commit(repo, "main") == head

    # nothing to discard or approve any more: exit code 1
    assert main(_args("discard"), settings=settings) == 1
    assert main(_args("final-approve"), settings=settings) == 1
    capsys.readouterr()


def test_final_approve_requires_human_approved(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    settings, work, draft = validated(tmp_path)
    capsys.readouterr()
    rc = main(_args("final-approve"), settings=settings)
    assert rc == 1
    assert "HUMAN_APPROVED" in capsys.readouterr().err


def test_requests_resolve_redraft_and_show(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    settings, work, rec = marker_draft(tmp_path)
    assert rec["state"] == "WAITING_HUMAN"
    capsys.readouterr()

    assert main(_args("show"), settings=settings) == 0
    out = capsys.readouterr().out
    assert "Requests: 2 unresolved of 2" in out
    assert "[0] 門番の名前" in out and "[1] 通行の許可" in out

    # errors are ordinary errors (exit code 1)
    assert main(_args("resolve-request", "--index", "9", "--decision", "x"), settings=settings) == 1
    assert "out of range" in capsys.readouterr().err
    assert main(_args("resolve-request", "--index", "0"), settings=settings) == 1  # --decision is required
    capsys.readouterr()

    # redraft with an unresolved request is refused and removes nothing
    assert main(_args("redraft"), settings=settings, container_runner=CaptureAgy(GOOD_TEXT)) == 1
    assert "unresolved requests remain" in capsys.readouterr().err
    assert len(chapter_branches(work.path, CH)) == 1

    assert main(_args("resolve-request", "--index", "0", "--decision", "名前は付けない"), settings=settings) == 0
    out = capsys.readouterr().out
    assert "finished with state: COMPLETED" in out and "Request 0 resolved; 1 unresolved" in out
    assert "run redraft" not in out

    assert main(_args("resolve-request", "--index", "1", "--decision", "紋章で通る"), settings=settings) == 0
    out = capsys.readouterr().out
    assert "Request 1 resolved; 0 unresolved" in out and "run redraft" in out

    assert main(_args("show"), settings=settings) == 0
    assert "Requests: 0 unresolved of 2" in capsys.readouterr().out

    agy = CaptureAgy(GOOD_TEXT)
    rc = main(_args("redraft"), settings=settings, container_runner=agy)
    out = capsys.readouterr().out
    assert rc == 0
    assert "the decisions apply to this chapter's text only" in out
    assert "finished with state: COMPLETED" in out
    assert "名前は付けない" in agy.prompt() and "紋章で通る" in agy.prompt()

    # the new job branch is a normal DRAFTED cycle
    assert main(_args("show"), settings=settings) == 0
    assert "chapter state on the branch: DRAFTED" in capsys.readouterr().out

    assert main(_args("discard"), settings=settings) == 0
    capsys.readouterr()


def test_redraft_waiting_exit_code(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    settings, work, rec = marker_draft(tmp_path)
    assert main(_args("resolve-request", "--index", "0", "--decision", "a"), settings=settings) == 0
    assert main(_args("resolve-request", "--index", "1", "--decision", "b"), settings=settings) == 0
    capsys.readouterr()
    again = CaptureAgy("<!-- scene: S1 -->\nカイは【要確認：別の不明点】を見た。\n\n<!-- scene: S2 -->\nカイは門を通った。\n")
    rc = main(_args("redraft"), settings=settings, container_runner=again)
    out = capsys.readouterr().out
    assert rc == 3
    assert "finished with state: WAITING_HUMAN" in out and "Reason: undefined_settings: 1" in out
