"""Tests for novui.states module."""

import pytest

from novui.states import (
    CHAPTER_TRANSITIONS,
    JOB_TRANSITIONS,
    TERMINAL_JOB_STATES,
    ChapterEvent,
    ChapterState,
    InvalidTransition,
    JobEvent,
    JobState,
    next_chapter_state,
    next_job_state,
)


# 1-A 指示書（docs/phase1/agy-instruction-1a.md）からリテラルで書き写した期待表
EXPECTED_CHAPTER_TRANSITIONS: dict[tuple[ChapterState | None, ChapterEvent], ChapterState] = {
    (None, ChapterEvent.OUTLINE_CREATED): ChapterState.OUTLINED,
    (ChapterState.OUTLINED, ChapterEvent.PLAN_WRITTEN): ChapterState.PLANNED,
    (ChapterState.PLANNED, ChapterEvent.PLAN_APPROVED): ChapterState.PLAN_APPROVED,
    (ChapterState.PLANNED, ChapterEvent.PLAN_REJECTED): ChapterState.OUTLINED,
    (ChapterState.PLAN_APPROVED, ChapterEvent.DRAFT_COMPLETED): ChapterState.DRAFTED,
    (ChapterState.DRAFTED, ChapterEvent.VALIDATION_ACCEPTED): ChapterState.AI_VALIDATED,
    (ChapterState.DRAFTED, ChapterEvent.VALIDATION_SKIPPED): ChapterState.AI_VALIDATED,
    (ChapterState.AI_VALIDATED, ChapterEvent.STATE_PATCH_APPROVED): ChapterState.HUMAN_APPROVED,
    (ChapterState.HUMAN_APPROVED, ChapterEvent.FINAL_APPROVED): ChapterState.FINAL,
}
for _st in (
    ChapterState.PLAN_APPROVED,
    ChapterState.DRAFTED,
    ChapterState.AI_VALIDATED,
    ChapterState.HUMAN_APPROVED,
    ChapterState.FINAL,
):
    EXPECTED_CHAPTER_TRANSITIONS[(_st, ChapterEvent.PLAN_REVISED)] = ChapterState.PLANNED

for _st in (
    ChapterState.DRAFTED,
    ChapterState.AI_VALIDATED,
    ChapterState.HUMAN_APPROVED,
    ChapterState.FINAL,
):
    EXPECTED_CHAPTER_TRANSITIONS[(_st, ChapterEvent.CONTENT_CHANGED)] = ChapterState.DRAFTED

for _st in ChapterState:
    EXPECTED_CHAPTER_TRANSITIONS[(_st, ChapterEvent.TYPO_FIXED)] = _st

SPECIAL_CHECK_PASSED = "VALIDATING_OR_COMPLETED"

EXPECTED_JOB_TRANSITIONS: dict[tuple[JobState, JobEvent], JobState | str] = {
    (JobState.QUEUED, JobEvent.LOCK_ACQUIRED): JobState.RUNNING,
    (JobState.QUEUED, JobEvent.NEEDS_HUMAN_INPUT): JobState.WAITING_HUMAN,
    (JobState.QUEUED, JobEvent.HUMAN_CANCEL): JobState.CANCELLED,
    (JobState.QUEUED, JobEvent.CONTROLLER_RESTARTED): JobState.STOPPED,

    (JobState.RUNNING, JobEvent.CLI_EXITED): JobState.CHECKING,
    (JobState.RUNNING, JobEvent.TIMED_OUT): JobState.FAILED,
    (JobState.RUNNING, JobEvent.HUMAN_STOP): JobState.STOPPED,
    (JobState.RUNNING, JobEvent.CONTROLLER_RESTARTED): JobState.STOPPED,

    (JobState.CHECKING, JobEvent.CHECK_PASSED): SPECIAL_CHECK_PASSED,
    (JobState.CHECKING, JobEvent.CHECK_FAILED): JobState.FAILED,
    (JobState.CHECKING, JobEvent.NEEDS_HUMAN_INPUT): JobState.WAITING_HUMAN,
    (JobState.CHECKING, JobEvent.CONTROLLER_RESTARTED): JobState.STOPPED,

    (JobState.VALIDATING, JobEvent.VALIDATION_PASSED): JobState.COMPLETED,
    (JobState.VALIDATING, JobEvent.VALIDATION_NEEDS_HUMAN): JobState.WAITING_HUMAN,
    (JobState.VALIDATING, JobEvent.HUMAN_STOP): JobState.STOPPED,
    (JobState.VALIDATING, JobEvent.CONTROLLER_RESTARTED): JobState.STOPPED,

    (JobState.WAITING_HUMAN, JobEvent.HUMAN_CONTINUE): JobState.COMPLETED,
    (JobState.WAITING_HUMAN, JobEvent.HUMAN_OVERRIDE): JobState.COMPLETED,
    (JobState.WAITING_HUMAN, JobEvent.HUMAN_REQUEST_FIX): JobState.COMPLETED,
    (JobState.WAITING_HUMAN, JobEvent.HUMAN_CANCEL): JobState.CANCELLED,
}


