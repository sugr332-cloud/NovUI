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


def test_chapter_transitions_table_rows() -> None:
    # 遷移表の全行が期待どおりの次状態になることを確認
    for (current, event), expected in CHAPTER_TRANSITIONS.items():
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
    # 遷移表の全行が期待どおりの次状態になることを確認
    for (current, event), expected in JOB_TRANSITIONS.items():
        if event == JobEvent.CHECK_PASSED:
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
