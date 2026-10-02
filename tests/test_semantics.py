"""Tests for novui.semantics module."""

import copy
from pathlib import Path
from novui.semantics import (
    check_approval,
    check_chapter,
    check_character,
    check_instruction_routing,
    check_integrity_review,
    check_job_record,
    check_plan,
    check_registry,
    check_requests,
    check_state_patch,
    check_writing_review,
)
from novui.yamlio import load_yaml

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "valid"


def test_check_chapter() -> None:
    doc = load_yaml(FIXTURES_DIR / "chapter.yaml")
    # 正例: review_required=false, review_reasons=[]
    assert check_chapter(doc) == []

    # 違反: review_required=false なのに review_reasons あり
    bad1 = copy.deepcopy(doc)
    bad1["review_reasons"] = [{
        "code": "external_change",
        "detail": "test",
        "at": "2026-10-01T23:00:00+09:00",
    }]
    assert len(check_chapter(bad1)) >= 1

    # 正例: review_required=true, review_reasons=[...]
    good2 = copy.deepcopy(bad1)
    good2["review_required"] = True
    assert check_chapter(good2) == []

    # 違反: review_required=true なのに review_reasons が空
    bad2 = copy.deepcopy(doc)
    bad2["review_required"] = True
    assert len(check_chapter(bad2)) >= 1


def test_check_plan() -> None:
    doc = load_yaml(FIXTURES_DIR / "plan.yaml")
    assert check_plan(doc) == []

    # 違反: min > max
    bad_min_max = copy.deepcopy(doc)
    bad_min_max["target_chars"] = {"min": 5000, "max": 3000}
    assert len(check_plan(bad_min_max)) >= 1

    # 違反: scenes の id 重複
    bad_scenes = copy.deepcopy(doc)
    bad_scenes["scenes"].append(copy.deepcopy(bad_scenes["scenes"][0]))
    assert len(check_plan(bad_scenes)) >= 1

    # 違反: settings に unsafe relpath
    bad_settings = copy.deepcopy(doc)
    bad_settings["context"]["settings"] = ["../escape.md"]
    assert len(check_plan(bad_settings)) >= 1


def test_check_integrity_review() -> None:
    doc = load_yaml(FIXTURES_DIR / "integrity_review.yaml")
    assert check_integrity_review(doc) == []

    # 違反: checks に WARNING があるのに overall が PASS
    bad_overall = copy.deepcopy(doc)
    bad_overall["checks"]["timeline"]["result"] = "WARNING"
    assert len(check_integrity_review(bad_overall)) >= 1

    # 正常: overall も WARNING に更新
    good_warning = copy.deepcopy(bad_overall)
    good_warning["result"] = "WARNING"
    assert check_integrity_review(good_warning) == []

    # 違反: checks の result が PASS なのに STOP finding を含む
    bad_finding = copy.deepcopy(doc)
    bad_finding["checks"]["plot"]["findings"] = [{
        "severity": "STOP",
        "anchor": None,
        "message": "重大な矛盾",
        "evidence": ["e1"],
    }]
    assert len(check_integrity_review(bad_finding)) >= 1


def test_check_writing_review() -> None:
    doc = load_yaml(FIXTURES_DIR / "writing_review.yaml")
    assert check_writing_review(doc) == []

    # 違反: findings の id 重複
    bad = copy.deepcopy(doc)
    bad["findings"].append(copy.deepcopy(bad["findings"][0]))
    assert len(check_writing_review(bad)) >= 1


def test_check_state_patch() -> None:
    doc = load_yaml(FIXTURES_DIR / "state_patch.yaml")
    assert check_state_patch(doc) == []

    # 違反: unsafe relpath
    bad = copy.deepcopy(doc)
    bad["target"] = "/absolute/path.yaml"
    assert len(check_state_patch(bad)) >= 1


def test_check_instruction_routing() -> None:
    doc = load_yaml(FIXTURES_DIR / "instruction_routing.yaml")
    assert check_instruction_routing(doc) == []

    # 違反: questions があるのに proposed_jobs が空でない
    bad_q = copy.deepcopy(doc)
    bad_q["questions"] = ["どのキャラを登場させますか？"]
    assert len(check_instruction_routing(bad_q)) >= 1

    # 正例: questions があり proposed_jobs が空
    good_q = copy.deepcopy(bad_q)
    good_q["proposed_jobs"] = []
    assert check_instruction_routing(good_q) == []

    # 違反: scope_mismatch=True なのに reason が空/null
    bad_mismatch = copy.deepcopy(doc)
    bad_mismatch["scope_mismatch"] = True
    bad_mismatch["scope_mismatch_reason"] = None
    assert len(check_instruction_routing(bad_mismatch)) >= 1

    # 違反: scope_mismatch=False なのに reason が文字列
    bad_no_mismatch = copy.deepcopy(doc)
    bad_no_mismatch["scope_mismatch"] = False
    bad_no_mismatch["scope_mismatch_reason"] = "不整合です"
    assert len(check_instruction_routing(bad_no_mismatch)) >= 1

    # 違反: proposed_jobs[i].depends_on が >= i
    bad_dep = copy.deepcopy(doc)
    bad_dep["proposed_jobs"][0]["depends_on"] = 0  # 0番目が 0 に依存
    assert len(check_instruction_routing(bad_dep)) >= 1

    # 正例: 1番目が 0 に依存
    good_dep = copy.deepcopy(doc)
    good_dep["proposed_jobs"].append({
        "job_type": "draft",
        "summary": "第2ステップ",
        "depends_on": 0,
    })
    assert check_instruction_routing(good_dep) == []


