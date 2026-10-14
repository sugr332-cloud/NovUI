"""CLI tests for edit-range (range_edit on the job branch; fake AGY)."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_requestflow import CaptureAgy  # noqa: E402
from test_validatejob import CH, GOOD_TEXT, drafted  # noqa: E402

from novui.cli import main

KEY = "test-novel"
NEW = "門番は紋章を見て、黙って道をあけた。"


@pytest.fixture(autouse=True)
def mock_container_meta(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("novui.jobrunner.image_label", lambda img, key: "1.2.14")
    monkeypatch.setattr(
        "novui.jobrunner.image_id",
        lambda img: "sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
    )


def _args(command: str, *extra: str) -> list[str]:
    return [command, "--work", KEY, "--chapter", CH, *extra]


def test_edit_range_cli(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    settings, work, rec = drafted(tmp_path)
    draft = Path(rec["worktree"]) / f"chapters/{CH}/draft.md"
    capsys.readouterr()

    # --anchor and --reason are required unless --retry
    assert main(_args("edit-range", "--anchor", "門番が紋章に目を留めた。"), settings=settings) == 1
    assert "--anchor and --reason are required" in capsys.readouterr().err

    # an anchor that matches twice -> WAITING_HUMAN (exit code 3)
    rc = main(_args("edit-range", "--anchor", "カイは", "--reason", "短く"), settings=settings,
              container_runner=CaptureAgy(NEW))
    out = capsys.readouterr().out
    assert rc == 3 and "Reason: anchor_not_unique: 2" in out
    waiting = out.split("Job ", 1)[1].split(" ", 1)[0]

    # the waiting Job blocks the next edit until it is retried
    assert main(_args("edit-range", "--anchor", "門番が紋章に目を留めた。", "--reason", "x"), settings=settings,
                container_runner=CaptureAgy(NEW)) == 1
    assert "waiting for a Human decision" in capsys.readouterr().err
    assert main(_args("edit-range", "--retry", waiting, "--reason", "x"), settings=settings) == 1
    assert "omit --anchor and --reason" in capsys.readouterr().err
    # a retry with the same (bad) anchor waits again and closes the old Job
    assert main(_args("edit-range", "--retry", waiting), settings=settings, container_runner=CaptureAgy(NEW)) == 3
    out = capsys.readouterr().out
    again = out.split("Job ", 1)[1].split(" ", 1)[0]
    assert again != waiting

    # cancel-job closes it; then a unique anchor (with context) -> COMPLETED
    assert main(["cancel-job", "--work", KEY, "--job", again], settings=settings) == 0
    assert f"Job {again} finished with state: CANCELLED" in capsys.readouterr().out
    assert main(["cancel-job", "--work", KEY, "--job", again], settings=settings) == 1
    assert "not WAITING_HUMAN" in capsys.readouterr().err
    assert main(["cancel-job", "--work", KEY, "--job", rec["job_id"]], settings=settings) == 1
    assert "only edit Jobs" in capsys.readouterr().err
    rc = main(_args("edit-range", "--anchor", "カイは", "--after", "理由", "--reason", "短く"), settings=settings,
              container_runner=CaptureAgy("カイは"))
    assert rc == 0, capsys.readouterr()
    assert draft.read_text(encoding="utf-8") == GOOD_TEXT  # the replacement equals the range
    capsys.readouterr()


def test_edit_range_cli_success_and_marker_hint(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    settings, work, rec = drafted(tmp_path)
    draft = Path(rec["worktree"]) / f"chapters/{CH}/draft.md"
    capsys.readouterr()

    rc = main(_args("edit-range", "--anchor", "門番が紋章に目を留めた。", "--reason", "門番をはっきり"),
              settings=settings, container_runner=CaptureAgy("門番は【要確認：門番の名前】と名乗った。"))
    out = capsys.readouterr().out
    assert rc == 3 and "Reason: undefined_settings: 1" in out
    first = out.split("Job ", 1)[1].split(" ", 1)[0]
    assert draft.read_text(encoding="utf-8") == GOOD_TEXT

    assert main(_args("resolve-request", "--index", "0", "--decision", "名前は付けない"), settings=settings) == 0
    out = capsys.readouterr().out
    assert f"run edit-range --retry {first}" in out and "run redraft" not in out

    agy = CaptureAgy(NEW)
    rc = main(_args("edit-range", "--retry", first), settings=settings, container_runner=agy)
    out = capsys.readouterr().out
    assert rc == 0 and "finished with state: COMPLETED" in out
    assert "名前は付けない" in agy.prompt() and "門番をはっきり" in agy.prompt()
    assert draft.read_text(encoding="utf-8") == GOOD_TEXT.replace("門番が紋章に目を留めた。", NEW)

    assert main(_args("show"), settings=settings) == 0
    assert "chapter state on the branch: DRAFTED" in capsys.readouterr().out


def test_rewrite_cli(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    settings, work, rec = drafted(tmp_path)
    capsys.readouterr()
    assert main(_args("rewrite"), settings=settings) == 1
    assert "--reason is required" in capsys.readouterr().err
    marked = GOOD_TEXT.replace("門番が", "【要確認：門番の態度】門番が")
    assert main(_args("rewrite", "--reason", "テンポを上げる"), settings=settings,
                container_runner=CaptureAgy(marked)) == 3
    first = capsys.readouterr().out.split("Job ", 1)[1].split(" ", 1)[0]
    assert main(_args("resolve-request", "--index", "0", "--decision", "黙る"), settings=settings) == 0
    assert f"run rewrite --retry {first}" in capsys.readouterr().out
    assert main(_args("rewrite", "--retry", first, "--reason", "x"), settings=settings) == 1
    assert "omit --reason" in capsys.readouterr().err
    agy = CaptureAgy(GOOD_TEXT)
    assert main(_args("rewrite", "--retry", first), settings=settings, container_runner=agy) == 0
    assert "テンポを上げる" in agy.prompt() and "黙る" in agy.prompt()


def test_fix_queue_cli(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from test_validatejob import integrity, ok_claude, writing
    from novui.validatejob import run_validate_job
    settings, work, rec = drafted(tmp_path)
    run_validate_job(settings, work, CH, claude_runner=ok_claude(integrity("WARNING"), writing(1)))
    capsys.readouterr()

    assert main(_args("show"), settings=settings) == 0
    out = capsys.readouterr().out
    assert "decision: none" in out
    assert "  I1 [WARNING character] C001 speech.first_person" in out and "  W1 [writing redundant] 冗長" in out

    assert main(_args("fix", "--findings", "I1"), settings=settings) == 1
    assert "REQUEST_FIX" in capsys.readouterr().err
    assert main(_args("decide", "--action", "REQUEST_FIX", "--reason", "一人称"), settings=settings) == 0
    assert main(_args("fix", "--findings", "I1, W1", "--note", "短く"), settings=settings) == 0
    out = capsys.readouterr().out
    assert "Queued Q-0001: range_edit ch-001 (I1)" in out and "Queued Q-0002" in out

    assert main(["queue", "--work", KEY], settings=settings) == 0
    assert "Q-0001 [pending] range_edit ch-001 I1" in capsys.readouterr().out

    rc = main(["run-queue", "--work", KEY], settings=settings, container_runner=CaptureAgy(NEW))
    out = capsys.readouterr().out
    assert rc == 0 and "The queue is empty." in out and out.count("finished with state: COMPLETED") == 2
    assert main(["queue", "--work", KEY, "--cancel", "Q-0001"], settings=settings) == 1
    assert "cannot be cancelled" in capsys.readouterr().err
    assert main(["queue", "--work", KEY], settings=settings) == 0
    assert "Q-0002 [done] range_edit ch-001 W1 job" in capsys.readouterr().out
