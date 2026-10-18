"""Unified audit for protected path changes across commit_all and finalize."""

from __future__ import annotations

from typing import Any, Callable, Mapping, Sequence

from novui.approvals import approval_rel
from novui.paths import is_safe_relpath
from novui.protected import is_protected
from novui.schema import SchemaError, validate_or_raise
from novui.semantics import check_approval
from novui.yamlio import loads_yaml

_APPROVALS_PREFIX = ".novui/approvals/"


def audit_changes(
    changes: Sequence[tuple[str, str]],          # (status, path)。status は A・M・D・T（--no-renames）
    trailers: Mapping[str, Sequence[str]],       # parse_trailers の戻り値
    read_added: Callable[[str], bytes],          # 同じ変更で追加されたファイルの中身を読む
    is_used: Callable[[str], bool],              # approval_id がすでに使われていれば True
    *,
    allow_edit: bool,                            # NovUI-Edit を受け付けるか
) -> list[str]:
    """Audit changes to protected paths.

    Returns a list of violation messages in English (empty list if clean).
    """
    violations: list[str] = []
    protected: list[tuple[str, str]] = []

    # 1. 安全でないパス（is_safe_relpath が False）は違反。
    for status, path in changes:
        if not is_safe_relpath(path):
            violations.append(f"unsafe path {path!r}")
        elif is_protected(path):
            protected.append((status, path))

    # 2. 保護対象の変更が無ければ、ここで空を返す（unsafe path があればそれを返す）。
    if not protected:
        return violations

    # 3. allow_edit が True で、NovUI-Edit の trailer があれば、空を返す（安全でないパスの違反があればそれを返す）。
    if allow_edit and trailers.get("NovUI-Edit"):
        return violations

    # 4. Approval-Id が無ければ違反。
    approvals = trailers.get("Approval-Id", [])
    paths = [p for _, p in protected]
    if not approvals:
        if allow_edit:
            violations.append(f"protected paths changed without an Approval-Id trailer: {paths}")
        else:
            violations.append(
                f"protected paths changed without an Approval-Id trailer "
                f"(NovUI-Edit is not accepted on a job branch): {paths}"
            )
        return violations

    # 5. 各 Approval-Id について検査
    added = {p for s, p in changes if s == "A"}
    targets: set[str] = set()
    records: set[str] = set()
    job_trailers = trailers.get("NovUI-Job", [])

    for approval_id in approvals:
        try:
            rel = approval_rel(approval_id)
        except ValueError:
            violations.append(f"invalid Approval-Id trailer {approval_id!r}")
            continue

        if rel not in added:
            violations.append(f"approval record {rel} was not added in the same commit")
            continue

        try:
            content_bytes = read_added(rel)
            doc = loads_yaml(content_bytes.decode("utf-8"))
            validate_or_raise(doc, "approval")
        except (SchemaError, ValueError, Exception) as exc:
            violations.append(f"approval record {rel} is invalid: {exc}")
            continue

        sem = check_approval(doc)
        if sem:
            violations.append(f"approval record {rel} is invalid: {sem}")
            continue

        if doc.get("approval_id") != approval_id:
            violations.append(f"approval record {rel} has approval_id {doc.get('approval_id')!r}")
            continue

        record_job_id = doc.get("job_id")
        if record_job_id is not None and record_job_id not in job_trailers:
            violations.append(f"approval record {rel} job_id {record_job_id!r} is not a NovUI-Job trailer")

        if is_used(approval_id):
            violations.append(f"approval {approval_id} already exists on main (used)")

        records.add(rel)
        targets.update(doc.get("targets", []))

    # 6. .novui/approvals/ の下の変更は、この変更で追加した有効な記録だけを許す。
    # 保護対象の削除（D）は違反。それ以外の保護対象は、有効な記録の targets のどれかに入っていなければ違反。
    for status, path in protected:
        if path.startswith(_APPROVALS_PREFIX):
            if status != "A" or path not in records:
                violations.append(f"{path} ({status}) is not an approval record of this commit")
        elif status == "D":
            violations.append(f"protected path deleted: {path}")
        elif path not in targets:
            violations.append(f"{path} is not in the targets of the approval records {sorted(records)}")

    return violations
