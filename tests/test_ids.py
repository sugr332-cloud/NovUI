"""Tests for novui.ids module."""

from pathlib import Path

from novui.config import Settings
from novui.ids import next_job_id
from novui.jobrunner import jobs_dir


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


def test_next_job_id_empty_and_increments(tmp_path: Path) -> None:
    settings = _make_settings(tmp_path)
    work_key = "test-work"

    # 1. jobs_dir が存在しないとき job-1
    assert next_job_id(settings, work_key) == "job-1"

    # 2. jobs_dir が空のとき job-1
    jdir = jobs_dir(settings, work_key)
    jdir.mkdir(parents=True)
    assert next_job_id(settings, work_key) == "job-1"

    # 3. 関係ないファイルや job-1.yaml があるとき
    (jdir / "other.yaml").write_text("", encoding="utf-8")
    (jdir / "job-invalid.yaml").write_text("", encoding="utf-8")
    (jdir / "job-1.yaml").write_text("", encoding="utf-8")
    assert next_job_id(settings, work_key) == "job-2"

    # 4. job-5.yaml があるとき次は job-6
    (jdir / "job-5.yaml").write_text("", encoding="utf-8")
    assert next_job_id(settings, work_key) == "job-6"
