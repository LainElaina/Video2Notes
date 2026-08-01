from __future__ import annotations

import io
import json
import subprocess
import unittest
from collections.abc import Sequence
from typing import Any

from pydantic import ValidationError

from video2notes.system.benchmark_guard import (
    BenchmarkGuardConfig,
    NvidiaReading,
    PsutilProcessLimiter,
    ResourceGuardError,
    ResourceSample,
    ResourceSummary,
    WindowsJobController,
    build_guard_environment,
    probe_nvidia,
    run_guarded_benchmark,
)


class FakeWindowsJobApi:
    def __init__(self, *, fail_cpu_configuration: bool = False):
        self.fail_cpu_configuration = fail_cpu_configuration
        self.calls: list[tuple[object, ...]] = []

    def create_job(self) -> int:
        self.calls.append(("create_job",))
        return 101

    def configure_cpu_hard_cap(self, job_handle: int, cpu_rate: int) -> None:
        self.calls.append(("configure_cpu_hard_cap", job_handle, cpu_rate))
        if self.fail_cpu_configuration:
            raise OSError("fixture failure")

    def configure_kill_on_close(self, job_handle: int) -> None:
        self.calls.append(("configure_kill_on_close", job_handle))

    def open_process_for_job(self, process_id: int) -> int:
        self.calls.append(("open_process_for_job", process_id))
        return 202

    def assign_process(self, job_handle: int, process_handle: int) -> None:
        self.calls.append(("assign_process", job_handle, process_handle))

    def terminate_job(self, job_handle: int, exit_code: int) -> None:
        self.calls.append(("terminate_job", job_handle, exit_code))

    def close_handle(self, handle: int) -> None:
        self.calls.append(("close_handle", handle))


class FakeStdin(io.StringIO):
    def __init__(self) -> None:
        super().__init__()
        self.payload = ""

    def close(self) -> None:
        self.payload = self.getvalue()
        super().close()


class FakeProcess:
    def __init__(self, *, running_polls: int = 2, exit_code: int = 125):
        self.pid = 999
        self.stdin = FakeStdin()
        self.running_polls = running_polls
        self.exit_code = exit_code
        self.poll_count = 0
        self.terminated = False

    def poll(self) -> int | None:
        self.poll_count += 1
        if self.poll_count <= self.running_polls:
            return None
        return self.exit_code

    def wait(self, timeout: float | None = None) -> int:
        del timeout
        return self.exit_code

    def terminate(self) -> None:
        self.terminated = True


class FakeSampler:
    def __init__(self, run_gpu: Sequence[float | None] = (20.0, 25.0)):
        self.run_gpu = tuple(run_gpu)
        self.run_index = 0

    def sample(self, process_id: int | None, *, elapsed_seconds: float) -> ResourceSample:
        if process_id is None:
            return ResourceSample(
                elapsed_seconds=elapsed_seconds,
                system_cpu_percent=12.0,
                nvidia_gpu_percent=5.0,
                nvidia_vram_used_bytes=256 * 1024**2,
            )
        index = min(self.run_index, len(self.run_gpu) - 1)
        gpu = self.run_gpu[index]
        self.run_index += 1
        return ResourceSample(
            elapsed_seconds=elapsed_seconds,
            process_tree_cpu_percent=42.0,
            process_tree_rss_bytes=512 * 1024**2,
            system_cpu_percent=30.0,
            nvidia_gpu_percent=gpu,
            nvidia_vram_used_bytes=768 * 1024**2,
        )


class FakeLimiter:
    def __init__(self) -> None:
        self.calls: list[tuple[int, float, bool]] = []

    def apply(
        self,
        process_id: int,
        *,
        max_cpu_ratio: float,
        below_normal_priority: bool,
    ) -> tuple[tuple[int, ...], bool, tuple[str, ...]]:
        self.calls.append((process_id, max_cpu_ratio, below_normal_priority))
        return (0, 1, 2, 3), below_normal_priority, ()


class FakeClock:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        self.value += 0.1
        return self.value


