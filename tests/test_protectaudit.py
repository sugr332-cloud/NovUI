"""Tests for novui.protectaudit and commit_all protected path enforcement."""

from pathlib import Path
import pytest

from novui.approvals import build_approval
from novui.gitinspect import run_git
from novui.jobrecord import now_iso
from novui.protectaudit import audit_changes
from novui.workrepo import CommitGuardError, commit_all, head_commit
from novui.yamlio import dumps_yaml


def _valid_patch(target: str = "world/setting.yaml") -> dict:
    return {
        "type": "state_patch",
        "target": target,
        "base_hash": "sha256:" + "0" * 64,
        "operations": [{"op": "test", "path": "/id", "value": "test"}],
        "reason": "testing",
    }


def _instruction_approval(approval_id: str, targets: list[str], job_id: str | None = None) -> dict:
    return {
        "approval_id": approval_id,
        "source": "instruction",
        "instruction_id": "INS-0001",
        "job_id": job_id,
        "patch_sha256": "sha256:" + "0" * 64,
        "targets": targets,
        "approved_by": "human",
        "approved_at": now_iso(),
    }


# =====================================================================
# 規則 1〜6 のテスト
# =====================================================================

def test_rule1_unsafe_path() -> None:
    # 不正なパスは違反
    changes = [("M", "../escaped.yaml")]
    v = audit_changes(changes, {}, lambda p: b"", lambda i: False, allow_edit=True)
    assert any("unsafe path '../escaped.yaml'" in msg for msg in v)


def test_rule2_no_protected_changes() -> None:
    # 保護対象以外の変更のみなら空
    changes = [("M", "chapters/ch-001/draft.md"), ("A", "chapters/ch-001/summary.yaml")]
    v = audit_changes(changes, {}, lambda p: b"", lambda i: False, allow_edit=True)
    assert v == []


def test_rule3_allow_edit_with_edit_trailer() -> None:
    # allow_edit=True で NovUI-Edit があれば空
    changes = [("M", "world/setting.yaml")]
    trailers = {"NovUI-Edit": ["human-content"]}
    v = audit_changes(changes, trailers, lambda p: b"", lambda i: False, allow_edit=True)
    assert v == []

    # allow_edit=False では NovUI-Edit があっても通らない
    v_false = audit_changes(changes, trailers, lambda p: b"", lambda i: False, allow_edit=False)
    assert v_false and "NovUI-Edit is not accepted on a job branch" in v_false[0]


def test_rule3_allow_edit_with_unsafe_path() -> None:
    # NovUI-Edit 付きで安全でないパスを含む変更が違反になること
    changes = [("M", "world/setting.yaml"), ("M", "../unsafe.yaml")]
    trailers = {"NovUI-Edit": ["human-content"]}
    v = audit_changes(changes, trailers, lambda p: b"", lambda i: False, allow_edit=True)
    assert len(v) == 1
    assert "unsafe path '../unsafe.yaml'" in v[0]


def test_rule4_no_approval_id() -> None:
    changes = [("M", "world/setting.yaml")]
    # allow_edit=True
    v_true = audit_changes(changes, {}, lambda p: b"", lambda i: False, allow_edit=True)
    assert len(v_true) == 1
    assert "protected paths changed without an Approval-Id trailer: ['world/setting.yaml']" in v_true[0]
    assert "NovUI-Edit is not accepted" not in v_true[0]

    # allow_edit=False
    v_false = audit_changes(changes, {}, lambda p: b"", lambda i: False, allow_edit=False)
    assert len(v_false) == 1
    assert "(NovUI-Edit is not accepted on a job branch)" in v_false[0]


def test_rule5_invalid_approval_id_format() -> None:
    changes = [("M", "world/setting.yaml")]
    trailers = {"Approval-Id": ["A-12"]}  # invalid regex
    v = audit_changes(changes, trailers, lambda p: b"", lambda i: False, allow_edit=True)
    assert any("invalid Approval-Id trailer 'A-12'" in msg for msg in v)


def test_rule5_approval_record_not_added() -> None:
    changes = [("M", "world/setting.yaml"), ("M", ".novui/approvals/A-0001.yaml")]  # M not A
    trailers = {"Approval-Id": ["A-0001"]}
    v = audit_changes(changes, trailers, lambda p: b"", lambda i: False, allow_edit=True)
    assert any("approval record .novui/approvals/A-0001.yaml was not added in the same commit" in msg for msg in v)


def test_rule5_approval_record_invalid_content() -> None:
    changes = [("M", "world/setting.yaml"), ("A", ".novui/approvals/A-0001.yaml")]
    trailers = {"Approval-Id": ["A-0001"]}
    # Corrupt yaml
    v = audit_changes(changes, trailers, lambda p: b"not: yaml: [invalid", lambda i: False, allow_edit=True)
    assert any("approval record .novui/approvals/A-0001.yaml is invalid" in msg for msg in v)


