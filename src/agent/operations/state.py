from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class JobStatus(StrEnum):
    QUEUED = "queued"
    SEARCHING = "searching"
    REVIEWING = "reviewing"
    FINALIZING = "finalizing"
    COMPLETED = "completed"
    BLOCKED = "blocked"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"


TERMINAL_STATUSES = {
    JobStatus.COMPLETED,
    JobStatus.BLOCKED,
    JobStatus.FAILED,
    JobStatus.CANCELLED,
}

ACTIVE_STATUSES = {
    JobStatus.QUEUED,
    JobStatus.SEARCHING,
    JobStatus.REVIEWING,
    JobStatus.FINALIZING,
}

_ALLOWED_TRANSITIONS: dict[JobStatus, set[JobStatus]] = {
    JobStatus.QUEUED: {
        JobStatus.SEARCHING,
        JobStatus.CANCELLED,
        JobStatus.INTERRUPTED,
        JobStatus.FAILED,
    },
    JobStatus.SEARCHING: {
        JobStatus.REVIEWING,
        JobStatus.FINALIZING,
        JobStatus.CANCELLED,
        JobStatus.INTERRUPTED,
        JobStatus.FAILED,
        JobStatus.BLOCKED,
    },
    JobStatus.REVIEWING: {
        JobStatus.SEARCHING,
        JobStatus.FINALIZING,
        JobStatus.CANCELLED,
        JobStatus.INTERRUPTED,
        JobStatus.FAILED,
        JobStatus.BLOCKED,
    },
    JobStatus.FINALIZING: {
        JobStatus.COMPLETED,
        JobStatus.BLOCKED,
        JobStatus.FAILED,
        JobStatus.CANCELLED,
        JobStatus.INTERRUPTED,
    },
    JobStatus.COMPLETED: set(),
    JobStatus.BLOCKED: {
        JobStatus.QUEUED,
        JobStatus.SEARCHING,
        JobStatus.REVIEWING,
        JobStatus.FINALIZING,
    },
    JobStatus.FAILED: {
        JobStatus.QUEUED,
        JobStatus.SEARCHING,
        JobStatus.REVIEWING,
        JobStatus.FINALIZING,
    },
    JobStatus.CANCELLED: {
        JobStatus.QUEUED,
        JobStatus.SEARCHING,
        JobStatus.REVIEWING,
    },
    JobStatus.INTERRUPTED: {
        JobStatus.QUEUED,
        JobStatus.SEARCHING,
        JobStatus.REVIEWING,
        JobStatus.FINALIZING,
        JobStatus.CANCELLED,
        JobStatus.FAILED,
    },
}


class InvalidJobTransition(ValueError):
    def __init__(self, previous: str, requested: str) -> None:
        super().__init__(f"invalid_job_transition:{previous}->{requested}")
        self.previous = previous
        self.requested = requested


@dataclass(frozen=True, slots=True)
class Transition:
    previous: JobStatus
    current: JobStatus
    phase: str
    reason: str
    event_type: str


def coerce_status(value: str | JobStatus) -> JobStatus:
    try:
        return value if isinstance(value, JobStatus) else JobStatus(str(value).lower())
    except ValueError as exc:
        raise InvalidJobTransition("unknown", str(value)) from exc


def validate_transition(
    previous: str | JobStatus,
    requested: str | JobStatus,
    *,
    phase: str,
    reason: str,
    event_type: str,
    allow_same: bool = True,
) -> Transition:
    before = coerce_status(previous)
    after = coerce_status(requested)
    if before == after and allow_same:
        return Transition(before, after, phase, reason, event_type)
    if after not in _ALLOWED_TRANSITIONS[before]:
        raise InvalidJobTransition(before.value, after.value)
    return Transition(before, after, phase, reason, event_type)


def status_group(status: str | JobStatus) -> str:
    current = coerce_status(status)
    if current in ACTIVE_STATUSES:
        return "active"
    if current == JobStatus.COMPLETED:
        return "completed"
    if current == JobStatus.INTERRUPTED:
        return "needs_attention"
    if current in {JobStatus.BLOCKED, JobStatus.FAILED, JobStatus.CANCELLED}:
        return "needs_attention"
    return "unknown"
