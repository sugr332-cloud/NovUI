"""Claude CLI invocation and argument construction."""

from pathlib import Path
from novui.procrun import ProcResult, run_process


def claude_args(
    *,
    model: str,
    json_schema: str,
) -> list[str]:
    """Construct arguments for Claude CLI.

    Returns the exact argument list required for isolated Claude execution.
    Raises ValueError if model or json_schema is empty.
    """
    if not model or not model.strip():
        raise ValueError("model cannot be empty")
    if not json_schema or not json_schema.strip():
        raise ValueError("json_schema cannot be empty")

    return [
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
        json_schema,
    ]


def run_claude(
    *,
    prompt: str,
    cwd: Path,
    model: str,
    timeout_seconds: float,
    log_dir: Path,
    name: str,
    json_schema: str,
) -> ProcResult:
    """Execute Claude CLI with prompt provided via stdin."""
    args = claude_args(model=model, json_schema=json_schema)
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