def test_rule5_approval_id_mismatch() -> None:
    changes = [("M", "world/setting.yaml"), ("A", ".novui/approvals/A-0001.yaml")]
    trailers = {"Approval-Id": ["A-0001"]}
    doc = _instruction_approval("A-0002", ["world/setting.yaml"])
    v = audit_changes(changes, trailers, lambda p: dumps_yaml(doc).encode("utf-8"), lambda i: False, allow_edit=True)
    assert any("has approval_id 'A-0002'" in msg for msg in v)


def test_rule5_null_job_id_instruction_allowed() -> None:
    # job_id が null の instruction の記録が通ること
    changes = [("M", "world/setting.yaml"), ("A", ".novui/approvals/A-0001.yaml")]
    trailers = {"Approval-Id": ["A-0001"]}  # NovUI-Job trailer なし
    doc = _instruction_approval("A-0001", ["world/setting.yaml"], job_id=None)
    v = audit_changes(changes, trailers, lambda p: dumps_yaml(doc).encode("utf-8"), lambda i: False, allow_edit=True)
    assert v == []


def test_rule5_non_null_job_id_requires_novui_job_trailer() -> None:
    # null でない記録で NovUI-Job が無いと違反になること
    changes = [("M", "world/setting.yaml"), ("A", ".novui/approvals/A-0001.yaml")]
    trailers = {"Approval-Id": ["A-0001"]}  # No NovUI-Job
    doc = _instruction_approval("A-0001", ["world/setting.yaml"], job_id="job-0100")
    v = audit_changes(changes, trailers, lambda p: dumps_yaml(doc).encode("utf-8"), lambda i: False, allow_edit=True)
    assert any("job_id 'job-0100' is not a NovUI-Job trailer" in msg for msg in v)

    # NovUI-Job があれば通る
    trailers_with_job = {"Approval-Id": ["A-0001"], "NovUI-Job": ["job-0100"]}
    v2 = audit_changes(changes, trailers_with_job, lambda p: dumps_yaml(doc).encode("utf-8"), lambda i: False, allow_edit=True)
    assert v2 == []


def test_rule5_used_approval_id() -> None:
    changes = [("M", "world/setting.yaml"), ("A", ".novui/approvals/A-0001.yaml")]
    trailers = {"Approval-Id": ["A-0001"]}
    doc = _instruction_approval("A-0001", ["world/setting.yaml"])
    v = audit_changes(changes, trailers, lambda p: dumps_yaml(doc).encode("utf-8"), lambda i: True, allow_edit=True)
    assert any("approval A-0001 already exists on main (used)" in msg for msg in v)


def test_rule6_approvals_dir_modifications_and_targets() -> None:
    # .novui/approvals/ の変更は A のみ許す
    changes_mod = [("M", ".novui/approvals/A-0001.yaml")]
    v_mod = audit_changes(changes_mod, {"Approval-Id": ["A-0001"]}, lambda p: b"", lambda i: False, allow_edit=True)
    assert any(".novui/approvals/A-0001.yaml (M) is not an approval record of this commit" in msg for msg in v_mod)

    # 保護対象の削除（D）は違反
    changes_del = [("D", "world/setting.yaml")]
    v_del = audit_changes(changes_del, {"Approval-Id": ["A-0001"]}, lambda p: b"", lambda i: False, allow_edit=True)
    assert any("protected path deleted: world/setting.yaml" in msg for msg in v_del)

    # targets に無い保護対象の変更は違反
    changes_outside = [("M", "characters/C001.yaml"), ("A", ".novui/approvals/A-0001.yaml")]
    doc = _instruction_approval("A-0001", ["world/setting.yaml"])  # C001 is outside targets
    v_out = audit_changes(
        changes_outside,
        {"Approval-Id": ["A-0001"]},
        lambda p: dumps_yaml(doc).encode("utf-8"),
        lambda i: False,
        allow_edit=True,
    )
    assert any("characters/C001.yaml is not in the targets" in msg for msg in v_out)


# =====================================================================
# commit_all のテスト
# =====================================================================

def _setup_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "novel_repo"
    repo.mkdir(parents=True)
    run_git(repo, "init", "-b", "main")
    (repo / "README.md").write_text("novel", encoding="utf-8")
    (repo / "world").mkdir()
    (repo / "world" / "setting.yaml").write_text("original", encoding="utf-8")
    run_git(repo, "add", ".")
    run_git(repo, "-c", "user.name=tester", "-c", "user.email=t@example.com", "commit", "-m", "init")
    return repo


