"""Tests for novui.container module without executing podman."""

from pathlib import Path
import pytest

from novui.container import (
    ContainerError,
    Mount,
    PromptTooLarge,
    agy_command,
    build_agy_mounts,
    build_run_args,
    run_container,
)


def test_build_agy_mounts(tmp_path: Path) -> None:
    jobhome_root = tmp_path / "jobhomes"
    jobhome_root.mkdir()
    jh = jobhome_root / "job-1-12345678"
    jh.mkdir()

    # 正常系
    mounts = build_agy_mounts(job_home=jh, jobhome_root=jobhome_root)
    assert mounts == [Mount(source=jh, target="/home/agy", mode="rw")]

    # 1. job_home が jobhome_root の外
    outside_jh = tmp_path / "outside_jh"
    outside_jh.mkdir()
    with pytest.raises(ContainerError):
        build_agy_mounts(job_home=outside_jh, jobhome_root=jobhome_root)

    # 2. job_home が jobhome_root そのもの
    with pytest.raises(ContainerError):
        build_agy_mounts(job_home=jobhome_root, jobhome_root=jobhome_root)

    # 3. パスにカンマまたはコロンを含む
    bad_jh_comma = jobhome_root / "jh,comma"
    bad_jh_comma.mkdir()
    with pytest.raises(ContainerError):
        build_agy_mounts(job_home=bad_jh_comma, jobhome_root=jobhome_root)

    bad_jh_colon = jobhome_root / "jh:colon"
    bad_jh_colon.mkdir()
    with pytest.raises(ContainerError):
        build_agy_mounts(job_home=bad_jh_colon, jobhome_root=jobhome_root)


def test_build_run_args() -> None:
    mounts = [
        Mount(source=Path("/path/to/jh"), target="/home/agy", mode="rw"),
    ]
    cmd = ["agy", "--version"]
    args = build_run_args(
        name="novui-job-1",
        image="novui-spike:test",
        mounts=mounts,
        command=cmd,
    )
    expected = [
        "podman",
        "run",
        "--rm",
        "--name",
        "novui-job-1",
        "--pull=never",
        "--userns=keep-id",
        "--cap-drop=all",
        "--security-opt=no-new-privileges",
        "-e",
        "HOME=/home/agy",
        "-w",
        "/workspace",
        "-v",
        "/path/to/jh:/home/agy:rw,Z",
        "novui-spike:test",
        "agy",
        "--version",
    ]
    assert args == expected

    # 不正なコンテナ名で ValueError
    with pytest.raises(ValueError):
        build_run_args(name="invalid_name", image="img", mounts=[], command=["echo"])


def test_agy_command() -> None:
    prompt = "第1章の執筆。\n空白や  --mode も含む日本語のプロンプト。"
    args = agy_command(model="gemini-2.5-flash", prompt=prompt)
    assert args == [
        "agy",
        "--mode",
        "accept-edits",
        "--model",
        "gemini-2.5-flash",
        f"--print={prompt}",
    ]
    assert "--dangerously-skip-permissions" not in args

    with pytest.raises(ValueError):
        agy_command(model="", prompt="hello")

    with pytest.raises(ValueError):
        agy_command(model="flash", prompt="")

    # NUL文字で ValueError
    with pytest.raises(ValueError, match="NUL"):
        agy_command(model="flash", prompt="hello\x00world")


def test_agy_command_prompt_size_limit() -> None:
    # 120,000 バイトちょうどは成功
    p_exact = "a" * 120_000
    args = agy_command(model="flash", prompt=p_exact)
    assert args[-1] == f"--print={p_exact}"

    # 120,001 バイトで PromptTooLarge
    p_over = "a" * 120_001
    with pytest.raises(PromptTooLarge) as exc_info:
        agy_command(model="flash", prompt=p_over)
    assert exc_info.value.size == 120_001
    assert exc_info.value.limit == 120_000

    # 日本語（1文字3バイト）で境界を確かめるテスト
    # "あ" は3バイト。40,000文字でちょうど120,000バイト
    jp_exact = "あ" * 40_000
    assert len(jp_exact.encode("utf-8")) == 120_000
    args_jp = agy_command(model="flash", prompt=jp_exact)
    assert args_jp[-1] == f"--print={jp_exact}"

    jp_over = "あ" * 40_000 + "x"
    assert len(jp_over.encode("utf-8")) == 120_001
    with pytest.raises(PromptTooLarge) as exc_jp:
        agy_command(model="flash", prompt=jp_over)
    assert exc_jp.value.size == 120_001
    assert exc_jp.value.limit == 120_000


def test_run_container_cleanup_behavior(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    log_dir = tmp_path / "logs"
    log_dir.mkdir()

    # 1. run_process が正常に戻り remove_container が失敗した場合に ContainerError
    def mock_run_process_ok(*args, **kwargs):
        from novui.procrun import ProcResult
        return ProcResult(
            exit_code=0,
            elapsed_seconds=1.0,
            timed_out=False,
            signal_sent=None,
            group_remaining=False,
            stdout=b"ok",
            stderr=b"",
        )

    def mock_remove_fail(name: str) -> None:
        raise ContainerError(f"Simulated remove failure for {name}")

    monkeypatch.setattr("novui.container.run_process", mock_run_process_ok)
    monkeypatch.setattr("novui.container.remove_container", mock_remove_fail)

    with pytest.raises(ContainerError, match="Simulated remove failure"):
        run_container(
            name="novui-test-remove-fail",
            image="test:img",
            mounts=[],
            command=["echo"],
            timeout_seconds=5.0,
            log_dir=log_dir,
        )

    # 2. run_process が例外を送出し remove_container も失敗した場合に、元の例外が伝わる
    def mock_run_process_err(*args, **kwargs):
        raise RuntimeError("Original process crash")

    monkeypatch.setattr("novui.container.run_process", mock_run_process_err)
    monkeypatch.setattr("novui.container.remove_container", mock_remove_fail)

    with pytest.raises(RuntimeError, match="Original process crash"):
        run_container(
            name="novui-test-proc-crash",
            image="test:img",
            mounts=[],
            command=["echo"],
            timeout_seconds=5.0,
            log_dir=log_dir,
        )
