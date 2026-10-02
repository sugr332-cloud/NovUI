"""Probe script testing AGY in-context prompting: missing setting markers (P1) and prompt length limit (P2)."""

from __future__ import annotations

from pathlib import Path
import secrets
import shutil
import sys

# Ensure src is in sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from novui.config import load_settings
from novui.container import Mount, image_id, run_container
from novui.jobhome import job_home
from novui.yamlio import dumps_yaml


P1_PROMPT = """\
あなたはシェルコマンドを実行できず、ファイルを読むこともできません。ツールは一切使わないでください。必要な情報はすべて以下にあります。
設定に書かれていないこと（人物の名前、地名、出来事など）を、推測で決めてはいけません。必要なのに設定にない場合は、その箇所に【要確認：何が不明か】と書いてください。
【世界観】
舞台は架空の港町。
【これまでの本文】
# Chapter 1

Initial draft text.
【指示】
主人公が名乗る場面を、続きとして1〜2文で書いてください。前置き・説明・コードブロックは不要です。本文だけを出力してください。
"""

P2_HEADER = "あなたはツールを一切使わないでください。以下の参考資料は無視して、最後の指示に従ってください。\n"
P2_PADDING = "参考資料：これは試験用の詰め物の文章です。内容に意味はありません。\n"
P2_FOOTER = "指示：「結合試験」という1行だけを出力してください。前置き・説明は不要です。"