class GuardConfigurationTests(unittest.TestCase):
    def test_default_cpu_limit_is_half_and_larger_values_are_rejected(self) -> None:
        self.assertEqual(BenchmarkGuardConfig().max_cpu_ratio, 0.5)
        with self.assertRaises(ValidationError):
            BenchmarkGuardConfig(max_cpu_ratio=0.51)

    def test_force_cpu_environment_overrides_cuda_before_child_start(self) -> None:
        environment, controlled = build_guard_environment(
            {
                "CUDA_VISIBLE_DEVICES": "0",
                "OMP_NUM_THREADS": "64",
                "KEEP_ME": "yes",
            },
            force_cpu=True,
            thread_limit=4,
        )

        self.assertEqual(environment["CUDA_VISIBLE_DEVICES"], "-1")
        self.assertEqual(environment["OMP_NUM_THREADS"], "4")
        self.assertEqual(environment["MKL_NUM_THREADS"], "4")
        self.assertEqual(environment["OPENBLAS_NUM_THREADS"], "4")
        self.assertEqual(environment["NUMEXPR_MAX_THREADS"], "4")
        self.assertEqual(environment["KEEP_ME"], "yes")
        self.assertEqual(controlled["CUDA_VISIBLE_DEVICES"], "-1")

    def test_resource_summary_separates_average_and_peak(self) -> None:
        summary = ResourceSummary.from_samples(
            [
                ResourceSample(
                    elapsed_seconds=0,
                    process_tree_cpu_percent=10,
                    process_tree_rss_bytes=100,
                    system_cpu_percent=20,
                    nvidia_gpu_percent=5,
                    nvidia_vram_used_bytes=1_000,
                ),
                ResourceSample(
                    elapsed_seconds=1,
                    process_tree_cpu_percent=30,
                    process_tree_rss_bytes=300,
                    system_cpu_percent=40,
                    nvidia_gpu_percent=25,
                    nvidia_vram_used_bytes=3_000,
                ),
            ]
        )

        self.assertEqual(summary.average_process_tree_cpu_percent, 20)
        self.assertEqual(summary.peak_process_tree_cpu_percent, 30)
        self.assertEqual(summary.average_process_tree_rss_bytes, 200)
        self.assertEqual(summary.peak_process_tree_rss_bytes, 300)
        self.assertEqual(summary.average_nvidia_gpu_percent, 15)
        self.assertEqual(summary.peak_nvidia_vram_used_bytes, 3_000)


class WindowsJobControllerTests(unittest.TestCase):
    def test_job_hard_cap_is_configured_before_assignment_and_handle_is_retained(self) -> None:
        api = FakeWindowsJobApi()
        controller = WindowsJobController(api)

        cpu_rate = controller.apply(999, max_cpu_ratio=0.5)

        self.assertEqual(cpu_rate, 5_000)
        self.assertTrue(controller.is_open)
        self.assertEqual(controller.assigned_process_id, 999)
        self.assertEqual(
            api.calls[:5],
            [
                ("create_job",),
                ("configure_cpu_hard_cap", 101, 5_000),
                ("configure_kill_on_close", 101),
                ("open_process_for_job", 999),
                ("assign_process", 101, 202),
            ],
        )
        self.assertIn(("close_handle", 202), api.calls)
        self.assertNotIn(("close_handle", 101), api.calls)

        controller.terminate(exit_code=125)
        controller.close()

        self.assertIn(("terminate_job", 101, 125), api.calls)
        self.assertEqual(api.calls[-1], ("close_handle", 101))
        self.assertFalse(controller.is_open)

    def test_failed_configuration_closes_job_without_assigning_process(self) -> None:
        api = FakeWindowsJobApi(fail_cpu_configuration=True)
        controller = WindowsJobController(api)

        with self.assertRaises(OSError):
            controller.apply(999, max_cpu_ratio=0.5)

        self.assertIn(("close_handle", 101), api.calls)
        self.assertFalse(controller.is_open)
        self.assertFalse(any(call[0] == "assign_process" for call in api.calls))


class ProcessLimiterTests(unittest.TestCase):
    def test_affinity_uses_at_most_half_and_windows_priority_is_applied(self) -> None:
        class Process:
            def __init__(self) -> None:
                self.applied_affinity: list[int] | None = None
                self.priority: int | None = None

            def cpu_affinity(self, value: list[int] | None = None) -> list[int]:
                if value is not None:
                    self.applied_affinity = value
                return list(range(16))

            def nice(self, value: int) -> None:
                self.priority = value

        process = Process()

        class Psutil:
            BELOW_NORMAL_PRIORITY_CLASS = 16_384

            @staticmethod
            def Process(process_id: int) -> Process:
                del process_id
                return process

        limiter = PsutilProcessLimiter(Psutil())
        affinity, priority, notes = limiter.apply(
            999,
            max_cpu_ratio=0.5,
            below_normal_priority=True,
        )

        self.assertEqual(affinity, tuple(range(8)))
        self.assertEqual(process.applied_affinity, list(range(8)))
        self.assertEqual(process.priority, 16_384)
        self.assertTrue(priority)
        self.assertEqual(notes, ())


