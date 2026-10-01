"""Claude CLI invocation and argument construction."""

from pathlib import Path
from novui.procrun import ProcResult, run_process


def claude_args(
    *,
    model: str,
    output_format: str = "text",
    json_schema: str | None = None,
) -> list[str]:
    """Construct arguments for Claude CLI.

    output_format must be either 'text' or 'json'.
    Does not include --dangerously-skip-permissions or --permission-mode.
    """
    if output_format not in ("text", "json"):
        raise ValueError(f"output_format must be 'text' or 'json', got: {output_format!r}")

    args = [
        "claude",
        "-p",
        "--tools",
        "Read",
        "--permission-prompts",
        "none",
        "--no-session-persistence",
        "--model",
        model,
        "--output-format",
        output_format,
    ]

    if json_schema is not None:
        args.extend(["--json-schema", json_schema])

    return args


def run_claude(
    *,
    prompt: str,
    cwd: Path,
    model: str,
    timeout_seconds: float,
    log_dir: Path,
    name: str,
    output_format: str = "text",
    json_schema: str | None = None,
) -> ProcResult:
    """Execute Claude CLI with prompt provided via stdin."""
    args = claude_args(model=model, output_format=output_format, json_schema=json_schema)
    stdout_path = log_dir / f"{name}.stdout.log"
    stderr_path = log_dir / f"{name}.stderr.log"

    return run_process(
        args,
        cwd=cwd,
        stdin_data=prompt.encode("utf-8"),
        timeout_seconds=timeout_seconds,
        stdout_path=stdout_path,
        stderr_path=stderr_path,
    )
