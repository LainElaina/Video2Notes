"""In-process Windows-friendly job execution with cooperative cancellation."""

from .manager import (
    JobAlreadyRunningError,
    JobEvent,
    JobEventPage,
    JobEventStore,
    JobManager,
    JobNotFoundError,
    JobSnapshot,
    JobState,
)

__all__ = [
    "JobAlreadyRunningError",
    "JobEvent",
    "JobEventPage",
    "JobEventStore",
    "JobManager",
    "JobNotFoundError",
    "JobSnapshot",
    "JobState",
]