class NvidiaProbeTests(unittest.TestCase):
    def test_probe_uses_peak_device_utilization_and_total_used_vram(self) -> None:
        def runner(
            command: Sequence[str],
            **kwargs: Any,
        ) -> subprocess.CompletedProcess[str]:
            del kwargs
            return subprocess.CompletedProcess(
                command,
                0,
                "12, 1024\n37, 2048\n",
                "",
            )

        reading = probe_nvidia(runner=runner)

        self.assertEqual(
            reading,
            NvidiaReading(
                gpu_percent=37,
                vram_used_bytes=3_072 * 1024**2,
            ),
        )


class GuardedRunnerTests(unittest.TestCase):
    def test_windows_watchdog_report_is_structured_and_win32_is_mocked(self) -> None:
        fake_process = FakeProcess(running_polls=2)
        created: dict[str, Any] = {}

        def process_factory(command: Sequence[str], **kwargs: Any) -> FakeProcess:
            created["command"] = tuple(command)
            created["environment"] = kwargs["env"]
            return fake_process

        api = FakeWindowsJobApi()
        limiter = FakeLimiter()
        report = run_guarded_benchmark(
            ("benchmark.exe", "--fixture"),
            config=BenchmarkGuardConfig(
                baseline_samples=1,
                poll_interval_seconds=0.05,
                gpu_breach_samples=2,
            ),
            sampler=FakeSampler((55.0, 60.0)),
            limiter=limiter,
            process_factory=process_factory,
            windows_job_factory=lambda: WindowsJobController(api),
            platform_name="windows",
            sleep=lambda _: None,
            monotonic=FakeClock(),
        )

        self.assertEqual(created["environment"]["CUDA_VISIBLE_DEVICES"], "-1")
        self.assertEqual(limiter.calls, [(999, 0.5, True)])
        self.assertTrue(report.cpu_control.windows_job_hard_cap_applied)
        self.assertEqual(report.cpu_control.windows_job_cpu_rate, 5_000)
        self.assertTrue(report.cpu_control.below_normal_priority_applied)
        self.assertEqual(report.run.sample_count, 2)
        self.assertEqual(report.run.peak_nvidia_gpu_percent, 60)
        self.assertTrue(report.gpu_watchdog.exceeded)
        self.assertFalse(report.gpu_watchdog.hard_limit)
        self.assertTrue(report.gpu_watchdog.terminated_process_tree)
        self.assertEqual(report.termination_reason, "gpu_watchdog_exceeded")
        self.assertIn(("terminate_job", 101, 125), api.calls)
        self.assertEqual(api.calls[-1], ("close_handle", 101))
        payload = json.loads(fake_process.stdin.payload)
        self.assertEqual(payload["command"], ["benchmark.exe", "--fixture"])

    def test_windows_job_failure_does_not_release_target_gate(self) -> None:
        fake_process = FakeProcess(running_polls=0)
        api = FakeWindowsJobApi(fail_cpu_configuration=True)

        with self.assertRaises(ResourceGuardError):
            run_guarded_benchmark(
                ("benchmark.exe",),
                config=BenchmarkGuardConfig(baseline_samples=1),
                sampler=FakeSampler(),
                limiter=FakeLimiter(),
                process_factory=lambda *args, **kwargs: fake_process,
                windows_job_factory=lambda: WindowsJobController(api),
                platform_name="windows",
                sleep=lambda _: None,
                monotonic=FakeClock(),
            )

        self.assertTrue(fake_process.terminated)
        self.assertEqual(fake_process.stdin.payload, "")
        self.assertFalse(any(call[0] == "assign_process" for call in api.calls))

    def test_non_windows_report_explicitly_says_cpu_limit_is_not_hard(self) -> None:
        fake_process = FakeProcess(running_polls=1, exit_code=0)
        report = run_guarded_benchmark(
            ("benchmark",),
            config=BenchmarkGuardConfig(baseline_samples=1),
            sampler=FakeSampler((10.0,)),
            limiter=FakeLimiter(),
            process_factory=lambda *args, **kwargs: fake_process,
            platform_name="linux",
            sleep=lambda _: None,
            monotonic=FakeClock(),
        )

        self.assertFalse(report.cpu_control.windows_job_hard_cap_supported)
        self.assertFalse(report.cpu_control.windows_job_hard_cap_applied)
        self.assertTrue(any("not an OS hard" in note for note in report.cpu_control.notes))


if __name__ == "__main__":
    unittest.main()
