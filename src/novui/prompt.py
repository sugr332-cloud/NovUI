"""Prompt builder for AGY execution."""

from dataclasses import dataclass
import hashlib
from pathlib import Path
from typing import Sequence

from novui.paths import PathError, ensure_within, is_safe_relpath

PREAMBLE: str = (
    "あなたはシェルコマンドを実行できず、ファイルを読むこともできません。ツールは一切使わないでください。必要な情報はすべて以下にあります。\n"
    "設定に書かれていないこと（人物の名前、地名、出来事など）を、推測で決めてはいけません。必要なのに設定にない場合は、その箇所に【要確認：何が不明か】と書いてください。\n"
    "出力は本文だけにしてください。前置き・説明・コードブロックは不要です。"
)


@dataclass(frozen=True)
class ContextEntry:
    path: str  # 作品リポジトリ内の相対パス
    sha256: str  # "sha256:<16進>"


@dataclass(frozen=True)
class BuiltPrompt:
    text: str
    context: tuple[ContextEntry, ...]
    size_bytes: int


import re

PROMPTS_DIR: Path = Path(__file__).resolve().parents[2] / "prompts"
_TEMPLATE_NAME_RE = re.compile(r"^[a-z0-9_]+$")


_TEMPLATE_GROUPS = frozenset({"claude", "agy"})


def load_prompt_template(name: str, *, group: str = "claude", **values: str) -> str:
    """Load prompt template from prompts/<group>/<name>.md and substitute {{key}} placeholders."""
    if group not in _TEMPLATE_GROUPS:
        raise ValueError(f"Invalid template group: {group!r}; must be one of {sorted(_TEMPLATE_GROUPS)}")
    if not _TEMPLATE_NAME_RE.match(name):
        raise ValueError(f"Invalid template name: {name!r}; must match ^[a-z0-9_]+$")
    path = PROMPTS_DIR / group / f"{name}.md"
    if not path.is_file():
        raise FileNotFoundError(f"Prompt template file not found: {path}")
    text = path.read_text(encoding="utf-8")
    for k, v in values.items():
        text = text.replace(f"{{{{{k}}}}}", str(v))
    if "{{" in text:
        raise ValueError(f"Unsubstituted template placeholder(s) in {name}.md")
    return text


def _collect_context(root: Path, context_paths: Sequence[str]) -> tuple[list[ContextEntry], list[str]]:
    context_entries: list[ContextEntry] = []
    file_sections: list[str] = []

    for relpath in context_paths:
        if not is_safe_relpath(relpath):
            raise ValueError(f"Unsafe relative path: {relpath!r}")

        target = root / relpath
        try:
            resolved = ensure_within(root, target)
        except (PathError, ValueError) as exc:
            raise ValueError(f"Context path outside worktree: {relpath}") from exc

        if not resolved.is_file():
            raise ValueError(f"Context path is not a regular file: {relpath}")

        raw_bytes = resolved.read_bytes()
        try:
            content = raw_bytes.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError(f"Context file is not valid UTF-8: {relpath}") from exc

        h = hashlib.sha256(raw_bytes).hexdigest().lower()
        context_entries.append(ContextEntry(path=relpath, sha256=f"sha256:{h}"))
        file_sections.append(f"【ファイル：{relpath}】\n{content}")

    return context_entries, file_sections


def build_agy_prompt(
    worktree: Path,
    context_paths: Sequence[str],
    instruction: str,
) -> BuiltPrompt:
    """Build AGY prompt from worktree context files and instruction.

    Args:
        worktree: Path to the worktree root.
        context_paths: Sequence of relative paths inside worktree.
        instruction: Writing instructions.

    Returns:
        BuiltPrompt containing assembled text, ContextEntry tuple, and UTF-8 byte size.

    Raises:
        ValueError: If paths are unsafe/outside/non-regular, UTF-8 decode fails,
                    or instruction is empty/contains NUL.
    """
    if not instruction or not instruction.strip():
        raise ValueError("instruction cannot be empty")
    if "\x00" in instruction:
        raise ValueError("instruction cannot contain NUL bytes")

    context_entries, file_sections = _collect_context(worktree, context_paths)

    parts: list[str] = [PREAMBLE, ""]
    for section in file_sections:
        parts.append(section)
        parts.append("")
    parts.append(f"【指示】\n{instruction}")

    prompt_text = "\n".join(parts)
    size_bytes = len(prompt_text.encode("utf-8"))

    return BuiltPrompt(
        text=prompt_text,
        context=tuple(context_entries),
        size_bytes=size_bytes,
    )


def build_claude_prompt(
    root: Path,
    context_paths: Sequence[str],
    task_text: str,
) -> BuiltPrompt:
    """Build Claude prompt from root directory context files and task_text.

    Args:
        root: Path to repository root or worktree root.
        context_paths: Sequence of relative paths inside root.
        task_text: Task instructions (starts with 【作業】).

    Returns:
        BuiltPrompt containing assembled text, ContextEntry tuple, and UTF-8 byte size.

    Raises:
        ValueError: If paths are unsafe/outside/non-regular, UTF-8 decode fails,
                    or task_text is empty/contains NUL.
    """
    if not task_text or not task_text.strip():
        raise ValueError("task_text cannot be empty")
    if "\x00" in task_text:
        raise ValueError("task_text cannot contain NUL bytes")

    context_entries, file_sections = _collect_context(root, context_paths)

    preamble = load_prompt_template("preamble")
    parts: list[str] = [preamble, ""]
    for section in file_sections:
        parts.append(section)
        parts.append("")
    parts.append(task_text)

    prompt_text = "\n".join(parts)
    size_bytes = len(prompt_text.encode("utf-8"))

    return BuiltPrompt(
        text=prompt_text,
        context=tuple(context_entries),
        size_bytes=size_bytes,
    )

