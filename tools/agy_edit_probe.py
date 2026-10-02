"""Probe script comparing AGY edit approaches (V1: prompt restriction, V2: stdout response)."""

from __future__ import annotations

from pathlib import Path
import secrets
import shutil
import subprocess
import sys

# Ensure src is in sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from novui.config import load_settings
from novui.container import agy_command, build_mounts, image_id, run_container
from novui.gitinspect import get_changes, run_git
from novui.jobhome import job_home
from novui.yamlio import dumps_yaml


V1_PROMPT = """\
あなたはシェルコマンドを実行できません。実行しようとすると作業全体が失敗します。ファイルの読み込みと編集には、ファイルを読む・編集するためのツールだけを使ってください。
作業：chapters/ch-001/draft.md の末尾に「結合試験」という1行を追記してください。それ以外のファイルは変更しないでください。完了したら「完了」とだけ出力してください。
"""

V2_PROMPT = """\
あなたはシェルコマンドを実行できません。実行しようとすると作業全体が失敗します。ファイルを作成・変更しないでください。ファイルを読む必要もありません。
次の本文の続きとして、「結合試験」という1行だけを出力してください。前置き・説明・コードブロックは不要です。
---
# Chapter 1

Initial draft text.
"""


def setup_env_for_run(run_dir: Path) -> tuple[Path, Path, Path, Path]:
    data_dir = run_dir / "data"
    worktree_root = data_dir / "worktrees"
    jobhome_root = data_dir / "jobhomes"
    repo_dir = run_dir / "repo"
    log_dir = run_dir / "logs"

    run_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    data_dir.mkdir(mode=0o700)
    worktree_root.mkdir(mode=0o700)
    jobhome_root.mkdir(mode=0o700)
    repo_dir.mkdir(mode=0o700)
    log_dir.mkdir(mode=0o700)

    # Initialize repository
    subprocess.run(["git", "-C", str(repo_dir), "init", "-b", "main"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo_dir), "config", "user.name", "test"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo_dir), "config", "user.email", "test@example.invalid"], check=True, capture_output=True)

    (repo_dir / "project.yaml").write_text("title: Integration Test Novel\n", encoding="utf-8")
    (repo_dir / "world").mkdir()
    (repo_dir / "world/w.md").write_text("# World Setting\n", encoding="utf-8")
    (repo_dir / "characters").mkdir()
    (repo_dir / "characters/C001.yaml").write_text("id: C001\nname: Hero\n", encoding="utf-8")
    (repo_dir / ".novui/approvals").mkdir(parents=True)
    (repo_dir / ".novui/approvals/.keep").write_text("", encoding="utf-8")
    (repo_dir / "chapters/ch-001").mkdir(parents=True)
    (repo_dir / "chapters/ch-001/draft.md").write_text("# Chapter 1\n\nInitial draft text.\n", encoding="utf-8")

    subprocess.run(["git", "-C", str(repo_dir), "add", "."], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo_dir), "-c", "user.name=test", "-c", "user.email=test@example.invalid", "commit", "-m", "Initial commit"],
        check=True,
        capture_output=True,
    )

    # Create linked worktree
    wt_name = f"itest-{secrets.token_hex(4)}"
    worktree_dir = worktree_root / wt_name
    run_git(repo_dir, "worktree", "add", str(worktree_dir), "-b", wt_name)

    return repo_dir, worktree_dir, worktree_root, jobhome_root, log_dir


