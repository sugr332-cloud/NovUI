"""State machines for Chapters and Jobs."""

from enum import Enum


class InvalidTransition(Exception):
    """Raised when a requested transition is not allowed."""


class ChapterState(Enum):
    OUTLINED = "OUTLINED"
    PLANNED = "PLANNED"
    PLAN_APPROVED = "PLAN_APPROVED"
    DRAFTED = "DRAFTED"
    AI_VALIDATED = "AI_VALIDATED"
    HUMAN_APPROVED = "HUMAN_APPROVED"
    FINAL = "FINAL"


class ChapterEvent(Enum):
    OUTLINE_CREATED = "OUTLINE_CREATED"
    PLAN_WRITTEN = "PLAN_WRITTEN"
    PLAN_APPROVED = "PLAN_APPROVED"
    PLAN_REJECTED = "PLAN_REJECTED"
    DRAFT_COMPLETED = "DRAFT_COMPLETED"
    VALIDATION_ACCEPTED = "VALIDATION_ACCEPTED"
    VALIDATION_SKIPPED = "VALIDATION_SKIPPED"
    STATE_PATCH_APPROVED = "STATE_PATCH_APPROVED"
    FINAL_APPROVED = "FINAL_APPROVED"
    PLAN_REVISED = "PLAN_REVISED"
    CONTENT_CHANGED = "CONTENT_CHANGED"
    TYPO_FIXED = "TYPO_FIXED"


CHAPTER_TRANSITIONS: dict[tuple[ChapterState | None, ChapterEvent], ChapterState] = {
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
    CHAPTER_TRANSITIONS[(_st, ChapterEvent.PLAN_REVISED)] = ChapterState.PLANNED

for _st in (
    ChapterState.DRAFTED,
    ChapterState.AI_VALIDATED,
    ChapterState.HUMAN_APPROVED,
    ChapterState.FINAL,
):
    CHAPTER_TRANSITIONS[(_st, ChapterEvent.CONTENT_CHANGED)] = ChapterState.DRAFTED

for _st in ChapterState:
    CHAPTER_TRANSITIONS[(_st, ChapterEvent.TYPO_FIXED)] = _st


def next_chapter_state(
    current: ChapterState | None,
    event: ChapterEvent,
    *,
    review_required: bool = False,
) -> ChapterState:
    """Compute next chapter state based on current state and event."""
    key = (current, event)
    if key not in CHAPTER_TRANSITIONS:
        raise InvalidTransition(f"Invalid chapter transition: {current} on {event}")
    if current == ChapterState.HUMAN_APPROVED and event == ChapterEvent.FINAL_APPROVED:
        if review_required:
            raise InvalidTransition("Cannot transition to FINAL when review_required is True")
    return CHAPTER_TRANSITIONS[key]


class JobState(Enum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    CHECKING = "CHECKING"
    VALIDATING = "VALIDATING"
    WAITING_HUMAN = "WAITING_HUMAN"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    STOPPED = "STOPPED"
    CANCELLED = "CANCELLED"


class JobEvent(Enum):
    LOCK_ACQUIRED = "LOCK_ACQUIRED"
    CLI_EXITED = "CLI_EXITED"
    TIMED_OUT = "TIMED_OUT"
    HUMAN_STOP = "HUMAN_STOP"
    CHECK_PASSED = "CHECK_PASSED"
    CHECK_FAILED = "CHECK_FAILED"
    VALIDATION_PASSED = "VALIDATION_PASSED"
    VALIDATION_NEEDS_HUMAN = "VALIDATION_NEEDS_HUMAN"
    NEEDS_HUMAN_INPUT = "NEEDS_HUMAN_INPUT"
    HUMAN_CONTINUE = "HUMAN_CONTINUE"
    HUMAN_OVERRIDE = "HUMAN_OVERRIDE"
    HUMAN_REQUEST_FIX = "HUMAN_REQUEST_FIX"
    HUMAN_CANCEL = "HUMAN_CANCEL"
    CONTROLLER_RESTARTED = "CONTROLLER_RESTARTED"


TERMINAL_JOB_STATES: frozenset[JobState] = frozenset({
    JobState.COMPLETED,
    JobState.FAILED,
    JobState.STOPPED,
    JobState.CANCELLED,
})


JOB_TRANSITIONS: dict[tuple[JobState, JobEvent], JobState | str] = {
    (JobState.QUEUED, JobEvent.LOCK_ACQUIRED): JobState.RUNNING,
    (JobState.QUEUED, JobEvent.NEEDS_HUMAN_INPUT): JobState.WAITING_HUMAN,
    (JobState.QUEUED, JobEvent.HUMAN_CANCEL): JobState.CANCELLED,
    (JobState.QUEUED, JobEvent.CONTROLLER_RESTARTED): JobState.STOPPED,
    (JobState.RUNNING, JobEvent.CLI_EXITED): JobState.CHECKING,
    (JobState.RUNNING, JobEvent.TIMED_OUT): JobState.FAILED,
    (JobState.RUNNING, JobEvent.HUMAN_STOP): JobState.STOPPED,
    (JobState.RUNNING, JobEvent.CONTROLLER_RESTARTED): JobState.STOPPED,
    (JobState.CHECKING, JobEvent.CHECK_PASSED): "CONDITIONAL_VALIDATING_OR_COMPLETED",
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


def next_job_state(
    current: JobState,
    event: JobEvent,
    *,
    needs_validation: bool | None = None,
    reason: str | None = None,
) -> JobState:
    """Compute next job state based on current state and event."""
    if current in TERMINAL_JOB_STATES:
        raise InvalidTransition(f"Job is already in terminal state: {current}")
    key = (current, event)
    if key not in JOB_TRANSITIONS:
        raise InvalidTransition(f"Invalid job transition: {current} on {event}")

    if event == JobEvent.CHECK_PASSED:
        if needs_validation is None:
            raise ValueError("needs_validation must be provided (True or False) on CHECK_PASSED")
        return JobState.VALIDATING if needs_validation else JobState.COMPLETED

    if event in (JobEvent.HUMAN_OVERRIDE, JobEvent.HUMAN_REQUEST_FIX):
        if reason is None or not reason.strip():
            raise ValueError(f"reason must be provided and non-empty for {event.name}")
        return JobState.COMPLETED

    target = JOB_TRANSITIONS[key]
    assert isinstance(target, JobState)
    return target
