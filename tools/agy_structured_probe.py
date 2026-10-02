"""Probe script investigating AGY structured output with --json-schema and --output-format json."""

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

import jsonschema
from novui.config import load_settings
from novui.container import Mount, image_id, run_container
from novui.gitinspect import get_changes, run_git
from novui.jobhome import job_home
from novui.yamlio import dumps_yaml


SCHEMA = '{"type":"object","properties":{"type":{"const":"draft_output"},"text":{"type":"string"},"requests":{"type":"array","items":{"type":"object","properties":{"kind":{"enum":["undefined_setting","conflict","question"]},"target":{"type":["string","null"]},"message":{"type":"string"}},"required":["kind","target","message"],"additionalProperties":false}}},"required":["type","text","requests"],"additionalProperties":false}'

V3A_PROMPT = """\
あなたはシェルコマンドを実行できません。実行しようとすると作業全体が失敗します。ファイルを作成・変更しないでください。
次の本文の続きとして「結合試験」という1行を text に入れてください。requests は空の配列にしてください。
---
# Chapter 1

Initial draft text.
"""

V3B_PROMPT = """\
あなたはシェルコマンドを実行できません。実行しようとすると作業全体が失敗します。ファイルを作成・変更しないでください。ファイルの読み込みには、ファイルを読むためのツールだけを使ってください。
chapters/ch-001/draft.md と world/w.md を読み、draft.md の続きとして「結合試験」という1行を text に入れてください。また、world/w.md に主人公の名前が書かれていない場合は、requests に kind を undefined_setting、target を "characters"、message にその旨を書いた要求を1件入れてください。
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
    (repo_dir / "world/w.md").write_text("# World\n\n舞台は架空の港町。\n", encoding="utf-8")
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


def find_schema_matches(parsed: Any, validator: jsonschema.Draft202012Validator) -> tuple[list[str], Any | None]:
    paths: list[str] = []
    first_matched: Any | None = None

    if validator.is_valid(parsed):
        paths.append("$")
        first_matched = parsed

    if isinstance(parsed, dict):
        for k, v in parsed.items():
            if validator.is_valid(v):
                paths.append(f"$.{k}")
                if first_matched is None:
                    first_matched = v
            elif isinstance(v, str):
                try:
                    inner = json.loads(v)
                    if validator.is_valid(inner):
                        paths.append(f"$.{k}(string)")
                        if first_matched is None:
                            first_matched = inner
                except Exception:
                    pass
    return paths, first_matched


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

    schema_dict = json.loads(SCHEMA)
    validator = jsonschema.Draft202012Validator(schema_dict)

    probe_root = Path.home() / ".local/share/novui-itest" / f"probe-{secrets.token_hex(4)}"
    probe_root.mkdir(mode=0o700, parents=True, exist_ok=True)

    runs_data: list[dict] = []
    v3a_success_count = 0
    v3b_success_count = 0

    variants = [
        ("V3a", V3A_PROMPT),
        ("V3b", V3B_PROMPT),
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
                    mounts = [
                        Mount(source=worktree_dir, target="/workspace", mode="ro"),
                        Mount(source=jh, target="/home/agy", mode="rw"),
                    ]
                    cmd = [
                        "agy",
                        "--mode",
                        "accept-edits",
                        "--model",
                        model,
                        "--output-format",
                        "json",
                        "--json-schema",
                        SCHEMA,
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

                # Process results and token safety
                stdout_len = len(res.stdout)
                if real_token_bytes in res.stdout:
                    stdout_head = "TOKEN_FOUND"
                else:
                    stdout_head = res.stdout[:1500].decode("utf-8", errors="replace")

                if real_token_bytes in res.stderr:
                    stderr_head = "TOKEN_FOUND"
                else:
                    stderr_head = res.stderr[:500].decode("utf-8", errors="replace")

                stdout_str = res.stdout.decode("utf-8", errors="replace").strip()
                stdout_is_json = False
                parsed_json: Any | None = None
                top_level_keys: list[str] | None = None

                try:
                    loaded = json.loads(stdout_str)
                    stdout_is_json = True
                    parsed_json = loaded
                    if isinstance(loaded, dict):
                        top_level_keys = list(loaded.keys())
                except Exception:
                    pass

                schema_match_paths: list[str] = []
                matched: Any | None = None
                if stdout_is_json and parsed_json is not None:
                    schema_match_paths, matched = find_schema_matches(parsed_json, validator)

                text_has_marker = False
                requests_count = 0
                has_undefined_setting = False
                if isinstance(matched, dict):
                    text_val = matched.get("text", "")
                    if isinstance(text_val, str) and "結合試験" in text_val:
                        text_has_marker = True
                    reqs = matched.get("requests")
                    if isinstance(reqs, list):
                        requests_count = len(reqs)
                        has_undefined_setting = any(
                            isinstance(r, dict) and r.get("kind") == "undefined_setting" for r in reqs
                        )

                changes = get_changes(worktree_dir)
                changes_record = [{"status": c.status, "path": c.path} for c in changes]

                if variant_name == "V3a":
                    success = (
                        res.exit_code == 0
                        and len(schema_match_paths) >= 1
                        and text_has_marker
                        and requests_count == 0
                        and len(changes) == 0
                    )
                    if success:
                        v3a_success_count += 1
                else:  # V3b
                    success = (
                        res.exit_code == 0
                        and len(schema_match_paths) >= 1
                        and text_has_marker
                        and requests_count >= 1
                        and has_undefined_setting
                        and len(changes) == 0
                    )
                    if success:
                        v3b_success_count += 1

                run_record = {
                    "variant": variant_name,
                    "run": run_num,
                    "exit_code": res.exit_code,
                    "elapsed_seconds": round(res.elapsed_seconds, 3),
                    "timed_out": res.timed_out,
                    "stdout_len": stdout_len,
                    "stdout_head": stdout_head,
                    "stderr_head": stderr_head,
                    "stdout_is_json": stdout_is_json,
                    "top_level_keys": top_level_keys,
                    "schema_match_paths": schema_match_paths,
                    "matched": matched,
                    "text_has_marker": text_has_marker,
                    "requests_count": requests_count,
                    "changes": changes_record,
                    "success": success,
                }
                runs_data.append(run_record)
                print(
                    f"Finished {variant_name} run {run_num}: success={success}, exit={res.exit_code}, "
                    f"paths={schema_match_paths}, marker={text_has_marker}, reqs={requests_count}"
                )
    finally:
        if probe_root.exists():
            shutil.rmtree(probe_root, ignore_errors=True)

    result_doc = {
        "agy_image": settings.agy_image,
        "image_id": img_id,
        "model": model,
        "schema": SCHEMA,
        "runs": runs_data,
        "summary": {
            "V3a": f"{v3a_success_count}/3",
            "V3b": f"{v3b_success_count}/3",
        },
    }

    out_path = Path("docs/phase1/results/1c-agy-structured-probe.yaml")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(dumps_yaml(result_doc), encoding="utf-8")
    print(f"Wrote structured probe results to {out_path}")


if __name__ == "__main__":
    main()
