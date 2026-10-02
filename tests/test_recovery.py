"""Tests for novui.recovery module."""

from pathlib import Path

from novui.config import Settings
from novui.jobrecord import apply_transition, load_job_record, new_job_record, save_job_record
from novui.recovery import recover_on_startup
from novui.states import JobEvent, JobState


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
    )


def test_recover_on_startup_transitions_and_stale_cleanup(tmp_path: Path) -> None:
    settings = _make_settings(tmp_path)
    jdir = settings.data_dir / "works" / "w1" / "jobs"
    jdir.mkdir(parents=True)

    # 1. QUEUED -> STOPPED
    rec_q = new_job_record("job-0001", "draft", chapter_id="ch-001")
    save_job_record(jdir, rec_q)

    # 2. RUNNING -> STOPPED
    rec_r = new_job_record("job-0002", "draft", chapter_id="ch-001")
    rec_r = apply_transition(rec_r, JobEvent.LOCK_ACQUIRED)
    save_job_record(jdir, rec_r)

    # 3. CHECKING -> STOPPED
    rec_c = new_job_record("job-0003", "draft", chapter_id="ch-001")
    rec_c = apply_transition(rec_c, JobEvent.LOCK_ACQUIRED)
    rec_c = apply_transition(rec_c, JobEvent.CLI_EXITED)
    save_job_record(jdir, rec_c)

    # 4. VALIDATING -> STOPPED
    rec_v = new_job_record("job-0004", "draft", chapter_id="ch-001")
    rec_v = apply_transition(rec_v, JobEvent.LOCK_ACQUIRED)
    rec_v = apply_transition(rec_v, JobEvent.CLI_EXITED)
    rec_v = apply_transition(rec_v, JobEvent.CHECK_PASSED, needs_validation=True)
    save_job_record(jdir, rec_v)

    # 5. WAITING_HUMAN -> unchanged
    rec_w = new_job_record("job-0005", "draft", chapter_id="ch-001")
    rec_w = apply_transition(rec_w, JobEvent.LOCK_ACQUIRED)
    rec_w = apply_transition(rec_w, JobEvent.CLI_EXITED)
    rec_w = apply_transition(rec_w, JobEvent.NEEDS_HUMAN_INPUT, reason="need user input")
    save_job_record(jdir, rec_w)

    # 6. COMPLETED -> unchanged
    rec_comp = new_job_record("job-0006", "draft", chapter_id="ch-001")
    rec_comp = apply_transition(rec_comp, JobEvent.LOCK_ACQUIRED)
    rec_comp = apply_transition(rec_comp, JobEvent.CLI_EXITED)
    rec_comp = apply_transition(rec_comp, JobEvent.CHECK_PASSED, needs_validation=False)
    save_job_record(jdir, rec_comp)

    # 7. FAILED -> unchanged
    rec_f = new_job_record("job-0007", "draft", chapter_id="ch-001")
    rec_f = apply_transition(rec_f, JobEvent.LOCK_ACQUIRED)
    rec_f = apply_transition(rec_f, JobEvent.TIMED_OUT)
    save_job_record(jdir, rec_f)

    # Setup stale jobhomes
    settings.jobhome_root.mkdir(parents=True)
    stale1 = settings.jobhome_root / "job-old1"
    stale1.mkdir()
    stale2 = settings.jobhome_root / "itest-abc12345"
    stale2.mkdir()
    keep = settings.jobhome_root / "other-dir"
    keep.mkdir()

    # Execute recovery
    stopped = recover_on_startup(settings)

    assert stopped == ["job-0001", "job-0002", "job-0003", "job-0004"]

    # Verify states on disk
    assert load_job_record(jdir / "job-0001.yaml")["state"] == JobState.STOPPED.name
    assert load_job_record(jdir / "job-0002.yaml")["state"] == JobState.STOPPED.name
    assert load_job_record(jdir / "job-0003.yaml")["state"] == JobState.STOPPED.name
    assert load_job_record(jdir / "job-0004.yaml")["state"] == JobState.STOPPED.name
    assert load_job_record(jdir / "job-0005.yaml")["state"] == JobState.WAITING_HUMAN.name
    assert load_job_record(jdir / "job-0006.yaml")["state"] == JobState.COMPLETED.name
    assert load_job_record(jdir / "job-0007.yaml")["state"] == JobState.FAILED.name

    # Verify stale jobhomes cleaned
    assert not stale1.exists()
    assert not stale2.exists()
    assert keep.exists()


def test_recover_on_startup_empty(tmp_path: Path) -> None:
    settings = _make_settings(tmp_path)
    stopped = recover_on_startup(settings)
    assert stopped == []
