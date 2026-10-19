"""Impact analysis for setting changes (3-B2, phase3b-design §3.1).

Determines which chapters are affected by setting changes based on pre/post diff
and chapter summaries or plans. Reused across 3-C and 3-D.
"""

from __future__ import annotations

from pathlib import Path
import shutil
import tempfile
from typing import Any, Callable, Mapping, Sequence

from novui.chapters import read_chapter_meta, read_chapters_order
from novui.checks import CheckResult
from novui.config import Settings
from novui.prompt import load_prompt_template
from novui.states import ChapterState
from novui.validatejob import _claude_check
from novui.yamlio import dumps_yaml


def impact_chapters(repo: Path) -> list[tuple[str, str]]:
    """Return chapters to analyze: (chapter_id, "summary" | "plan").

    In chapters-order.yaml order:
    - Chapter with state FINAL and summary.yaml exists -> "summary"
    - Other chapters with plan.yaml exists -> "plan"
    - Neither -> omitted (decision 3). Only main is checked.
    """
    try:
        order = read_chapters_order(repo)
    except Exception:
        return []

    result: list[tuple[str, str]] = []
    for cid in order:
        meta = read_chapter_meta(repo, cid)
        if meta is None:
            continue
        summary_file = repo / f"chapters/{cid}/summary.yaml"
        plan_file = repo / f"chapters/{cid}/plan.yaml"
        if meta.get("state") == ChapterState.FINAL.name and summary_file.is_file():
            result.append((cid, "summary"))
        elif plan_file.is_file():
            result.append((cid, "plan"))
    return result


def analyze_impact(
    settings: Settings,
    *,
    repo: Path,
    instruction: str,
    diff_text: str,
    call: Callable[..., Any],
    post_settings: Mapping[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], list[str], list[CheckResult]]:
    """Run impact_analysis Claude call.

    Returns (affected, notes, checks).
    If no chapters are candidates, returns empty lists with a PASS CheckResult without calling Claude.
    """
    chapters = impact_chapters(repo)
    if not chapters:
        return [], [], [CheckResult("impact_analysis", "PASS", ("no chapter to analyse",))]

    chapters_str = "\n".join(f"* {ch}（{kind}）" for ch, kind in chapters)
    task_text = load_prompt_template(
        "impact_analysis",
        instruction=instruction,
        diff=diff_text,
        chapters=chapters_str,
    )

    from novui.settingchange import setting_context

    # Create temporary directory under settings.data_dir for post-change settings Context
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=settings.data_dir) as temp_dir_str:
        temp_dir = Path(temp_dir_str)

        # Place setting files with post-change content
        for rel in setting_context(repo):
            dst = temp_dir / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            if post_settings is not None and rel in post_settings:
                val = post_settings[rel]
                if isinstance(val, (dict, list)):
                    dst.write_text(dumps_yaml(val), encoding="utf-8")
                else:
                    dst.write_text(str(val), encoding="utf-8")
            else:
                src = repo / rel
                if src.is_file():
                    shutil.copy2(src, dst)

        # Place chapter summary/plan files
        ctx_paths = list(setting_context(repo))
        for ch, kind in chapters:
            rel = f"chapters/{ch}/{kind}.yaml"
            src = repo / rel
            if src.is_file():
                dst = temp_dir / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
                ctx_paths.append(rel)

        outcome = call(
            expected_type="impact_analysis",
            task_text=task_text,
            log_name="impact",
            check_name="claude_impact_analysis",
            root=temp_dir,
            ctx_paths=ctx_paths,
        )

    checks: list[CheckResult] = []
    chk = _claude_check("claude_impact_analysis", outcome)
    checks.append(CheckResult("claude_impact_analysis", chk["status"], tuple(chk["details"])))

    if outcome.data is None:
        return [], [], checks

    data = outcome.data
    affected = list(data.get("affected", []))
    notes = list(data.get("notes", []))

    # Check impact_refs
    valid_cids = {ch for ch, _ in chapters}
    problems: list[str] = []
    seen: set[str] = set()
    for item in affected:
        cid = item.get("chapter_id")
        if cid in seen:
            problems.append(f"duplicate chapter_id: {cid}")
        seen.add(cid)
        if cid not in valid_cids:
            problems.append(f"chapter_id {cid!r} is not an analysed chapter")

    checks.append(CheckResult("impact_refs", "FAIL" if problems else "PASS", tuple(problems)))
    return affected, notes, checks