def build_p2_prompt(target_bytes: int) -> tuple[str, int]:
    base_bytes = len((P2_HEADER + P2_FOOTER).encode("utf-8"))
    line_bytes = len(P2_PADDING.encode("utf-8"))
    if target_bytes <= base_bytes:
        prompt = P2_HEADER + P2_FOOTER
        return prompt, len(prompt.encode("utf-8"))
    n = (target_bytes - base_bytes) // line_bytes
    prompt = P2_HEADER + (P2_PADDING * n) + P2_FOOTER
    actual_bytes = len(prompt.encode("utf-8"))
    return prompt, actual_bytes


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

    p1_runs: list[dict] = []
    p1_success_count = 0

    p2_runs: list[dict] = []
    p2_success_100k = 0
    p2_success_125k = 0
    p2_success_140k = 0

    try:
        # === P1: 設定不足の印（3回） ===
        for run_idx in range(1, 4):
            print(f"=== Starting P1 run {run_idx}/3 ===")
            run_dir = probe_root / f"p1-{run_idx}"
            jobhome_root = run_dir / "jobhomes"
            log_dir = run_dir / "logs"
            run_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
            jobhome_root.mkdir(mode=0o700)
            log_dir.mkdir(mode=0o700)

            jh_id = f"itest-{secrets.token_hex(4)}"
            container_name = f"novui-itest-{secrets.token_hex(4)}"

            with job_home(jobhome_root, jh_id, token_path) as jh:
                mounts = [
                    Mount(source=jh, target="/home/agy", mode="rw"),
                ]
                cmd = [
                    "agy",
                    "--mode",
                    "accept-edits",
                    "--model",
                    model,
                    f"--print={P1_PROMPT}",
                ]
                res = run_container(
                    name=container_name,
                    image=settings.agy_image,
                    mounts=mounts,
                    command=cmd,
                    timeout_seconds=300.0,
                    log_dir=log_dir,
                )

            if real_token_bytes in res.stdout:
                stdout_text = "TOKEN_FOUND"
            else:
                stdout_text = res.stdout.decode("utf-8", errors="replace")

            if real_token_bytes in res.stderr:
                stderr_head = "TOKEN_FOUND"
            else:
                stderr_head = res.stderr[:500].decode("utf-8", errors="replace")

            has_marker = "【要確認：" in stdout_text
            success = (res.exit_code == 0 and len(stdout_text.strip()) > 0 and has_marker)
            if success:
                p1_success_count += 1

            p1_record = {
                "run": run_idx,
                "exit_code": res.exit_code,
                "elapsed_seconds": round(res.elapsed_seconds, 3),
                "stdout": stdout_text,
                "stderr_head": stderr_head,
                "has_marker": has_marker,
                "success": success,
            }
            p1_runs.append(p1_record)
            print(f"Finished P1 run {run_idx}: success={success}, exit={res.exit_code}, has_marker={has_marker}")

        # === P2: プロンプトの長さの上限 ===
        p2_targets = [
            (100000, 1),
            (100000, 2),
            (125000, 1),
            (140000, 1),
        ]

        for target_bytes, run_num in p2_targets:
            print(f"=== Starting P2 target={target_bytes} run {run_num} ===")
            prompt, actual_bytes = build_p2_prompt(target_bytes)

            run_dir = probe_root / f"p2-{target_bytes}-{run_num}"
            jobhome_root = run_dir / "jobhomes"
            log_dir = run_dir / "logs"
            run_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
            jobhome_root.mkdir(mode=0o700)
            log_dir.mkdir(mode=0o700)

            jh_id = f"itest-{secrets.token_hex(4)}"
            container_name = f"novui-itest-{secrets.token_hex(4)}"

            error_msg: str | None = None
            res = None

            try:
                with job_home(jobhome_root, jh_id, token_path) as jh:
                    mounts = [
                        Mount(source=jh, target="/home/agy", mode="rw"),
                    ]
                    cmd = [
                        "agy",
                        "--mode",
                        "accept-edits",
                        "--model",
                        model,
                        f"--print={prompt}",
                    ]
                    res = run_container(
                        name=container_name,
                        image=settings.agy_image,
                        mounts=mounts,
                        command=cmd,
                        timeout_seconds=300.0,
                        log_dir=log_dir,
                    )
            except Exception as e:
                error_msg = f"{type(e).__name__}: {e}"
                print(f"Exception during P2 run ({target_bytes}): {error_msg}")

            if res is not None:
                exit_code = res.exit_code
                elapsed_seconds = round(res.elapsed_seconds, 3)
                timed_out = res.timed_out
                if real_token_bytes in res.stdout:
                    stdout_head = "TOKEN_FOUND"
                else:
                    stdout_head = res.stdout[:200].decode("utf-8", errors="replace")

                if real_token_bytes in res.stderr:
                    stderr_head = "TOKEN_FOUND"
                else:
                    stderr_head = res.stderr[:500].decode("utf-8", errors="replace")

                raw_stdout = res.stdout.decode("utf-8", errors="replace")
                success = (exit_code == 0 and "結合試験" in raw_stdout)
            else:
                exit_code = -1
                elapsed_seconds = 0.0
                timed_out = False
                stdout_head = ""
                stderr_head = ""
                success = False

            if success:
                if target_bytes == 100000:
                    p2_success_100k += 1
                elif target_bytes == 125000:
                    p2_success_125k += 1
                elif target_bytes == 140000:
                    p2_success_140k += 1

            p2_record = {
                "target_bytes": target_bytes,
                "actual_bytes": actual_bytes,
                "run": run_num,
                "exit_code": exit_code,
                "elapsed_seconds": elapsed_seconds if isinstance(elapsed_seconds, (int, float)) else elapsed_seconds[0],
                "stdout_head": stdout_head,
                "stderr_head": stderr_head,
                "error": error_msg,
                "success": success,
            }
            p2_runs.append(p2_record)
            print(f"Finished P2 target={target_bytes} run {run_num}: success={success}, exit={exit_code}, error={error_msg}")

    finally:
        if probe_root.exists():
            shutil.rmtree(probe_root, ignore_errors=True)

    result_doc = {
        "agy_image": settings.agy_image,
        "image_id": img_id,
        "model": model,
        "p1": p1_runs,
        "p2": p2_runs,
        "summary": {
            "p1": f"{p1_success_count}/3",
            "p2": {
                "100000": f"{p2_success_100k}/2",
                "125000": f"{p2_success_125k}/1",
                "140000": f"{p2_success_140k}/1",
            },
        },
    }

    out_path = Path("docs/phase1/results/1c-agy-context-probe.yaml")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(dumps_yaml(result_doc), encoding="utf-8")
    print(f"Wrote context probe results to {out_path}")


if __name__ == "__main__":
    main()
