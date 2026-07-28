from __future__ import annotations

import threading
import unittest

from video2notes.jobs import JobAlreadyRunningError, JobManager, JobState


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
