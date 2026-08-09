from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path

from video2notes.jobs import (
    JobAlreadyRunningError,
    JobEvent,
    JobEventStore,
    JobManager,
    JobState,
)


class JobManagerTests(unittest.TestCase):
    def test_worker_progress_and_completion_are_ordered(self) -> None:
        done = threading.Event()

        def worker(cancel, emit) -> None:
            cancel.raise_if_cancelled()
            emit("probe", progress=0.2, metrics={"duration_us": 1_000_000})
            emit("render", progress=0.9)
            done.set()

        with JobManager(max_workers=1) as manager:
            manager.submit("run-1", worker)
            self.assertTrue(done.wait(timeout=2))
            future = manager._handles["run-1"].future
            self.assertIsNotNone(future)
            future.result(timeout=2)
            snapshot = manager.get("run-1")

        self.assertEqual(snapshot.state, JobState.COMPLETED)
        self.assertEqual(snapshot.progress, 1.0)
        self.assertEqual(
            [item.sequence for item in snapshot.events],
            list(range(1, len(snapshot.events) + 1)),
        )
        self.assertIn("probe", [item.stage for item in snapshot.events])

    def test_cancel_is_cooperative_and_does_not_expose_exception_text(self) -> None:
        entered = threading.Event()
        release = threading.Event()

        def worker(cancel, emit) -> None:
            entered.set()
            release.wait(timeout=2)
            emit("next")
            cancel.raise_if_cancelled()

        with JobManager(max_workers=1) as manager:
            manager.submit("run-cancel", worker)
            self.assertTrue(entered.wait(timeout=2))
            manager.request_cancel("run-cancel")
            release.set()
            future = manager._handles["run-cancel"].future
            self.assertIsNotNone(future)
            future.result(timeout=2)
            snapshot = manager.get("run-cancel")

        self.assertEqual(snapshot.state, JobState.CANCELLED)
        self.assertIsNone(snapshot.error_type)

    def test_failure_exposes_type_but_not_sensitive_message(self) -> None:
        def worker(cancel, emit) -> None:
            del cancel, emit
            raise RuntimeError("token=private")

        with JobManager(max_workers=1) as manager:
            manager.submit("run-fail", worker)
            future = manager._handles["run-fail"].future
            self.assertIsNotNone(future)
            future.result(timeout=2)
            snapshot = manager.get("run-fail")

        self.assertEqual(snapshot.state, JobState.FAILED)
        self.assertEqual(snapshot.error_type, "RuntimeError")
        self.assertNotIn("private", snapshot.message or "")
        self.assertNotIn("private", str(snapshot.events))

    def test_duplicate_active_run_is_rejected(self) -> None:
        entered = threading.Event()
        release = threading.Event()

        def worker(cancel, emit) -> None:
            del cancel, emit
            entered.set()
            release.wait(timeout=2)

        with JobManager(max_workers=1) as manager:
            manager.submit("same", worker)
            self.assertTrue(entered.wait(timeout=2))
            with self.assertRaises(JobAlreadyRunningError):
                manager.submit("same", worker)
            release.set()

    def test_events_are_persisted_paginated_and_survive_a_new_store(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            runs_root = Path(temporary) / "runs"
            run_root = runs_root / "persisted-run"
            (run_root / "logs").mkdir(parents=True)
            store = JobEventStore(runs_root)

            def worker(cancel, emit) -> None:
                cancel.raise_if_cancelled()
                emit(
                    "probe",
                    progress=0.5,
                    message="token=private-value",
                    metrics={
                        "detail": "Bearer private-value",
                        "api_key": "plain-private-value",
                        "token": 123456,
                        "token_count": 42,
                    },
                )

            with JobManager(max_workers=1, event_store=store) as manager:
                manager.submit("persisted-run", worker)
                future = manager._handles["persisted-run"].future
                self.assertIsNotNone(future)
                future.result(timeout=2)

            first = JobEventStore(runs_root).read("persisted-run", limit=2)
            self.assertEqual([event.sequence for event in first.events], [1, 2])
            self.assertTrue(first.has_more)
            self.assertTrue(first.log_available)

            second = JobEventStore(runs_root).read(
                "persisted-run",
                after_sequence=first.next_sequence,
                limit=10,
            )
            self.assertFalse(second.has_more)
            self.assertEqual(second.events[-1].stage, "completed")
            encoded = str([event.model_dump(mode="json") for event in second.events])
            self.assertNotIn("private-value", encoded)
            self.assertNotIn("123456", encoded)
            self.assertIn("<redacted>", encoded)
            probe = next(event for event in second.events if event.stage == "probe")
            self.assertEqual(probe.metrics["token_count"], 42)

    def test_append_recovers_after_a_truncated_tail_and_rejects_nested_run_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            runs_root = Path(temporary) / "runs"
            logs_root = runs_root / "recovered-run" / "logs"
            logs_root.mkdir(parents=True)
            log_path = logs_root / "events.jsonl"
            log_path.write_text('{"sequence":', encoding="utf-8")
            store = JobEventStore(runs_root)
            event = JobEvent(
                sequence=1,
                run_id="recovered-run",
                state=JobState.RUNNING,
                stage="probe",
            )

            self.assertTrue(store.append(event))
            recovered = store.read("recovered-run")

            self.assertEqual([item.sequence for item in recovered.events], [1])
            self.assertEqual(recovered.corrupt_line_count, 1)
            (runs_root / "nested" / "run" / "logs").mkdir(parents=True)
            nested = event.model_copy(update={"run_id": "nested/run"})
            self.assertFalse(store.append(nested))
            self.assertFalse((runs_root / "nested" / "run" / "logs" / "events.jsonl").exists())
            aliased = event.model_copy(update={"run_id": "nested/../recovered-run"})
            self.assertFalse(store.append(aliased))

    def test_concurrent_appends_remain_line_atomic(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            runs_root = Path(temporary) / "runs"
            (runs_root / "concurrent-run" / "logs").mkdir(parents=True)
            store = JobEventStore(runs_root)
            barrier = threading.Barrier(16)
            results: list[bool] = []
            results_lock = threading.Lock()

            def append_event(sequence: int) -> None:
                barrier.wait(timeout=2)
                event = JobEvent(
                    sequence=sequence,
                    run_id="concurrent-run",
                    state=JobState.RUNNING,
                    stage="probe",
                    message="x" * 4096,
                )
                appended = store.append(event)
                with results_lock:
                    results.append(appended)

            threads = [
                threading.Thread(target=append_event, args=(sequence,))
                for sequence in range(1, 17)
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=3)

            self.assertEqual(results, [True] * 16)
            lines = (runs_root / "concurrent-run" / "logs" / "events.jsonl").read_text(
                encoding="utf-8"
            ).splitlines()
            parsed = [JobEvent.model_validate_json(line) for line in lines if line]
            self.assertEqual(len(parsed), 16)
            self.assertEqual(
                {event.sequence for event in parsed},
                set(range(1, 17)),
            )

    def test_missing_and_corrupt_event_logs_degrade_without_raising(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            runs_root = Path(temporary) / "runs"
            run_root = runs_root / "damaged-run"
            logs_root = run_root / "logs"
            logs_root.mkdir(parents=True)
            store = JobEventStore(runs_root)

            missing = store.read("damaged-run")
            self.assertFalse(missing.log_available)
            self.assertEqual(missing.events, [])

            (logs_root / "events.jsonl").write_text(
                '{"not":"a-job-event"}\n{this is incomplete',
                encoding="utf-8",
            )
            damaged = store.read("damaged-run")
            self.assertTrue(damaged.log_available)
            self.assertEqual(damaged.events, [])
            self.assertEqual(damaged.corrupt_line_count, 2)

    def test_failed_event_persists_redacted_detail_and_error_type(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            runs_root = Path(temporary) / "runs"
            run_root = runs_root / "failed-run"
            (run_root / "logs").mkdir(parents=True)

            def worker(cancel, emit) -> None:
                cancel.raise_if_cancelled()
                emit("ocr.extract", progress=0.2, message="starting")
                raise RuntimeError("api_key=private-value")

            with JobManager(
                max_workers=1,
                event_store=JobEventStore(runs_root),
            ) as manager:
                manager.submit("failed-run", worker)
                future = manager._handles["failed-run"].future
                self.assertIsNotNone(future)
                future.result(timeout=2)

            page = JobEventStore(runs_root).read("failed-run")
            failed = next(event for event in page.events if event.error_type is not None)
            self.assertEqual(failed.stage, "ocr.extract")
            self.assertEqual(failed.error_type, "RuntimeError")
            self.assertIn("<redacted>", failed.message or "")
            self.assertNotIn("private-value", failed.message or "")