def main() -> None:
    settings = load_settings()
    token_path = settings.agy_token_path
    if not token_path.is_file():
        print(f"Error: AGY token file not found: {token_path}", file=sys.stderr)
        sys.exit(1)

    real_token_bytes = token_path.read_bytes().strip()
    if not real_token_bytes:
        print("Error: Real token file is empty", file=sys.stderr)
        sys.exit(1)

    model = "gemini-3.8-flash-high"
    img_id = image_id(settings.agy_image)

    probe_root = Path.home() / ".local/share/novui-itest" / f"probe-{secrets.token_hex(4)}"
    probe_root.mkdir(mode=0o700, parents=True, exist_ok=True)

    runs_data: list[dict] = []
    v1_success_count = 0
    v2_success_count = 0

    variants = [
        ("V1", V1_PROMPT),
        ("V2", V2_PROMPT),
    ]

    try:
        for variant_name, prompt in variants:
            for run_num in range(1, 4):
                print(f"=== Starting probe {variant_name} run {run_num}/3 ===")
                run_dir = probe_root / f"{variant_name}-{run_num}"
                repo_dir, worktree_dir, worktree_root, jobhome_root, log_dir = setup_env_for_run(run_dir)

                jh_id = f"itest-{secrets.token_hex(4)}"
                container_name = f"novui-itest-{secrets.token_hex(4)}"

                with job_home(jobhome_root, jh_id, token_path) as jh:
                    mounts = build_mounts(
                        worktree=worktree_dir,
                        job_home=jh,
                        worktree_root=worktree_root,
                        jobhome_root=jobhome_root,
                    )
                    cmd = agy_command(model=model, prompt=prompt)
                    res = run_container(
                        name=container_name,
                        image=settings.agy_image,
                        mounts=mounts,
                        command=cmd,
                        timeout_seconds=300.0,
                        log_dir=log_dir,
                    )

                # Process results and token safety
                stdout_len = len(res.stdout)
                if real_token_bytes in res.stdout:
                    stdout_head = "TOKEN_FOUND"
                else:
                    stdout_head = res.stdout[:500].decode("utf-8", errors="replace")

                if real_token_bytes in res.stderr:
                    stderr_head = "TOKEN_FOUND"
                else:
                    stderr_head = res.stderr[:500].decode("utf-8", errors="replace")

                changes = get_changes(worktree_dir)
                changes_record = [{"status": c.status, "path": c.path} for c in changes]

                draft_file = worktree_dir / "chapters/ch-001/draft.md"
                draft_content = draft_file.read_text(encoding="utf-8") if draft_file.is_file() else ""
                draft_has_marker = "結合試験" in draft_content

                if variant_name == "V1":
                    success = (
                        res.exit_code == 0
                        and stdout_len > 0
                        and len(changes) == 1
                        and changes[0].path == "chapters/ch-001/draft.md"
                        and draft_has_marker
                    )
                    if success:
                        v1_success_count += 1
                else:  # V2
                    stdout_str = res.stdout.decode("utf-8", errors="replace")
                    success = (
                        res.exit_code == 0
                        and "結合試験" in stdout_str
                        and len(changes) == 0
                    )
                    if success:
                        v2_success_count += 1

                run_record = {
                    "variant": variant_name,
                    "run": run_num,
                    "exit_code": res.exit_code,
                    "elapsed_seconds": round(res.elapsed_seconds, 3),
                    "timed_out": res.timed_out,
                    "stdout_len": stdout_len,
                    "stdout_head": stdout_head,
                    "stderr_head": stderr_head,
                    "changes": changes_record,
                    "draft_has_marker": draft_has_marker,
                    "success": success,
                }
                runs_data.append(run_record)
                print(f"Finished {variant_name} run {run_num}: success={success}, exit={res.exit_code}, elapsed={res.elapsed_seconds:.2f}s")
    finally:
        if probe_root.exists():
            shutil.rmtree(probe_root, ignore_errors=True)

    result_doc = {
        "agy_image": settings.agy_image,
        "image_id": img_id,
        "model": model,
        "runs": runs_data,
        "summary": {
            "V1": f"{v1_success_count}/3",
            "V2": f"{v2_success_count}/3",
        },
    }

    out_path = Path("docs/phase1/results/1c-agy-edit-probe.yaml")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(dumps_yaml(result_doc), encoding="utf-8")
    print(f"Wrote probe results to {out_path}")


if __name__ == "__main__":
    main()
