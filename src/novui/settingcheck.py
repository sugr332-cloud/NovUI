"""Checks for setting_change patches and results (3-B2).

Includes policy check for setting_patch and comprehensive result check against schemas,
semantics, foreshadow order/positions, and script format flag conditions.
"""

from __future__ import annotations

from pathlib import Path
import re
from typing import Any, Mapping, Sequence

from novui.chapters import ChapterError, read_chapters_order
from novui.checks import CheckResult
from novui.flagcheck import check_new_flag_conditions, flag_check_args
from novui.foreshadowcheck import (
    check_foreshadow_positions,
    foreshadow_order_issues,
    load_registry,
)
from novui.schema import validate
from novui.semantics import SEMANTIC_CHECKS
from novui.statepatch import PatchError, parse_pointer
from novui.textformat import read_text_format
from novui.yamlio import load_yaml

REGISTRY_TARGET = "foreshadowing/registry.yaml"
_CHAR_TARGET_RE = re.compile(r"^characters/[^/]+\.yaml$")
_INDEX_RE = re.compile(r"^(0|[1-9][0-9]*)$")
_ALLOWED_OPS = frozenset({"add", "replace", "remove", "test"})


def is_setting_target(repo: Path, target: str) -> bool:
    """Return True if target is an allowed setting target and exists in repo."""
    if target == REGISTRY_TARGET:
        return (repo / target).is_file()
    if _CHAR_TARGET_RE.match(target):
        return (repo / target).is_file()
    return False


def check_setting_patch_policy(
    patch: Mapping[str, Any],
    doc_before: Any,
    *,
    target: str,
    new_foreshadow_ids: Sequence[str],
) -> CheckResult:
    """Verify that operations of a setting_patch comply with policy (phase3b-design §3.2)."""
    is_char = bool(_CHAR_TARGET_RE.match(target))
    is_reg = target == REGISTRY_TARGET

    if not is_char and not is_reg:
        return CheckResult("setting_patch_policy", "FAIL", (f"target {target!r} is not an allowed setting target",))

    ops = patch.get("operations", [])
    errs: list[str] = []
    added_reg_ids: list[str] = []

    for i, op in enumerate(ops):
        label = f"operations[{i}]"
        op_name = op.get("op")
        if op_name not in _ALLOWED_OPS:
            errs.append(f"{label}: op {op_name!r} is not allowed (add, replace, remove, test only)")
            continue

        path = op.get("path")
        if not isinstance(path, str):
            errs.append(f"{label}: path must be a string")
            continue

        try:
            toks = parse_pointer(path)
        except PatchError as exc:
            errs.append(f"{label}: invalid pointer {path!r}: {exc}")
            continue

        if is_char:
            # 人物：/id への replace・remove、空 path（文書全体）への replace・remove は FAIL
            if path in ("", "/"):
                if op_name in ("replace", "remove"):
                    errs.append(f"{label}: {op_name} of the whole document is not allowed")
            elif path == "/id" or toks == ["id"]:
                if op_name in ("replace", "remove"):
                    errs.append(f"{label}: {op_name} of /id is not allowed")

        elif is_reg:
            # 伏線：
            # トップレベルの要素（/<添字>）の remove・replace は FAIL
            if len(toks) == 1 and _INDEX_RE.match(toks[0]):
                if op_name in ("replace", "remove"):
                    errs.append(f"{label}: {op_name} of top-level item /{toks[0]} is not allowed")
            # 既存の要素の /<添字>/id の replace・remove は FAIL
            elif len(toks) == 2 and _INDEX_RE.match(toks[0]) and toks[1] == "id":
                if op_name in ("replace", "remove"):
                    errs.append(f"{label}: {op_name} of /{toks[0]}/id is not allowed")

            # トップレベルへの add は /- だけで、value の id は new_foreshadow_ids を順に使っていなければ FAIL
            if op_name == "add" and len(toks) == 1:
                if toks[0] != "-":
                    errs.append(f"{label}: add to /{toks[0]} is not allowed (top-level add must be to /-)")
                else:
                    val = op.get("value")
                    if isinstance(val, Mapping) and "id" in val and isinstance(val["id"], str):
                        added_reg_ids.append(val["id"])
                    else:
                        errs.append(f"{label}: added foreshadowing must be a mapping with a string 'id'")

    if is_reg:
        if added_reg_ids != list(new_foreshadow_ids):
            errs.append(
                f"added foreshadowing IDs {added_reg_ids} do not match expected sequence {list(new_foreshadow_ids)}"
            )

    if errs:
        return CheckResult("setting_patch_policy", "FAIL", tuple(errs))
    return CheckResult("setting_patch_policy", "PASS", ())


