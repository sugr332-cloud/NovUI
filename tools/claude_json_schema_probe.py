"""Probe script to investigate Claude CLI --json-schema behavior."""

from __future__ import annotations

import json
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
from typing import Any

# Ensure src is in sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from novui.claude_cli import run_claude
from novui.yamlio import dumps_yaml


PROMPT = "次の質問に答えてください。質問：日本の首都はどこですか。type には \"probe\" を入れてください。"
SCHEMA = '{"type":"object","properties":{"type":{"const":"probe"},"answer":{"type":"string"}},"required":["type","answer"],"additionalProperties":false}'


def check_schema_match(data: Any) -> bool:
    if isinstance(data, dict):
        if set(data.keys()) == {"type", "answer"}:
            if data.get("type") == "probe" and isinstance(data.get("answer"), str):
                return True
    return False


def find_schema_match_paths(stdout_bytes: bytes) -> list[str]:
    paths: list[str] = []
    try:
        text = stdout_bytes.decode("utf-8")
        parsed = json.loads(text)
    except Exception:
        return paths

    if check_schema_match(parsed):
        paths.append("$")

    if isinstance(parsed, dict):
        for k, v in parsed.items():
            if check_schema_match(v):
                paths.append(f"$.{k}")
            elif isinstance(v, str):
                try:
                    inner = json.loads(v)
                    if check_schema_match(inner):
                        paths.append(f"$.{k}(string)")
                except Exception:
                    pass
    return paths


def main() -> None:
    ver_proc = subprocess.run(["claude", "--version"], capture_output=True, text=True, check=True)
    claude_ver = ver_proc.stdout.strip()

    model = "opus"
    timeout_seconds = 300.0

    probe_root = Path.home() / ".local/share/novui-itest" / f"probe-{secrets.token_hex(4)}"
    probe_root.mkdir(mode=0o700, parents=True, exist_ok=True)

    configs = [
        ("A", "text", None),
        ("B", "text", SCHEMA),
        ("C", "json", SCHEMA),
    ]

    runs_data: dict[str, dict] = {}

    try:
        for run_id, output_format, schema_arg in configs:
            print(f"=== Starting Claude probe {run_id} (format={output_format}, schema={'yes' if schema_arg else 'no'}) ===")
            name = f"claude-probe-{run_id.lower()}-{secrets.token_hex(4)}"
            res = run_claude(
                prompt=PROMPT,
                cwd=probe_root,
                model=model,
                timeout_seconds=timeout_seconds,
                log_dir=probe_root,
                name=name,
                output_format=output_format,
                json_schema=schema_arg,
            )

            stdout_bytes = len(res.stdout)
            try:
                stdout_text = res.stdout.decode("utf-8")
                stdout_head = stdout_text[:1000]
            except UnicodeDecodeError:
                stdout_head = "<not utf-8>"

            try:
                stderr_text = res.stderr.decode("utf-8")
                stderr_head = stderr_text[:1000]
            except UnicodeDecodeError:
                stderr_head = "<not utf-8>"

            stdout_is_json = False
            top_level_keys: list[str] | None = None
            try:
                parsed = json.loads(res.stdout.decode("utf-8"))
                stdout_is_json = True
                if isinstance(parsed, dict):
                    top_level_keys = list(parsed.keys())
            except Exception:
                pass

            schema_match_paths = find_schema_match_paths(res.stdout)

            run_record = {
                "exit_code": res.exit_code,
                "elapsed_seconds": round(res.elapsed_seconds, 3),
                "timed_out": res.timed_out,
                "stdout_bytes": stdout_bytes,
                "stdout_head": stdout_head,
                "stderr_head": stderr_head,
                "stdout_is_json": stdout_is_json,
                "top_level_keys": top_level_keys,
                "schema_match_paths": schema_match_paths,
            }
            runs_data[run_id] = run_record
            print(f"Finished probe {run_id}: exit={res.exit_code}, elapsed={res.elapsed_seconds:.2f}s, is_json={stdout_is_json}, match={schema_match_paths}")
    finally:
        if probe_root.exists():
            shutil.rmtree(probe_root, ignore_errors=True)

    result_doc = {
        "claude_version": claude_ver,
        "model": model,
        "runs": runs_data,
    }

    out_path = Path("docs/phase1/results/1c-claude-json-schema.yaml")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(dumps_yaml(result_doc), encoding="utf-8")
    print(f"Wrote Claude probe results to {out_path}")


if __name__ == "__main__":
    main()
