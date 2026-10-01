"""Configuration and runtime settings for NovUI Controller."""

from dataclasses import dataclass
import os
from pathlib import Path
from typing import Mapping

DEFAULT_TIMEOUTS: Mapping[str, int] = {
    "agy_draft": 900,
    "agy_range_edit": 300,
    "claude": 300,
}


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    worktree_root: Path
    jobhome_root: Path
    agy_image: str
    agy_token_path: Path
    timeouts: Mapping[str, int]


def _resolve_abs_path(val: str, var_name: str) -> Path:
    expanded = Path(os.path.expanduser(val))
    if not expanded.is_absolute():
        raise ValueError(f"{var_name} must resolve to an absolute path, got: {val!r}")
    return expanded


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    """Load settings from environment mapping or os.environ.

    Expands user home directory (~) and enforces absolute paths.
    Does not access filesystem or open any token file.
    """
    if env is None:
        env = os.environ

    raw_data_dir = env.get("NOVUI_DATA_DIR", "~/.local/share/novui")
    raw_agy_image = env.get("NOVUI_AGY_IMAGE", "localhost/novui-spike:agy-1.2.14")
    raw_token_path = env.get("NOVUI_AGY_TOKEN", "~/.gemini/antigravity-cli/antigravity-oauth-token")

    data_dir = _resolve_abs_path(raw_data_dir, "NOVUI_DATA_DIR")
    token_path = _resolve_abs_path(raw_token_path, "NOVUI_AGY_TOKEN")

    worktree_root = data_dir / "worktrees"
    jobhome_root = data_dir / "jobhomes"

    return Settings(
        data_dir=data_dir,
        worktree_root=worktree_root,
        jobhome_root=jobhome_root,
        agy_image=raw_agy_image,
        agy_token_path=token_path,
        timeouts=DEFAULT_TIMEOUTS,
    )
