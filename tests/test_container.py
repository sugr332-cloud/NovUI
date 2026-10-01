"""Tests for novui.container module without executing podman."""

from pathlib import Path
import pytest

from novui.container import (
    ContainerError,
    Mount,
    agy_command,
    build_mounts,
    build_run_args,
    mount_arg,
    run_container,
)


def test_build_mounts_order_and_filtering(tmp_path: Path) -> None:
    worktree_root = tmp_path / "worktrees"
    worktree_root.mkdir()
    jobhome_root = tmp_path / "jobhomes"
    jobhome_root.mkdir()

    wt = worktree_root / "wt1"
    wt.mkdir()
    (wt / ".git").write_text("gitdir: ...")

    # PROTECTED_PATHS のうち一部だけ作成: project.yaml, world, .novui
    (wt / "project.yaml").write_text("name: novel")
    (wt / "world").mkdir()
    (wt / ".novui").mkdir()
    # characters や plot は作成しない

    jh = jobhome_root / "job-1-12345678"
    jh.mkdir()

    mounts = build_mounts(
        worktree=wt,
        job_home=jh,
        worktree_root=worktree_root,
        jobhome_root=jobhome_root,
    )

    # 期待される順序:
    # 1. worktree -> /workspace (rw)
    # 2. project.yaml -> /workspace/project.yaml (ro)
    # 3. world -> /workspace/world (ro)
    # 4. .novui -> /workspace/.novui (ro)
    # 5. .git -> /workspace/.git (ro)
    # 6. job_home -> /home/agy (rw)
    assert len(mounts) == 6
    assert mounts[0] == Mount(source=wt, target="/workspace", mode="rw")
    assert mounts[1] == Mount(source=wt / "project.yaml", target="/workspace/project.yaml", mode="ro")
    assert mounts[2] == Mount(source=wt / "world", target="/workspace/world", mode="ro")
    assert mounts[3] == Mount(source=wt / ".novui", target="/workspace/.novui", mode="ro")
    assert mounts[4] == Mount(source=wt / ".git", target="/workspace/.git", mode="ro")
    assert mounts[5] == Mount(source=jh, target="/home/agy", mode="rw")


def test_build_mounts_validation_errors(tmp_path: Path) -> None:
    worktree_root = tmp_path / "worktrees"
    worktree_root.mkdir()
    jobhome_root = tmp_path / "jobhomes"
    jobhome_root.mkdir()

    wt = worktree_root / "wt1"
    wt.mkdir()
    (wt / ".git").write_text("gitdir: ...")
    jh = jobhome_root / "jh1"
    jh.mkdir()

    # 1. worktree が worktree_root の外
    outside_wt = tmp_path / "outside_wt"
    outside_wt.mkdir()
    (outside_wt / ".git").write_text("gitdir: ...")
    with pytest.raises(ContainerError):
        build_mounts(worktree=outside_wt, job_home=jh, worktree_root=worktree_root, jobhome_root=jobhome_root)

    # 2. worktree が worktree_root そのもの
    with pytest.raises(ContainerError):
        build_mounts(worktree=worktree_root, job_home=jh, worktree_root=worktree_root, jobhome_root=jobhome_root)

    # 3. job_home が jobhome_root の外
    outside_jh = tmp_path / "outside_jh"
    outside_jh.mkdir()
    with pytest.raises(ContainerError):
        build_mounts(worktree=wt, job_home=outside_jh, worktree_root=worktree_root, jobhome_root=jobhome_root)

    # 4. .git がディレクトリ（linked worktree ではない本体）
    wt_dir_git = worktree_root / "wt_dir_git"
    wt_dir_git.mkdir()
    (wt_dir_git / ".git").mkdir()
    with pytest.raises(ContainerError):
        build_mounts(worktree=wt_dir_git, job_home=jh, worktree_root=worktree_root, jobhome_root=jobhome_root)

    # 5. パスにカンマまたはコロンを含む
    bad_name_wt = worktree_root / "wt,comma"
    bad_name_wt.mkdir()
    (bad_name_wt / ".git").write_text("gitdir: ...")
    with pytest.raises(ContainerError):
        build_mounts(worktree=bad_name_wt, job_home=jh, worktree_root=worktree_root, jobhome_root=jobhome_root)


def test_build_run_args() -> None:
    mounts = [
        Mount(source=Path("/path/to/wt"), target="/workspace", mode="rw"),
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
        "/path/to/wt:/workspace:rw,Z",
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


def test_run_container_always_cleans_up_on_exception(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    removed_containers: list[str] = []

    def mock_remove(name: str) -> None:
        removed_containers.append(name)

    def mock_run_process(*args, **kwargs):
        raise RuntimeError("Simulated process execution failure")

    monkeypatch.setattr("novui.container.remove_container", mock_remove)
    monkeypatch.setattr("novui.container.run_process", mock_run_process)

    log_dir = tmp_path / "logs"
    log_dir.mkdir()

    with pytest.raises(RuntimeError):
        run_container(
            name="novui-test-cleanup",
            image="test:img",
            mounts=[],
            command=["echo"],
            timeout_seconds=5.0,
            log_dir=log_dir,
        )

    assert removed_containers == ["novui-test-cleanup"]
