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

from test_stateupdate import CH, _branch_head, _wt, validated  # noqa: E402

from novui.config import load_settings
from novui.gitinspect import get_changes
from novui.proposals import find_proposals, pending_proposal
from novui.stateupdate import run_state_update
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
