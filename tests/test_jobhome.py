"""Tests for novui.jobhome module."""

import os
from pathlib import Path
import secrets
import pytest

from novui.jobhome import JobHomeError, TOKEN_RELPATH, cleanup_stale_job_homes, job_home


def test_job_home_lifecycle_and_permissions(tmp_path: Path) -> None:
    jobhome_root = tmp_path / "jobhomes"
    fake_token = tmp_path / "fake_token"
    token_secret = f"FAKE-TOKEN-{secrets.token_hex(8)}"
    fake_token.write_text(token_secret, encoding="utf-8")

    captured_home: Path | None = None
    with job_home(jobhome_root, "job-101", fake_token) as home:
        captured_home = home
        assert home.is_dir()
        # mode 700
        mode = home.stat().st_mode & 0o777
        assert mode == 0o700

        token_copy = home / TOKEN_RELPATH
        assert token_copy.is_file()
        assert token_copy.stat().st_mode & 0o777 == 0o600
        assert token_copy.read_text(encoding="utf-8") == token_secret

        # home の中にトークン以外のファイルがないことを確認
        all_files = [p for p in home.rglob("*") if p.is_file()]
        assert all_files == [token_copy]

    # with 終了後に削除されていること
    assert captured_home is not None
    assert not captured_home.exists()


def test_job_home_cleanup_on_exception(tmp_path: Path) -> None:
    jobhome_root = tmp_path / "jobhomes"
    fake_token = tmp_path / "fake_token"
    fake_token.write_text("FAKE-TOKEN-12345", encoding="utf-8")

    captured_home: Path | None = None
    with pytest.raises(RuntimeError) as exc_info:
        with job_home(jobhome_root, "job-102", fake_token) as home:
            captured_home = home
            raise RuntimeError("something went wrong inside job_home")

    assert "something went wrong inside job_home" in str(exc_info.value)
    assert captured_home is not None
    assert not captured_home.exists()


def test_invalid_token_errors_and_token_not_leaked(tmp_path: Path) -> None:
    jobhome_root = tmp_path / "jobhomes"
    token_secret = f"FAKE-TOKEN-{secrets.token_hex(8)}"

    # 1. トークンファイルが存在しない
    non_existent = tmp_path / "non_existent_token"
    with pytest.raises(JobHomeError) as exc_info:
        with job_home(jobhome_root, "job-103", non_existent):
            pass
    assert token_secret not in str(exc_info.value)

    # 2. ディレクトリである
    dir_token = tmp_path / "dir_token"
    dir_token.mkdir()
    with pytest.raises(JobHomeError) as exc_info:
        with job_home(jobhome_root, "job-104", dir_token):
            pass
    assert token_secret not in str(exc_info.value)

    # 3. シンボリックリンクである
    real_token = tmp_path / "real_token"
    real_token.write_text(token_secret, encoding="utf-8")
    symlink_token = tmp_path / "symlink_token"
    symlink_token.symlink_to(real_token)

    with pytest.raises(JobHomeError) as exc_info:
        with job_home(jobhome_root, "job-105", symlink_token):
            pass
    assert token_secret not in str(exc_info.value)


def test_invalid_job_id_raises_value_error(tmp_path: Path) -> None:
    jobhome_root = tmp_path / "jobhomes"
    fake_token = tmp_path / "token"
    fake_token.write_text("token", encoding="utf-8")

    with pytest.raises(ValueError):
        with job_home(jobhome_root, "invalid_id_format", fake_token):
            pass


def test_cleanup_stale_job_homes(tmp_path: Path) -> None:
    jobhome_root = tmp_path / "jobhomes"
    jobhome_root.mkdir()

    d1 = jobhome_root / "job-1-12345678"
    d2 = jobhome_root / "itest-abcdef12-34567890"
    d_other = jobhome_root / "other_directory"

    d1.mkdir()
    d2.mkdir()
    d_other.mkdir()

    removed = cleanup_stale_job_homes(jobhome_root)
    assert set(removed) == {d1, d2}
    assert not d1.exists()
    assert not d2.exists()
    assert d_other.exists()
