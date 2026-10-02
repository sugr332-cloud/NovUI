"""Probe script to test Claude CLI isolation options (--safe-mode, --restricted, --strict-mcp-config)."""

from __future__ import annotations

import json
from pathlib import Path
import secrets
import shutil
import subprocess
import sys

# Ensure src is in sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from novui.procrun import run_process
from novui.yamlio import dumps_yaml


PROMPT = "次の質問に答えてください。質問：日本の首都はどこですか。type には \"probe\" を入れてください。"
SCHEMA = '{"type":"object","properties":{"type":{"const":"probe"},"answer":{"type":"string"}},"required":["type","answer"],"additionalProperties":false}'


def main() -> None:
    ver_proc = subprocess.run(["claude", "--version"], capture_output=True, text=True, check=True)
    claude_ver = ver_proc.stdout.strip()

    model = "opus"
    timeout_seconds = 300.0

    probe_root = Path.home() / ".local/share/novui-itest" / f"probe-{secrets.token_hex(4)}"
    probe_root.mkdir(mode=0o700, parents=True, exist_ok=True)

    args = [
        "claude",
        "-p",
        "--tools",
        "Read",
        "--permission-prompts",
        "none",
        "--no-session-persistence",
        "--safe-mode",
        "--restricted",
        "--strict-mcp-config",
        "--model",
        model,
        "--output-format",
        "json",
        "--json-schema",
        SCHEMA,
    ]

    runs_data: list[dict] = []

    try:
        for run_idx in range(1, 3):
            print(f"=== Starting Claude isolation probe run {run_idx}/2 ===")
            name = f"claude-iso-{run_idx}-{secrets.token_hex(4)}"
            stdout_path = probe_root / f"{name}.stdout.log"
            stderr_path = probe_root / f"{name}.stderr.log"

            res = run_process(
                args,
                cwd=probe_root,
                stdin_data=PROMPT.encode("utf-8"),
                timeout_seconds=timeout_seconds,
                stdout_path=stdout_path,
                stderr_path=stderr_path,
            )

            try:
                stderr_text = res.stderr.decode("utf-8")
                stderr_head = stderr_text[:1000]
            except UnicodeDecodeError:
                stderr_head = "<not utf-8>"

            stdout_is_json = False
            parsed_json: dict | None = None
            try:
                loaded = json.loads(res.stdout.decode("utf-8"))
                if isinstance(loaded, dict):
                    parsed_json = loaded
                    stdout_is_json = True
            except Exception:
                pass

            is_error = None
            structured_output = None
            result_head = ""
            mentions_connector = False
            model_usage_keys: list[str] | None = None
            permission_denials = None

            if parsed_json is not None:
                is_error = parsed_json.get("is_error")
                structured_output = parsed_json.get("structured_output")
                result_val = parsed_json.get("result", "")
                if isinstance(result_val, str):
                    result_head = result_val[:300]
                    mentions_connector = ("Canva" in result_val) or ("コネクタ" in result_val)
                else:
                    result_head = str(result_val)[:300]
                    mentions_connector = ("Canva" in result_head) or ("コネクタ" in result_head)

                model_usage = parsed_json.get("modelUsage")
                if isinstance(model_usage, dict):
                    model_usage_keys = list(model_usage.keys())

                permission_denials = parsed_json.get("permission_denials")

            run_record = {
                "run": run_idx,
                "exit_code": res.exit_code,
                "elapsed_seconds": round(res.elapsed_seconds, 3),
                "timed_out": res.timed_out,
                "stderr_head": stderr_head,
                "stdout_is_json": stdout_is_json,
                "is_error": is_error,
                "structured_output": structured_output,
                "result_head": result_head,
                "mentions_connector": mentions_connector,
                "model_usage_keys": model_usage_keys,
                "permission_denials": permission_denials,
            }
            runs_data.append(run_record)
            print(f"Finished run {run_idx}: exit={res.exit_code}, elapsed={res.elapsed_seconds:.2f}s, structured={structured_output}, connector={mentions_connector}")
    finally:
        if probe_root.exists():
            shutil.rmtree(probe_root, ignore_errors=True)

    result_doc = {
        "claude_version": claude_ver,
        "runs": runs_data,
    }

    out_path = Path("docs/phase1/results/1c-claude-isolation-probe.yaml")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(dumps_yaml(result_doc), encoding="utf-8")
    print(f"Wrote Claude isolation probe results to {out_path}")


if __name__ == "__main__":
    main()