def test_commit_all_main_enforcement(tmp_path: Path) -> None:
    repo = _setup_repo(tmp_path)
    head_before = head_commit(repo, "main")

    # (a) NovUI-Edit 付きの保護対象の変更は通る
    (repo / "world" / "setting.yaml").write_text("human edit", encoding="utf-8")
    c_a = commit_all(
        repo,
        "edit setting",
        [("NovUI-Edit", "human-content")],
        name="tester",
        email="t@example.com",
    )
    assert c_a is not None and c_a != head_before
    assert head_commit(repo, "main") == c_a
    assert (repo / "world" / "setting.yaml").read_text(encoding="utf-8") == "human edit"
    head_before = c_a

    # (b) 有効な記録と Approval-Id の変更は通る
    (repo / "world" / "setting.yaml").write_text("approved change", encoding="utf-8")
    app_doc = _instruction_approval("A-0001", ["world/setting.yaml"])
    app_path = repo / ".novui" / "approvals" / "A-0001.yaml"
    app_path.parent.mkdir(parents=True, exist_ok=True)
    app_path.write_text(dumps_yaml(app_doc), encoding="utf-8")
    c_b = commit_all(
        repo,
        "approved edit",
        [("Approval-Id", "A-0001")],
        name="tester",
        email="t@example.com",
    )
    assert c_b is not None and c_b != head_before
    assert head_commit(repo, "main") == c_b
    head_before = c_b

    # (c) Approval-Id だけで記録が無い -> CommitGuardError, index が空に戻り HEAD が変わらない
    (repo / "world" / "setting.yaml").write_text("change without record", encoding="utf-8")
    with pytest.raises(CommitGuardError, match="was not added in the same commit"):
        commit_all(
            repo,
            "no record",
            [("Approval-Id", "A-0002")],
            name="tester",
            email="t@example.com",
        )
    assert head_commit(repo, "main") == head_before
    # index が空（staged なし）であることを確認
    cached = run_git(repo, "diff", "--cached").decode("utf-8")
    assert cached == ""
    # 作業ツリーをリセット
    run_git(repo, "checkout", "--", "world/setting.yaml")

    # (d) 記録の targets に無いパスを変える -> CommitGuardError
    (repo / "world" / "setting.yaml").write_text("outside target change", encoding="utf-8")
    app_doc2 = _instruction_approval("A-0002", ["characters/C001.yaml"])  # targets does not contain world/setting.yaml
    app_path2 = repo / ".novui" / "approvals" / "A-0002.yaml"
    app_path2.write_text(dumps_yaml(app_doc2), encoding="utf-8")
    with pytest.raises(CommitGuardError, match="not in the targets"):
        commit_all(
            repo,
            "outside target",
            [("Approval-Id", "A-0002")],
            name="tester",
            email="t@example.com",
        )
    assert head_commit(repo, "main") == head_before
    assert run_git(repo, "diff", "--cached").decode("utf-8") == ""
    run_git(repo, "checkout", "--", ".")
    app_path2.unlink()

    # (e) 使用済みの id -> CommitGuardError
    # A-0001 は既に (b) で commit されているため、再利用しようとすると CommitGuardError
    (repo / "world" / "setting.yaml").write_text("reuse A-0001", encoding="utf-8")
    with pytest.raises(CommitGuardError):
        commit_all(
            repo,
            "reuse approval",
            [("Approval-Id", "A-0001")],
            name="tester",
            email="t@example.com",
        )
    assert head_commit(repo, "main") == head_before
    assert run_git(repo, "diff", "--cached").decode("utf-8") == ""


def test_commit_all_used_approval_on_branch(tmp_path: Path) -> None:
    # 作業ブランチで A-0001 を追加しようとしたが、main にすでに A-0001 がある場合
    repo = _setup_repo(tmp_path)
    # main に A-0001 をコミット
    app_doc = _instruction_approval("A-0001", ["world/setting.yaml"])
    app_path = repo / ".novui" / "approvals" / "A-0001.yaml"
    app_path.parent.mkdir(parents=True, exist_ok=True)
    app_path.write_text(dumps_yaml(app_doc), encoding="utf-8")
    commit_all(repo, "add A-0001", [("Approval-Id", "A-0001")], name="tester", email="t@example.com")

    # 別ブランチを作成
    run_git(repo, "checkout", "-b", "feature")
    (repo / "world" / "setting.yaml").write_text("feature edit", encoding="utf-8")
    # feature ブランチでも A-0001.yaml を新しく追加してコミットしようとする
    # (一旦 untrack して新規追加にする)
    run_git(repo, "rm", "-f", ".novui/approvals/A-0001.yaml")
    run_git(repo, "commit", "-m", "temp remove")
    # 再度新規追加
    app_path.parent.mkdir(parents=True, exist_ok=True)
    app_path.write_text(dumps_yaml(app_doc), encoding="utf-8")
    head_before = head_commit(repo, "feature")
    with pytest.raises(CommitGuardError, match="already exists on main"):
        commit_all(
            repo,
            "reuse on branch",
            [("Approval-Id", "A-0001")],
            name="tester",
            email="t@example.com",
        )
    assert head_commit(repo, "feature") == head_before
    assert run_git(repo, "diff", "--cached").decode("utf-8") == ""
