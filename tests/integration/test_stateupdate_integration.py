"""Integration test: state_update with the real Claude (Phase 2-C2).

AGY and podman are NOT used: the draft and validate steps use the fakes of the unit tests. Only state_update
calls the real Claude. The result is printed (checks, summary, patches) and never adjusted to make it pass.
Run only on Human's instruction:

    NOVUI_INTEGRATION=1 .venv/bin/python -m pytest -s -m integration tests/integration/test_stateupdate_integration.py
"""

import dataclasses
import os
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from test_stateupdate import CH, _branch_head, _wt, rich_setup, validated  # noqa: E402
from test_validatejob import _agy_runner, ok_claude  # noqa: E402

from novui.config import load_settings
from novui.draftjob import run_chapter_draft_job
from novui.gitinspect import get_changes
from novui.proposals import find_proposals, pending_proposal
from novui.stateupdate import run_state_update, show_proposal
from novui.validatejob import run_validate_job
from novui.workrepo import head_commit
from novui.yamlio import dumps_yaml

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("NOVUI_INTEGRATION") != "1",
        reason="Requires NOVUI_INTEGRATION=1 environment variable",
    ),
]


@pytest.fixture(autouse=True)
def mock_container_meta(monkeypatch: pytest.MonkeyPatch) -> None:
    # the draft step uses a fake AGY runner; podman is not available or needed
    monkeypatch.setattr("novui.jobrunner.image_label", lambda img, key: "1.2.14")
    monkeypatch.setattr(
        "novui.jobrunner.image_id",
        lambda img: "sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
    )


def test_integration_state_update_with_real_claude(tmp_path: Path) -> None:
    """state_update only is real: the proposal and every check are printed; nothing is adjusted."""
    settings, work, draft = validated(tmp_path)
    base = load_settings()
    settings = dataclasses.replace(settings, claude_model=base.claude_model, timeouts=base.timeouts)
    repo = work.path
    main_before = head_commit(repo, "main")
    branch_before = _branch_head(work)

    rec = run_state_update(settings, work, CH)

    run = rec.get("run") or {}
    cli = rec.get("cli") or {}
    print(f"\n[SU] state={rec['state']} elapsed={run.get('elapsed_seconds')} exit_code={run.get('exit_code')} "
          f"attempt={rec.get('attempt')}")
    print(f"[SU] claude version={cli.get('version')} model={cli.get('model')} actual_model={cli.get('actual_model')}")
    print(f"[SU] history: {[(h['state'], h['event'], h['reason']) for h in rec['history']]}")
    for c in rec["checks"]:
        print(f"[SU] check {c['name']}: {c['status']} {c['details']}")

    proposals = find_proposals(settings, work.work_key, CH)
    for p in proposals:
        print(f"[SU] proposal {p['job_id']} status={p['status']} branch_head={p['branch_head']}")
        print("[SU] --- summary ---")
        print(dumps_yaml(p["summary"]))
        for e in p["patches"]:
            print(f"[SU] --- patch {e['patch']['target']} ({e['patch_sha256']}) ---")
            print(dumps_yaml(e["patch"]))

    # invariants that hold whatever Claude returns: the job branch and main are never written
    assert head_commit(repo, "main") == main_before
    assert _branch_head(work) == branch_before
    assert get_changes(_wt(draft)) == []
    assert not (settings.data_dir / "claude-cwd" / rec["job_id"]).exists()

    # a problem here is reported as it is; code and prompts are not adjusted to the output
    assert rec["state"] == "WAITING_HUMAN", f"state_update did not reach WAITING_HUMAN: {rec['history'][-1]}"
    pending = pending_proposal(settings, work.work_key, CH)
    assert pending is not None and pending["job_id"] == rec["job_id"]
    assert all(c["status"] != "FAIL" for c in rec["checks"])


# a draft in which C001 learns something new and the relationship with C002 changes (and C001 calls C002 differently)
CHARACTER_CHANGE_TEXT = (
    "<!-- scene: S1 -->\n"
    "カイは港の門の前でミサキと再会した。門番が紋章に目を留めるのを見て、ミサキは声を潜めた。"
    "「その紋章、門番はずっと見張っていたのよ」。カイはそれを初めて知った。\n"
    "カイは隠し事をやめ、銀の指輪をミサキに見せた。二人は仲間から親友になった。"
    "カイはミサキを初めて「相棒」と呼んだ。\n"
    "\n"
    "<!-- scene: S2 -->\n"
    "カイは理由が分からないまま門を通った。\n"
)


