"""Threaded task manager whose state is safe to expose to the desktop UI."""

from __future__ import annotations

import threading
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field

from video2notes.sources import AcquisitionCancelled, CancellationToken


def utc_now() -> datetime:
    return datetime.now(UTC)


class JobModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class JobState(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class JobEvent(JobModel):
    sequence: int = Field(ge=1)
    run_id: str
    state: JobState
    stage: str
    progress: float | None = Field(default=None, ge=0, le=1)
    message: str | None = None
    metrics: dict[str, float | int | str | bool | None] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)


class JobSnapshot(JobModel):
    run_id: str
    state: JobState
    stage: str
    progress: float | None = Field(default=None, ge=0, le=1)
    message: str | None = None
    error_type: str | None = None
    queued_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    events: list[JobEvent] = Field(default_factory=list)


class EventEmitter(Protocol):
    def __call__(
        self,
        stage: str,
        *,
        progress: float | None = None,
        message: str | None = None,
        metrics: dict[str, float | int | str | bool | None] | None = None,
    ) -> None: ...


JobWorker = Callable[[CancellationToken, EventEmitter], None]


class JobNotFoundError(KeyError):
    pass


class JobAlreadyRunningError(RuntimeError):
    pass


class _JobHandle:
    def __init__(self, run_id: str):
        now = utc_now()
        self.snapshot = JobSnapshot(
            run_id=run_id,
            state=JobState.QUEUED,
            stage="queued",
            queued_at=now,
        )
        self.cancel = CancellationToken()
        self.future: Future[None] | None = None
        self.lock = threading.RLock()
        self.next_sequence = 1


class JobManager:
    """Run CPU/media workers outside the API event loop."""

    def __init__(self, *, max_workers: int = 2, event_history: int = 250):
        if max_workers < 1:
            raise ValueError("max_workers must be positive")
        if event_history < 1:
            raise ValueError("event_history must be positive")
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="video2notes-job",
        )
        self._event_history = event_history
        self._handles: dict[str, _JobHandle] = {}
        self._lock = threading.RLock()

    def submit(self, run_id: str, worker: JobWorker) -> JobSnapshot:
        with self._lock:
            existing = self._handles.get(run_id)
            if existing is not None and existing.snapshot.state in {
                JobState.QUEUED,
                JobState.RUNNING,
            }:
                raise JobAlreadyRunningError(f"job is already running: {run_id}")
            handle = _JobHandle(run_id)
            self._handles[run_id] = handle
            self._append_event(handle, stage="queued")
            handle.future = self._executor.submit(self._execute, handle, worker)
            return handle.snapshot.model_copy(deep=True)

    def get(self, run_id: str, *, after_sequence: int = 0) -> JobSnapshot:
        handle = self._get_handle(run_id)
        with handle.lock:
            snapshot = handle.snapshot.model_copy(deep=True)
        if after_sequence > 0:
            snapshot.events = [
                event for event in snapshot.events if event.sequence > after_sequence
            ]
        return snapshot

    def list(self) -> list[JobSnapshot]:
        with self._lock:
            run_ids = list(self._handles)
        return [self.get(run_id) for run_id in reversed(run_ids)]

    def request_cancel(self, run_id: str) -> JobSnapshot:
        handle = self._get_handle(run_id)
        handle.cancel.cancel()
        with handle.lock:
            if handle.snapshot.state is JobState.QUEUED:
                handle.snapshot.message = "Cancellation requested before start."
            elif handle.snapshot.state is JobState.RUNNING:
                handle.snapshot.message = "Cancellation requested; finishing current operation."
            return handle.snapshot.model_copy(deep=True)

    def shutdown(self, *, wait: bool = True, cancel_pending: bool = False) -> None:
        if cancel_pending:
            with self._lock:
                handles = list(self._handles.values())
            for handle in handles:
                if handle.snapshot.state in {JobState.QUEUED, JobState.RUNNING}:
                    handle.cancel.cancel()
        self._executor.shutdown(wait=wait, cancel_futures=False)

    def _execute(self, handle: _JobHandle, worker: JobWorker) -> None:
        with handle.lock:
            if handle.cancel.is_cancelled:
                self._finish_cancelled(handle, "Cancelled before execution.")
                return
            handle.snapshot.state = JobState.RUNNING
            handle.snapshot.stage = "starting"
            handle.snapshot.started_at = utc_now()
            self._append_event(handle, stage="starting")

        def emit(
            stage: str,
            *,
            progress: float | None = None,
            message: str | None = None,
            metrics: dict[str, float | int | str | bool | None] | None = None,
        ) -> None:
            handle.cancel.raise_if_cancelled()
            with handle.lock:
                if handle.snapshot.state is not JobState.RUNNING:
                    return
                handle.snapshot.stage = stage
                handle.snapshot.progress = progress
                handle.snapshot.message = message
                self._append_event(
                    handle,
                    stage=stage,
                    progress=progress,
                    message=message,
                    metrics=metrics,
                )

        try:
            worker(handle.cancel, emit)
            handle.cancel.raise_if_cancelled()
        except AcquisitionCancelled:
            with handle.lock:
                self._finish_cancelled(handle, "Task cancelled.")
        except Exception as error:
            with handle.lock:
                handle.snapshot.state = JobState.FAILED
                handle.snapshot.stage = "failed"
                handle.snapshot.progress = None
                handle.snapshot.message = "Task failed. Inspect the run stage for details."
                handle.snapshot.error_type = type(error).__name__
                handle.snapshot.finished_at = utc_now()
                self._append_event(
                    handle,
                    stage="failed",
                    message=handle.snapshot.message,
                )
        else:
            with handle.lock:
                handle.snapshot.state = JobState.COMPLETED
                handle.snapshot.stage = "completed"
                handle.snapshot.progress = 1.0
                handle.snapshot.message = "Task completed."
                handle.snapshot.finished_at = utc_now()
                self._append_event(
                    handle,
                    stage="completed",
                    progress=1.0,
                    message=handle.snapshot.message,
                )

    def _finish_cancelled(self, handle: _JobHandle, message: str) -> None:
        handle.snapshot.state = JobState.CANCELLED
        handle.snapshot.stage = "cancelled"
        handle.snapshot.progress = None
        handle.snapshot.message = message
        handle.snapshot.finished_at = utc_now()
        self._append_event(handle, stage="cancelled", message=message)

    def _append_event(
        self,
        handle: _JobHandle,
        *,
        stage: str,
        progress: float | None = None,
        message: str | None = None,
        metrics: dict[str, float | int | str | bool | None] | None = None,
    ) -> None:
        event = JobEvent(
            sequence=handle.next_sequence,
            run_id=handle.snapshot.run_id,
            state=handle.snapshot.state,
            stage=stage,
            progress=progress,
            message=message,
            metrics=metrics or {},
        )
        handle.next_sequence += 1
        handle.snapshot.events.append(event)
        if len(handle.snapshot.events) > self._event_history:
            handle.snapshot.events = handle.snapshot.events[-self._event_history :]

    def _get_handle(self, run_id: str) -> _JobHandle:
        with self._lock:
            handle = self._handles.get(run_id)
        if handle is None:
            raise JobNotFoundError(run_id)
        return handle

    def __enter__(self) -> JobManager:
        return self

    def __exit__(self, *_: Any) -> None:
        self.shutdown()