def check_setting_result(
    repo: Path,
    target: str,
    doc_before: Any,
    doc_after: Any,
) -> list[CheckResult]:
    """Check post-patch setting document against schema, semantics, and mechanical consistency."""
    results: list[CheckResult] = []

    # 1. setting_schema
    is_char = bool(_CHAR_TARGET_RE.match(target))
    schema_name = "character" if is_char else "registry"

    schema_errs = [f"schema: {e}" for e in validate(doc_after, schema_name)]
    sem_fn = SEMANTIC_CHECKS.get(schema_name)
    if sem_fn:
        schema_errs += [f"semantics: {e}" for e in sem_fn(doc_after)]

    if is_char and isinstance(doc_after, Mapping):
        expected_id = target.rsplit("/", 1)[-1].removesuffix(".yaml")
        if doc_after.get("id") != expected_id:
            schema_errs.append(f"character id {doc_after.get('id')!r} does not match filename {expected_id!r}")

    results.append(
        CheckResult("setting_schema", "FAIL" if schema_errs else "PASS", tuple(schema_errs))
    )

    # 2. 伏線のとき: foreshadow_positions, foreshadow_order
    if target == REGISTRY_TARGET:
        try:
            order = read_chapters_order(repo)
        except Exception as exc:
            results.append(CheckResult("foreshadow_positions", "WARNING", (f"cannot read chapters-order.yaml: {exc}",)))
            results.append(CheckResult("foreshadow_order", "WARNING", (f"cannot read chapters-order.yaml: {exc}",)))
        else:
            pos_before = check_foreshadow_positions(order, doc_before)
            pos_after = check_foreshadow_positions(order, doc_after)
            old_pos_details = set(pos_before.details)
            new_pos = [d for d in pos_after.details if d not in old_pos_details]
            results.append(
                CheckResult("foreshadow_positions", "WARNING" if new_pos else "PASS", tuple(new_pos))
            )

            ord_before = set(foreshadow_order_issues(order, doc_before))
            ord_after = foreshadow_order_issues(order, doc_after)
            new_ord = [d for d in ord_after if d not in ord_before]
            results.append(
                CheckResult("foreshadow_order", "WARNING" if new_ord else "PASS", tuple(new_ord))
            )

    # 3. 台本の形式のとき: flag_conditions
    try:
        fmt = read_text_format(repo)
    except Exception:
        fmt = "novel"

    if fmt == "script":
        try:
            order = read_chapters_order(repo)
            if not order:
                raise ChapterError("chapters-order.yaml is empty")
            last_ch = order[-1]
            plan_path = repo / f"chapters/{last_ch}/plan.yaml"
            plan = load_yaml(plan_path) if plan_path.is_file() else {"scenes": []}
            kw = flag_check_args(repo, order, last_ch, plan)

            chars_dir = repo / "characters"
            all_chars: dict[str, Any] = {}
            if chars_dir.is_dir():
                for p in sorted(chars_dir.glob("*.yaml")):
                    if p.is_file():
                        all_chars[p.stem] = load_yaml(p)

            if target == REGISTRY_TARGET:
                before = (all_chars, doc_before)
                after = (all_chars, doc_after)
            else:
                cid = target.rsplit("/", 1)[-1].removesuffix(".yaml")
                chars_before = dict(all_chars)
                chars_after = dict(all_chars)
                chars_before[cid] = doc_before
                chars_after[cid] = doc_after
                reg = load_registry(repo)
                before = (chars_before, reg)
                after = (chars_after, reg)

            flag_res = check_new_flag_conditions(before, after, **kw)
            results.append(flag_res)
        except Exception:
            # 渡し方が決められないかエラー時は入れずに進める
            pass

    return results
