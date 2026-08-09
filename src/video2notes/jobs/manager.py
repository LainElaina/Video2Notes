"""Threaded task manager whose state is safe to expose to the desktop UI."""

from __future__ import annotations

import json
import re
import threading
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field

from video2notes.sources import AcquisitionCancelled, CancellationToken, redact_sensitive

_SENSITIVE_ASSIGNMENT = re.compile(
    r"(?i)\b(api[_ -]?key|authorization|client[_ -]?secret|cookie|password|secret|"
    r"access[_ -]?token|refresh[_ -]?token|session[_ -]?token|csrf[_ -]?token|"
    r"token|sessdata|auth[_ -]?token)"
    r"\b\s*[:=]\s*(?:\"[^\"\r\n]*\"|'[^'\r\n]*'|[^\s,;]+)"
)
_SENSITIVE_METRIC_KEY = re.compile(
    r"(?i)(?:^|[._ -])(?:api.?key|authorization|client.?secret|cookie|password|"
    r"secret|access.?token|refresh.?token|session.?token|csrf.?token|auth.?token|"
    r"sessdata|bili.?jct|guest.?token|ct0|twid)(?:$|[._ -])|^token$"
)
_BEARER_VALUE = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+")
_OPENAI_STYLE_KEY = re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b")


def _default_sanitize_message(message: str) -> str:
    sanitized = redact_sensitive(message)
    sanitized = _SENSITIVE_ASSIGNMENT.sub(
        lambda match: f"{match.group(1)}=<redacted>",
        sanitized,
    )
    sanitized = _BEARER_VALUE.sub("Bearer <redacted>", sanitized)
    return _OPENAI_STYLE_KEY.sub("<redacted-api-key>", sanitized)


def _safe_diagnostic_text(value: str) -> str:
    try:
        return _default_sanitize_message(value)
    except Exception:
        return "Diagnostic text was unavailable."


def _safe_diagnostic_metric(
    key: str,
    value: float | int | str | bool | None,
) -> float | int | str | bool | None:
    if _SENSITIVE_METRIC_KEY.search(key):
        return "<redacted>"
    if isinstance(value, str):
        return _safe_diagnostic_text(value)
    return value


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
    error_type: str | None = None
    metrics: dict[str, float | int | str | bool | None] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)


class JobEventPage(JobModel):
    run_id: str
    events: list[JobEvent] = Field(default_factory=list)
    next_sequence: int = Field(ge=0)
    has_more: bool = False
    log_available: bool = False
    corrupt_line_count: int = Field(default=0, ge=0)


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