def test_transitions_keys_match_spec_literal() -> None:
    # 期待表のキー集合とモジュールのキー集合が完全一致することを検証
    assert set(CHAPTER_TRANSITIONS.keys()) == set(EXPECTED_CHAPTER_TRANSITIONS.keys())
    assert set(JOB_TRANSITIONS.keys()) == set(EXPECTED_JOB_TRANSITIONS.keys())


def test_chapter_transitions_table_rows() -> None:
    # 期待表の全行について next_chapter_state の戻り値が期待値と一致することを検証
    for (current, event), expected in EXPECTED_CHAPTER_TRANSITIONS.items():
        assert next_chapter_state(current, event) == expected


def test_all_chapter_combinations_exhaustive() -> None:
    # 全状態（None含む）× 全イベント の組合せを列挙
    all_states: list[ChapterState | None] = [None] + list(ChapterState)
    for st in all_states:
        for ev in ChapterEvent:
            key = (st, ev)
            if key in CHAPTER_TRANSITIONS:
                # 表にある場合は正常遷移
                res = next_chapter_state(st, ev)
                assert res == CHAPTER_TRANSITIONS[key]
            else:
                # 表にないものはすべて InvalidTransition
                with pytest.raises(InvalidTransition):
                    next_chapter_state(st, ev)


def test_chapter_final_approved_review_required() -> None:
    # review_required=False なら FINAL
    assert next_chapter_state(ChapterState.HUMAN_APPROVED, ChapterEvent.FINAL_APPROVED, review_required=False) == ChapterState.FINAL
    # review_required=True なら InvalidTransition
    with pytest.raises(InvalidTransition):
        next_chapter_state(ChapterState.HUMAN_APPROVED, ChapterEvent.FINAL_APPROVED, review_required=True)


def test_job_transitions_table_rows() -> None:
    # 期待表の全行について next_job_state の戻り値が期待値と一致することを検証
    for (current, event), expected in EXPECTED_JOB_TRANSITIONS.items():
        if expected == SPECIAL_CHECK_PASSED:
            assert next_job_state(current, event, needs_validation=True) == JobState.VALIDATING
            assert next_job_state(current, event, needs_validation=False) == JobState.COMPLETED
        elif event in (JobEvent.HUMAN_OVERRIDE, JobEvent.HUMAN_REQUEST_FIX):
            assert next_job_state(current, event, reason="test reason") == JobState.COMPLETED
        else:
            assert next_job_state(current, event) == expected


def test_all_job_combinations_exhaustive() -> None:
    # 全状態 × 全イベント の組合せを列挙
    for st in JobState:
        for ev in JobEvent:
            key = (st, ev)
            if st in TERMINAL_JOB_STATES or key not in JOB_TRANSITIONS:
                with pytest.raises(InvalidTransition):
                    next_job_state(
                        st,
                        ev,
                        needs_validation=False,
                        reason="valid reason",
                    )
            else:
                # 表にある組合せ
                res = next_job_state(
                    st,
                    ev,
                    needs_validation=False,
                    reason="valid reason",
                )
                assert isinstance(res, JobState)


def test_job_check_passed_requires_needs_validation() -> None:
    with pytest.raises(ValueError):
        next_job_state(JobState.CHECKING, JobEvent.CHECK_PASSED, needs_validation=None)


def test_job_human_override_request_fix_requires_reason() -> None:
    for ev in (JobEvent.HUMAN_OVERRIDE, JobEvent.HUMAN_REQUEST_FIX):
        with pytest.raises(ValueError):
            next_job_state(JobState.WAITING_HUMAN, ev, reason=None)
        with pytest.raises(ValueError):
            next_job_state(JobState.WAITING_HUMAN, ev, reason="   ")


def test_job_failed_human_override_rejected() -> None:
    # 機械的な安全違反は OVERRIDE で解除できない
    with pytest.raises(InvalidTransition):
        next_job_state(JobState.FAILED, JobEvent.HUMAN_OVERRIDE, reason="trying to bypass")


def test_job_waiting_human_controller_restarted_rejected() -> None:
    # WAITING_HUMAN に CONTROLLER_RESTARTED は InvalidTransition
    with pytest.raises(InvalidTransition):
        next_job_state(JobState.WAITING_HUMAN, JobEvent.CONTROLLER_RESTARTED)