@pytest.mark.parametrize("with_changes", [True, False], ids=["changes-key-present", "changes-key-missing"])
def test_integration_state_update_character_changes_with_real_claude(tmp_path: Path, with_changes: bool) -> None:
    """state_update only is real; the draft text makes C001 learn a fact and changes the C001-C002 relationship.

    changes-key-present: relationships[0] has `changes: []` (the normal shape).
    changes-key-missing: relationships[0] has no `changes` key. The prompt (state_patch.md, "対象ファイルに欄がない場合")
    tells Claude not to create absent fields, so the expected result is the same as above: WAITING_HUMAN, no operation
    that creates the absent `changes` field, and the omission explained in the patch reason. The Patch policy is
    unchanged. Information that cannot be written (here the relationship change) stays only in the summary.
    A different real output is reported as it is: nothing (code, prompts, tests) is adjusted to make it pass.
    """
    settings, work = rich_setup(tmp_path, with_changes=with_changes)
    repo = work.path
    draft = run_chapter_draft_job(settings, work, CH, container_runner=_agy_runner(CHARACTER_CHANGE_TEXT))
    assert draft["state"] == "COMPLETED"
    assert run_validate_job(settings, work, CH, claude_runner=ok_claude())["state"] == "COMPLETED"
    base = load_settings()
    settings = dataclasses.replace(settings, claude_model=base.claude_model, timeouts=base.timeouts)
    main_before = head_commit(repo, "main")
    branch_before = _branch_head(work)

    rec = run_state_update(settings, work, CH)

    label = "SU2-present" if with_changes else "SU2-missing"
    run = rec.get("run") or {}
    cli = rec.get("cli") or {}
    print(f"\n[{label}] state={rec['state']} elapsed(last call)={run.get('elapsed_seconds')} attempt={rec.get('attempt')}")
    print(f"[{label}] claude version={cli.get('version')} model={cli.get('model')} actual_model={cli.get('actual_model')}")
    print(f"[{label}] history: {[(h['state'], h['event'], h['reason']) for h in rec['history']]}")
    for c in rec["checks"]:
        print(f"[{label}] check {c['name']}: {c['status']} {c['details']}")

    proposal = show_proposal(settings, work, CH)
    if proposal is not None:
        print(f"[{label}] proposal {proposal['job_id']} status={proposal['status']} targets={[e['patch']['target'] for e in proposal['patches']]}")
        print(f"[{label}] --- summary ---")
        print(dumps_yaml(proposal["summary"]))
        for e in proposal["patches"]:
            print(f"[{label}] --- patch {e['patch']['target']} ({e['patch_sha256']}) ---")
            print(dumps_yaml(e["patch"]))
    else:
        print(f"[{label}] no proposal was stored")

    # invariants that hold whatever Claude returns: the job branch and main are never written
    assert head_commit(repo, "main") == main_before
    assert _branch_head(work) == branch_before
    assert get_changes(_wt(draft)) == []
    assert not (settings.data_dir / "claude-cwd" / rec["job_id"]).exists()

    # expected for both shapes (the prompt tells Claude to omit what the file has no field for)
    assert rec["state"] == "WAITING_HUMAN", f"state_update did not reach WAITING_HUMAN: {rec['history'][-1]}"
    assert all(c["status"] != "FAIL" for c in rec["checks"])
    assert proposal is not None and proposal["status"] == "pending"

    # no operation creates an absent container: the structure of the setting files is never created by the AI
    created = [
        (e["patch"]["target"], op["path"])
        for e in proposal["patches"]
        for op in e["patch"]["operations"]
        if op["op"] != "test" and not op["path"].endswith("/-") and op["path"].rsplit("/", 1)[-1] in ("knowledge", "relationships", "changes")
    ]
    assert created == [], f"operations that create absent fields: {created}"
    if not with_changes:
        # relationships[0] has no `changes`, so no operation may append to it
        assert not any(
            op["path"] == "/relationships/0/changes/-"
            for e in proposal["patches"]
            for op in e["patch"]["operations"]
        )