class JobEventStore:
    """Append-only, per-run diagnostic events that survive backend restarts."""

    def __init__(
        self,
        runs_root: str | Path,
        *,
        sanitize_message: Callable[[str], str] | None = None,
    ):
        self.runs_root = Path(runs_root).expanduser().resolve()
        self._sanitize_message = sanitize_message or _default_sanitize_message
        self._lock = threading.RLock()

    def append(self, event: JobEvent) -> bool:
        try:
            path = self._event_path(event.run_id)
            if path is None or not path.parent.parent.is_dir():
                return False
            safe = event.model_copy(
                update={
                    "stage": self._sanitize(event.stage),
                    "message": (
                        self._sanitize(event.message) if event.message is not None else None
                    ),
                    "error_type": (
                        self._sanitize(event.error_type) if event.error_type is not None else None
                    ),
                    "metrics": {
                        self._sanitize(key): self._sanitize_metric(key, value)
                        for key, value in event.metrics.items()
                    },
                }
            )
            encoded = (
                json.dumps(
                    safe.model_dump(mode="json"),
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
                + "\n"
            ).encode("utf-8", errors="replace")
            with self._lock:
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open("a+b") as stream:
                    stream.seek(0, 2)
                    if stream.tell() > 0:
                        stream.seek(-1, 2)
                        if stream.read(1) != b"\n":
                            # Preserve a crash-truncated tail as one corrupt line
                            # instead of joining it to the next valid event.
                            stream.write(b"\n")
                    stream.write(encoded)
            return True
        except (OSError, RuntimeError, TypeError, ValueError):
            return False

    def read(
        self,
        run_id: str,
        *,
        after_sequence: int = 0,
        limit: int = 200,
    ) -> JobEventPage:
        if after_sequence < 0:
            raise ValueError("after_sequence cannot be negative")
        if limit < 1:
            raise ValueError("limit must be positive")
        if limit > 500:
            raise ValueError("limit cannot exceed 500")
        path = self._event_path(run_id)
        if path is None or not path.is_file():
            return JobEventPage(
                run_id=run_id,
                next_sequence=after_sequence,
            )

        events: list[JobEvent] = []
        corrupt_line_count = 0
        has_more = False
        last_seen_sequence = 0
        try:
            with (
                self._lock,
                path.open(
                    "r",
                    encoding="utf-8",
                    errors="replace",
                ) as stream,
            ):
                for raw_line in stream:
                    line = raw_line.strip()
                    if not line:
                        continue
                    try:
                        event = JobEvent.model_validate_json(line)
                    except ValueError:
                        corrupt_line_count += 1
                        continue
                    if event.run_id != run_id or event.sequence <= last_seen_sequence:
                        corrupt_line_count += 1
                        continue
                    last_seen_sequence = event.sequence
                    if event.sequence <= after_sequence:
                        continue
                    if len(events) >= limit:
                        has_more = True
                        break
                    events.append(event)
        except (OSError, RuntimeError):
            return JobEventPage(
                run_id=run_id,
                next_sequence=after_sequence,
            )

        return JobEventPage(
            run_id=run_id,
            events=events,
            next_sequence=(events[-1].sequence if events else after_sequence),
            has_more=has_more,
            log_available=True,
            corrupt_line_count=corrupt_line_count,
        )

    def _event_path(self, run_id: str) -> Path | None:
        if not run_id or Path(run_id).name != run_id:
            return None
        try:
            candidate = (self.runs_root / run_id).resolve()
            event_path = (candidate / "logs" / "events.jsonl").resolve()
        except (OSError, RuntimeError, ValueError):
            return None
        if candidate.parent != self.runs_root:
            return None
        if not event_path.is_relative_to(candidate):
            return None
        return event_path

    def _sanitize(self, value: str) -> str:
        try:
            return self._sanitize_message(value)
        except Exception:
            return "Diagnostic text was unavailable."

    def _sanitize_metric(
        self,
        key: str,
        value: float | int | str | bool | None,
    ) -> float | int | str | bool | None:
        if _SENSITIVE_METRIC_KEY.search(key):
            return "<redacted>"
        if not isinstance(value, str):
            return value
        return self._sanitize(value)


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

    def __init__(
        self,
        *,
        max_workers: int = 2,
        event_history: int = 250,
        event_store: JobEventStore | None = None,
    ):
        if max_workers < 1:
            raise ValueError("max_workers must be positive")
        if event_history < 1:
            raise ValueError("event_history must be positive")
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="video2notes-job",
        )
        self._event_history = event_history
        self._event_store = event_store
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
                self._append_event(
                    handle,
                    stage=handle.snapshot.stage,
                    message=handle.snapshot.message,
                )
            elif handle.snapshot.state is JobState.RUNNING:
                handle.snapshot.message = "Cancellation requested; finishing current operation."
                self._append_event(
                    handle,
                    stage=handle.snapshot.stage,
                    message=handle.snapshot.message,
                )
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
                failed_stage = handle.snapshot.stage
                handle.snapshot.state = JobState.FAILED
                handle.snapshot.stage = "failed"
                handle.snapshot.progress = None
                handle.snapshot.message = "Task failed. Inspect the run stage for details."
                handle.snapshot.error_type = type(error).__name__
                handle.snapshot.finished_at = utc_now()
                self._append_event(
                    handle,
                    stage=failed_stage,
                    message=f"{type(error).__name__}: {error}",
                    error_type=type(error).__name__,
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
        error_type: str | None = None,
        metrics: dict[str, float | int | str | bool | None] | None = None,
    ) -> None:
        safe_stage = _safe_diagnostic_text(stage)
        safe_message = _safe_diagnostic_text(message) if message is not None else None
        safe_error_type = (
            _safe_diagnostic_text(error_type) if error_type is not None else None
        )
        safe_metrics = {
            _safe_diagnostic_text(key): _safe_diagnostic_metric(key, value)
            for key, value in (metrics or {}).items()
        }
        event = JobEvent(
            sequence=handle.next_sequence,
            run_id=handle.snapshot.run_id,
            state=handle.snapshot.state,
            stage=safe_stage,
            progress=progress,
            message=safe_message,
            error_type=safe_error_type,
            metrics=safe_metrics,
        )
        handle.next_sequence += 1
        handle.snapshot.events.append(event)
        if self._event_store is not None:
            self._event_store.append(event)
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