def test_check_approval() -> None:
    doc = load_yaml(FIXTURES_DIR / "approval.yaml")
    assert check_approval(doc) == []

    # 違反: source=instruction なのに instruction_id が null
    bad_inst = copy.deepcopy(doc)
    bad_inst["instruction_id"] = None
    assert len(check_approval(bad_inst)) >= 1

    # 違反: source=proposal なのに job_id が null
    bad_prop = copy.deepcopy(doc)
    bad_prop["source"] = "proposal"
    bad_prop["job_id"] = None
    assert len(check_approval(bad_prop)) >= 1

    # 正例: source=proposal, job_id あり
    good_prop = copy.deepcopy(bad_prop)
    good_prop["job_id"] = "job-001"
    assert check_approval(good_prop) == []

    # 違反: targets に unsafe relpath
    bad_target_unsafe = copy.deepcopy(doc)
    bad_target_unsafe["targets"] = ["../secret.txt"]
    assert len(check_approval(bad_target_unsafe)) >= 1

    # 違反: targets に重複パス
    bad_target_dup = copy.deepcopy(doc)
    bad_target_dup["targets"] = ["settings/world.md", "settings/world.md"]
    assert len(check_approval(bad_target_dup)) >= 1


def test_check_requests() -> None:
    doc = load_yaml(FIXTURES_DIR / "requests.yaml")
    assert check_requests(doc) == []

    # 空リストも正しい
    assert check_requests([]) == []

    # 違反: resolution の request_index が自分自身以降
    bad1 = copy.deepcopy(doc)
    bad1[1]["request_index"] = 1
    assert len(check_requests(bad1)) >= 1

    # 違反: resolution の request_index が範囲外
    bad2 = copy.deepcopy(doc)
    bad2[1]["request_index"] = 99
    assert len(check_requests(bad2)) >= 1

    # 違反: resolution の request_index が resolution を指している
    bad3 = [
        {"type": "request", "job_id": "job-1", "chapter_id": "ch-001", "kind": "question", "target": None, "message": "m"},
        {"type": "resolution", "request_index": 0, "decision": "d1", "resolved_at": "2026-10-01T23:00:00+09:00"},
        {"type": "resolution", "request_index": 1, "decision": "d2", "resolved_at": "2026-10-01T23:01:00+09:00"},
    ]
    assert len(check_requests(bad3)) >= 1


def test_check_job_record() -> None:
    doc = load_yaml(FIXTURES_DIR / "job_record.yaml")
    assert check_job_record(doc) == []

    # 違反: history の最後の state が record.state と不一致
    bad_hist = copy.deepcopy(doc)
    bad_hist["history"].append({
        "state": "RUNNING",
        "event": "LOCK_ACQUIRED",
        "at": "2026-10-01T23:01:00+09:00",
        "reason": None,
    })
    # doc['state'] は QUEUED のまま
    assert len(check_job_record(bad_hist)) >= 1

    # 違反: context path が unsafe
    bad_ctx = copy.deepcopy(doc)
    bad_ctx["context"] = [{
        "path": "../secret.yaml",
        "sha256": "sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
    }]
    assert len(check_job_record(bad_ctx)) >= 1


def test_check_character() -> None:
    doc = load_yaml(FIXTURES_DIR / "character.yaml")
    assert check_character(doc) == []

    # 違反: address に自分の id
    bad_addr = copy.deepcopy(doc)
    bad_addr["address"]["C001"] = "自分"
    assert len(check_character(bad_addr)) >= 1

    # 違反: relationships[].with に自分の id
    bad_rel = copy.deepcopy(doc)
    bad_rel["relationships"].append({"with": "C001", "state": "自問自答"})
    assert len(check_character(bad_rel)) >= 1

    # 違反: knowledge の id 重複
    bad_know = copy.deepcopy(doc)
    bad_know["knowledge"].append({
        "id": "K014",
        "fact": "別の事実",
        "source_chapter": "ch-005",
    })
    assert len(check_character(bad_know)) >= 1


def test_check_registry() -> None:
    doc = load_yaml(FIXTURES_DIR / "registry.yaml")
    assert check_registry(doc) == []

    # 違反: id 重複
    bad_dup = copy.deepcopy(doc)
    dup_item = copy.deepcopy(bad_dup[0])
    dup_item["name"] = "別の紋章"
    bad_dup.append(dup_item)
    assert len(check_registry(bad_dup)) >= 1

    # 違反: status が resolved なのに resolved が null
    bad_res_null = copy.deepcopy(doc)
    bad_res_null[0]["status"] = "resolved"
    bad_res_null[0]["resolved"] = None
    assert len(check_registry(bad_res_null)) >= 1

    # 正例: status が resolved で resolved が設定されている
    good_resolved = copy.deepcopy(doc)
    good_resolved[0]["status"] = "resolved"
    good_resolved[0]["resolved"] = {"chapter": "ch-015", "scene": "S003"}
    assert check_registry(good_resolved) == []

    # 違反: status が active（resolved 以外）なのに resolved が入っている
    bad_active_resolved = copy.deepcopy(doc)
    bad_active_resolved[0]["status"] = "active"
    bad_active_resolved[0]["resolved"] = {"chapter": "ch-015", "scene": "S003"}
    assert len(check_registry(bad_active_resolved)) >= 1
