"""Run a benchmark command in an independently guarded process tree.

The guard intentionally separates enforceable controls from telemetry:

* Windows CPU rate control is a Job Object hard cap.  The Job handle stays
  open for the complete child lifetime and is configured to kill descendants
  when it closes.
* CPU affinity and native thread-pool environment variables are portable
  defence-in-depth controls, not hard utilisation limits on non-Windows hosts.
* NVIDIA GPU utilisation is device-wide watchdog telemetry.  It can stop a
  benchmark after a breach, but it cannot prevent a CUDA kernel from briefly
  exceeding the threshold.  ``force_cpu`` is the strict safe default.

The target command is launched by a small gated Python child.  The parent
applies the Job Object, affinity, and priority to that child before releasing
the gate, so the actual benchmark and its descendants inherit the controls.
"""

from __future__ import annotations

import ctypes
import importlib
import json
import math
import os
import subprocess
import sys
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol, Self, TextIO, cast

from pydantic import BaseModel, ConfigDict, Field, model_validator

_WINDOWS_JOB_CPU_RATE_ENABLE = 0x1
_WINDOWS_JOB_CPU_RATE_HARD_CAP = 0x4
_WINDOWS_JOB_KILL_ON_CLOSE = 0x2000
_WINDOWS_JOB_EXTENDED_LIMIT_INFORMATION = 9
_WINDOWS_JOB_CPU_RATE_CONTROL_INFORMATION = 15
_WINDOWS_PROCESS_SET_QUOTA = 0x0100
_WINDOWS_PROCESS_TERMINATE = 0x0001
_WINDOWS_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_THREAD_ENVIRONMENT_KEYS = (
    "OMP_NUM_THREADS",
    "OMP_THREAD_LIMIT",
    "MKL_NUM_THREADS",
    "OPENBLAS_NUM_THREADS",
    "NUMEXPR_MAX_THREADS",
)


class ResourceGuardError(RuntimeError):
    """The benchmark could not be started with the requested safety controls."""


class GuardModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class BenchmarkGuardConfig(GuardModel):
    """Safety policy for one independently launched benchmark command."""

    max_cpu_ratio: float = Field(default=0.5, gt=0, le=0.5)
    force_cpu: bool = True
    poll_interval_seconds: float = Field(default=0.25, ge=0.05, le=5.0)
    baseline_samples: int = Field(default=3, ge=1, le=50)
    gpu_watchdog_percent: float | None = Field(default=50.0, gt=0, le=100)
    gpu_breach_samples: int = Field(default=2, ge=1, le=20)
    timeout_seconds: float | None = Field(default=None, gt=0)


class ResourceSample(GuardModel):
    elapsed_seconds: float = Field(ge=0)
    process_tree_cpu_percent: float | None = Field(default=None, ge=0)
    process_tree_rss_bytes: int | None = Field(default=None, ge=0)
    system_cpu_percent: float | None = Field(default=None, ge=0, le=100)
    nvidia_gpu_percent: float | None = Field(default=None, ge=0, le=100)
    nvidia_vram_used_bytes: int | None = Field(default=None, ge=0)


class ResourceSummary(GuardModel):
    sample_count: int = Field(ge=0)
    average_process_tree_cpu_percent: float | None = Field(default=None, ge=0)
    peak_process_tree_cpu_percent: float | None = Field(default=None, ge=0)
    average_process_tree_rss_bytes: int | None = Field(default=None, ge=0)
    peak_process_tree_rss_bytes: int | None = Field(default=None, ge=0)
    average_system_cpu_percent: float | None = Field(default=None, ge=0, le=100)
    peak_system_cpu_percent: float | None = Field(default=None, ge=0, le=100)
    average_nvidia_gpu_percent: float | None = Field(default=None, ge=0, le=100)
    peak_nvidia_gpu_percent: float | None = Field(default=None, ge=0, le=100)
    average_nvidia_vram_used_bytes: int | None = Field(default=None, ge=0)
    peak_nvidia_vram_used_bytes: int | None = Field(default=None, ge=0)

    @classmethod
    def from_samples(cls, samples: Sequence[ResourceSample]) -> Self:
        return cls(
            sample_count=len(samples),
            average_process_tree_cpu_percent=_average_float(
                item.process_tree_cpu_percent for item in samples
            ),
            peak_process_tree_cpu_percent=_maximum_float(
                item.process_tree_cpu_percent for item in samples
            ),
            average_process_tree_rss_bytes=_average_int(
                item.process_tree_rss_bytes for item in samples
            ),
            peak_process_tree_rss_bytes=_maximum_int(
                item.process_tree_rss_bytes for item in samples
            ),
            average_system_cpu_percent=_average_float(
                item.system_cpu_percent for item in samples
            ),
            peak_system_cpu_percent=_maximum_float(
                item.system_cpu_percent for item in samples
            ),
            average_nvidia_gpu_percent=_average_float(
                item.nvidia_gpu_percent for item in samples
            ),
            peak_nvidia_gpu_percent=_maximum_float(
                item.nvidia_gpu_percent for item in samples
            ),
            average_nvidia_vram_used_bytes=_average_int(
                item.nvidia_vram_used_bytes for item in samples
            ),
            peak_nvidia_vram_used_bytes=_maximum_int(
                item.nvidia_vram_used_bytes for item in samples
            ),
        )


class CpuControlReport(GuardModel):
    requested_max_ratio: float = Field(gt=0, le=0.5)
    windows_job_hard_cap_supported: bool
    windows_job_hard_cap_applied: bool
    windows_job_cpu_rate: int | None = Field(default=None, ge=1, le=5_000)
    affinity_logical_processors: tuple[int, ...] = ()
    below_normal_priority_applied: bool = False
    notes: tuple[str, ...] = ()


class GpuWatchdogReport(GuardModel):
    threshold_percent: float | None = Field(default=None, gt=0, le=100)
    hard_limit: bool = False
    telemetry_available: bool
    exceeded: bool
    consecutive_breach_limit: int = Field(ge=1)
    maximum_consecutive_breaches: int = Field(ge=0)
    terminated_process_tree: bool
    notes: tuple[str, ...] = ()

    @model_validator(mode="after")
    def reject_false_hard_limit_claim(self) -> Self:
        if self.hard_limit:
            raise ValueError("GPU watchdog telemetry is not a hard utilisation limit")
        return self


class BenchmarkResourceReport(GuardModel):
    schema_version: int = 1
    platform: str
    executable: str
    process_id: int = Field(ge=1)
    started_at: datetime
    finished_at: datetime
    duration_seconds: float = Field(ge=0)
    exit_code: int | None = None
    termination_reason: str | None = None
    force_cpu: bool
    thread_limit: int = Field(ge=1)
    controlled_environment: dict[str, str]
    cpu_control: CpuControlReport
    gpu_watchdog: GpuWatchdogReport
    baseline: ResourceSummary
    run: ResourceSummary


class NvidiaReading(GuardModel):
    gpu_percent: float = Field(ge=0, le=100)
    vram_used_bytes: int = Field(ge=0)


class ResourceSampler(Protocol):
    def sample(self, process_id: int | None, *, elapsed_seconds: float) -> ResourceSample: ...


class ProcessLimiter(Protocol):
    def apply(
        self,
        process_id: int,
        *,
        max_cpu_ratio: float,
        below_normal_priority: bool,
    ) -> tuple[tuple[int, ...], bool, tuple[str, ...]]: ...


class GuardedProcess(Protocol):
    pid: int
    stdin: TextIO | None

    def poll(self) -> int | None: ...

    def wait(self, timeout: float | None = None) -> int: ...

    def terminate(self) -> None: ...


class WindowsJobApi(Protocol):
    def create_job(self) -> int: ...

    def configure_cpu_hard_cap(self, job_handle: int, cpu_rate: int) -> None: ...

    def configure_kill_on_close(self, job_handle: int) -> None: ...

    def open_process_for_job(self, process_id: int) -> int: ...

    def assign_process(self, job_handle: int, process_handle: int) -> None: ...

    def terminate_job(self, job_handle: int, exit_code: int) -> None: ...

    def close_handle(self, handle: int) -> None: ...


class WindowsJobController:
    """Own a configured Windows Job Object for exactly one process tree."""

    def __init__(self, api: WindowsJobApi | None = None):
        self._api = api or CtypesWindowsJobApi()
        self._job_handle: int | None = None
        self._assigned_process_id: int | None = None

    @property
    def is_open(self) -> bool:
        return self._job_handle is not None

    @property
    def assigned_process_id(self) -> int | None:
        return self._assigned_process_id

    def apply(self, process_id: int, *, max_cpu_ratio: float) -> int:
        if self._job_handle is not None:
            raise RuntimeError("Windows Job Object is already configured")
        if not 0 < max_cpu_ratio <= 0.5:
            raise ValueError("max_cpu_ratio must be in the range (0, 0.5]")
        cpu_rate = max(1, min(5_000, round(max_cpu_ratio * 10_000)))
        job_handle = self._api.create_job()
        try:
            self._api.configure_cpu_hard_cap(job_handle, cpu_rate)
            self._api.configure_kill_on_close(job_handle)
            process_handle = self._api.open_process_for_job(process_id)
            try:
                self._api.assign_process(job_handle, process_handle)
            finally:
                self._api.close_handle(process_handle)
        except Exception:
            self._api.close_handle(job_handle)
            raise
        self._job_handle = job_handle
        self._assigned_process_id = process_id
        return cpu_rate

    def terminate(self, *, exit_code: int = 125) -> None:
        if self._job_handle is not None:
            self._api.terminate_job(self._job_handle, exit_code)

    def close(self) -> None:
        if self._job_handle is None:
            return
        handle = self._job_handle
        self._job_handle = None
        self._assigned_process_id = None
        self._api.close_handle(handle)

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


class _LargeInteger(ctypes.Structure):
    _fields_ = [("QuadPart", ctypes.c_longlong)]


class _IoCounters(ctypes.Structure):
    _fields_ = [
        ("ReadOperationCount", ctypes.c_ulonglong),
        ("WriteOperationCount", ctypes.c_ulonglong),
        ("OtherOperationCount", ctypes.c_ulonglong),
        ("ReadTransferCount", ctypes.c_ulonglong),
        ("WriteTransferCount", ctypes.c_ulonglong),
        ("OtherTransferCount", ctypes.c_ulonglong),
    ]


class _JobObjectBasicLimitInformation(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", _LargeInteger),
        ("PerJobUserTimeLimit", _LargeInteger),
        ("LimitFlags", ctypes.c_ulong),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", ctypes.c_ulong),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", ctypes.c_ulong),
        ("SchedulingClass", ctypes.c_ulong),
    ]


class _JobObjectExtendedLimitInformation(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _JobObjectBasicLimitInformation),
        ("IoInfo", _IoCounters),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


class _JobObjectCpuRateControlInformation(ctypes.Structure):
    _fields_ = [
        ("ControlFlags", ctypes.c_ulong),
        ("CpuRate", ctypes.c_ulong),
    ]


class CtypesWindowsJobApi:
    """Small mockable adapter around the Win32 Job Object calls."""

    def __init__(self) -> None:
        if os.name != "nt":
            raise OSError("Windows Job Objects are only available on Windows")
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateJobObjectW.restype = ctypes.c_void_p
        kernel32.CreateJobObjectW.argtypes = (ctypes.c_void_p, ctypes.c_wchar_p)
        kernel32.SetInformationJobObject.restype = ctypes.c_int
        kernel32.SetInformationJobObject.argtypes = (
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.c_void_p,
            ctypes.c_ulong,
        )
        kernel32.OpenProcess.restype = ctypes.c_void_p
        kernel32.OpenProcess.argtypes = (ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong)
        kernel32.AssignProcessToJobObject.restype = ctypes.c_int
        kernel32.AssignProcessToJobObject.argtypes = (ctypes.c_void_p, ctypes.c_void_p)
        kernel32.TerminateJobObject.restype = ctypes.c_int
        kernel32.TerminateJobObject.argtypes = (ctypes.c_void_p, ctypes.c_uint)
        kernel32.CloseHandle.restype = ctypes.c_int
        kernel32.CloseHandle.argtypes = (ctypes.c_void_p,)
        self._kernel32 = kernel32

    def create_job(self) -> int:
        handle = self._kernel32.CreateJobObjectW(None, None)
        if not handle:
            self._raise_last_error("CreateJobObjectW")
        return int(handle)

    def configure_cpu_hard_cap(self, job_handle: int, cpu_rate: int) -> None:
        information = _JobObjectCpuRateControlInformation(
            ControlFlags=(
                _WINDOWS_JOB_CPU_RATE_ENABLE | _WINDOWS_JOB_CPU_RATE_HARD_CAP
            ),
            CpuRate=cpu_rate,
        )
        self._set_information(
            job_handle,
            _WINDOWS_JOB_CPU_RATE_CONTROL_INFORMATION,
            information,
            "SetInformationJobObject(CPU rate)",
        )

    def configure_kill_on_close(self, job_handle: int) -> None:
        information = _JobObjectExtendedLimitInformation()
        information.BasicLimitInformation.LimitFlags = _WINDOWS_JOB_KILL_ON_CLOSE
        self._set_information(
            job_handle,
            _WINDOWS_JOB_EXTENDED_LIMIT_INFORMATION,
            information,
            "SetInformationJobObject(kill on close)",
        )

    def open_process_for_job(self, process_id: int) -> int:
        access = (
            _WINDOWS_PROCESS_SET_QUOTA
            | _WINDOWS_PROCESS_TERMINATE
            | _WINDOWS_PROCESS_QUERY_LIMITED_INFORMATION
        )
        handle = self._kernel32.OpenProcess(access, False, process_id)
        if not handle:
            self._raise_last_error("OpenProcess")
        return int(handle)

    def assign_process(self, job_handle: int, process_handle: int) -> None:
        if not self._kernel32.AssignProcessToJobObject(job_handle, process_handle):
            self._raise_last_error("AssignProcessToJobObject")

    def terminate_job(self, job_handle: int, exit_code: int) -> None:
        if not self._kernel32.TerminateJobObject(job_handle, exit_code):
            self._raise_last_error("TerminateJobObject")

    def close_handle(self, handle: int) -> None:
        if handle and not self._kernel32.CloseHandle(handle):
            self._raise_last_error("CloseHandle")

    def _set_information(
        self,
        job_handle: int,
        information_class: int,
        information: ctypes.Structure,
        operation: str,
    ) -> None:
        if not self._kernel32.SetInformationJobObject(
            job_handle,
            information_class,
            ctypes.byref(information),
            ctypes.sizeof(information),
        ):
            self._raise_last_error(operation)

    @staticmethod
    def _raise_last_error(operation: str) -> None:
        error_code = ctypes.get_last_error()
        raise OSError(error_code, f"{operation} failed")


class PsutilProcessLimiter:
    """Apply inheritable affinity and priority controls to the gated child."""

    def __init__(self, psutil_module: Any | None = None):
        self._psutil = psutil_module or importlib.import_module("psutil")

    def apply(
        self,
        process_id: int,
        *,
        max_cpu_ratio: float,
        below_normal_priority: bool,
    ) -> tuple[tuple[int, ...], bool, tuple[str, ...]]:
        notes: list[str] = []
        process = self._psutil.Process(process_id)
        affinity: tuple[int, ...] = ()
        try:
            allowed = tuple(int(item) for item in process.cpu_affinity())
            affinity_count = max(
                1,
                min(len(allowed), math.floor(len(allowed) * max_cpu_ratio)),
            )
            affinity = allowed[:affinity_count]
            process.cpu_affinity(list(affinity))
        except (AttributeError, OSError, RuntimeError, ValueError) as error:
            notes.append(f"CPU affinity could not be applied ({type(error).__name__}).")

        priority_applied = False
        if below_normal_priority:
            try:
                priority = self._psutil.BELOW_NORMAL_PRIORITY_CLASS
                process.nice(priority)
                priority_applied = True
            except (AttributeError, OSError, RuntimeError, ValueError) as error:
                notes.append(
                    "Below Normal process priority could not be applied "
                    f"({type(error).__name__})."
                )
        return affinity, priority_applied, tuple(notes)


class PsutilResourceSampler:
    """Sample process-tree CPU/RSS plus device-wide CPU and NVIDIA telemetry."""

    def __init__(
        self,
        *,
        psutil_module: Any | None = None,
        gpu_probe: Callable[[], NvidiaReading | None] | None = None,
    ):
        self._psutil = psutil_module or importlib.import_module("psutil")
        logical = self._psutil.cpu_count(logical=True) or os.cpu_count() or 1
        self._logical_processors = max(1, int(logical))
        self._known_process_ids: set[int] = set()
        self._gpu_probe = gpu_probe or probe_nvidia
        # Prime psutil's non-blocking system CPU counter before baseline samples.
        self._psutil.cpu_percent(interval=None)

    def sample(self, process_id: int | None, *, elapsed_seconds: float) -> ResourceSample:
        tree_cpu: float | None = None
        tree_rss: int | None = None
        if process_id is not None:
            tree_cpu, tree_rss = self._sample_process_tree(process_id)
        try:
            system_cpu = float(self._psutil.cpu_percent(interval=None))
        except (AttributeError, OSError, RuntimeError, ValueError):
            system_cpu = None
        gpu = self._gpu_probe()
        return ResourceSample(
            elapsed_seconds=max(0.0, elapsed_seconds),
            process_tree_cpu_percent=tree_cpu,
            process_tree_rss_bytes=tree_rss,
            system_cpu_percent=(
                max(0.0, min(100.0, system_cpu)) if system_cpu is not None else None
            ),
            nvidia_gpu_percent=gpu.gpu_percent if gpu is not None else None,
            nvidia_vram_used_bytes=gpu.vram_used_bytes if gpu is not None else None,
        )

    def _sample_process_tree(self, process_id: int) -> tuple[float | None, int | None]:
        try:
            root = self._psutil.Process(process_id)
            processes = [root, *root.children(recursive=True)]
        except Exception:  # psutil's platform-specific process errors share no stdlib base
            return None, None

        cpu_total = 0.0
        rss_total = 0
        sampled = False
        for process in processes:
            try:
                pid = int(process.pid)
                if pid not in self._known_process_ids:
                    process.cpu_percent(interval=None)
                    self._known_process_ids.add(pid)
                    cpu_value = 0.0
                else:
                    cpu_value = float(process.cpu_percent(interval=None))
                rss_value = int(process.memory_info().rss)
            except Exception:  # process can exit between enumeration and sampling
                continue
            sampled = True
            cpu_total += max(0.0, cpu_value)
            rss_total += max(0, rss_value)
        if not sampled:
            return None, None
        return cpu_total / self._logical_processors, rss_total


def build_guard_environment(
    base: Mapping[str, str] | None = None,
    *,
    force_cpu: bool = True,
    thread_limit: int,
) -> tuple[dict[str, str], dict[str, str]]:
    """Return a child-only environment and the controls safe to record."""

    if thread_limit < 1:
        raise ValueError("thread_limit must be positive")
    environment = dict(os.environ)
    if base is not None:
        environment.update(base)
    controlled = {key: str(thread_limit) for key in _THREAD_ENVIRONMENT_KEYS}
    controlled["OMP_DYNAMIC"] = "FALSE"
    controlled["TOKENIZERS_PARALLELISM"] = "false"
    if force_cpu:
        controlled["CUDA_VISIBLE_DEVICES"] = "-1"
    environment.update(controlled)
    return environment, controlled


def probe_nvidia(
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> NvidiaReading | None:
    """Return conservative device-wide NVIDIA utilisation and used VRAM."""

    command = (
        "nvidia-smi",
        "--query-gpu=utilization.gpu,memory.used",
        "--format=csv,noheader,nounits",
    )
    try:
        result = runner(
            command,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=2,
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    utilizations: list[float] = []
    used_mib: list[float] = []
    for line in result.stdout.splitlines():
        fields = [item.strip() for item in line.split(",")]
        if len(fields) < 2:
            continue
        try:
            utilizations.append(max(0.0, min(100.0, float(fields[0]))))
            used_mib.append(max(0.0, float(fields[1])))
        except ValueError:
            continue
    if not utilizations:
        return None
    return NvidiaReading(
        gpu_percent=max(utilizations),
        vram_used_bytes=round(sum(used_mib) * 1024 * 1024),
    )


ProcessFactory = Callable[..., GuardedProcess]
JobFactory = Callable[[], WindowsJobController]


def run_guarded_benchmark(
    command: Sequence[str],
    *,
    config: BenchmarkGuardConfig | None = None,
    cwd: str | Path | None = None,
    environment: Mapping[str, str] | None = None,
    sampler: ResourceSampler | None = None,
    limiter: ProcessLimiter | None = None,
    process_factory: ProcessFactory | None = None,
    windows_job_factory: JobFactory | None = None,
    platform_name: str | None = None,
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
) -> BenchmarkResourceReport:
    """Run ``command`` after applying controls to a gated child process.

    On Windows, failure to install the Job Object hard cap fails closed: the
    target command is never released.  On other operating systems the report
    explicitly says that affinity and thread limits are cooperative controls.
    """

    selected = config or BenchmarkGuardConfig()
    normalized_command = tuple(str(item) for item in command)
    if not normalized_command or not normalized_command[0].strip():
        raise ValueError("benchmark command cannot be empty")
    logical_processors = max(1, os.cpu_count() or 1)
    thread_limit = max(
        1,
        min(
            math.floor(logical_processors * selected.max_cpu_ratio),
            math.floor(logical_processors * 0.5),
        ),
    )
    child_environment, controlled_environment = build_guard_environment(
        environment,
        force_cpu=selected.force_cpu,
        thread_limit=thread_limit,
    )
    selected_platform = platform_name or ("windows" if os.name == "nt" else sys.platform)
    resource_sampler = sampler or PsutilResourceSampler()
    process_limiter = limiter or PsutilProcessLimiter()
    factory = process_factory or _default_process_factory

    baseline: list[ResourceSample] = []
    baseline_start = monotonic()
    for index in range(selected.baseline_samples):
        baseline.append(
            resource_sampler.sample(
                None,
                elapsed_seconds=max(0.0, monotonic() - baseline_start),
            )
        )
        if index + 1 < selected.baseline_samples:
            sleep(selected.poll_interval_seconds)

    process = factory(
        (
            sys.executable,
            "-m",
            "video2notes.system.benchmark_guard",
            "--guard-child",
        ),
        env=child_environment,
        stdin=subprocess.PIPE,
        text=True,
    )
    if process.stdin is None:
        process.terminate()
        raise ResourceGuardError("gated child did not expose a control pipe")

    job: WindowsJobController | None = None
    job_rate: int | None = None
    affinity: tuple[int, ...] = ()
    priority_applied = False
    control_notes: list[str] = []
    hard_cap_supported = selected_platform == "windows"
    hard_cap_applied = False
    gate_released = False
    try:
        affinity, priority_applied, limiter_notes = process_limiter.apply(
            process.pid,
            max_cpu_ratio=selected.max_cpu_ratio,
            below_normal_priority=selected_platform == "windows",
        )
        control_notes.extend(limiter_notes)
        if selected_platform == "windows":
            job = (windows_job_factory or WindowsJobController)()
            try:
                job_rate = job.apply(
                    process.pid,
                    max_cpu_ratio=selected.max_cpu_ratio,
                )
            except Exception as error:
                process.terminate()
                raise ResourceGuardError(
                    "Windows CPU hard cap could not be installed; benchmark was not started "
                    f"({type(error).__name__})."
                ) from error
            hard_cap_applied = True
            control_notes.append(
                "The Windows hard cap applies to the gated benchmark process tree; "
                "the controller and unrelated host processes are outside that Job Object."
            )
        else:
            control_notes.append(
                "This platform has affinity and thread-pool limits only; "
                "max_cpu_ratio is not an OS hard utilisation cap."
            )

        payload = json.dumps(
            {
                "command": list(normalized_command),
                "cwd": str(Path(cwd).expanduser().resolve()) if cwd is not None else None,
            },
            ensure_ascii=False,
        )
        process.stdin.write(payload + "\n")
        process.stdin.flush()
        process.stdin.close()
        gate_released = True

        started_at = datetime.now(UTC)
        started_monotonic = monotonic()
        run_samples: list[ResourceSample] = []
        consecutive_gpu_breaches = 0
        maximum_gpu_breaches = 0
        gpu_exceeded = False
        terminated = False
        termination_reason: str | None = None

        while process.poll() is None:
            elapsed = max(0.0, monotonic() - started_monotonic)
            sample = resource_sampler.sample(process.pid, elapsed_seconds=elapsed)
            run_samples.append(sample)
            if (
                selected.gpu_watchdog_percent is not None
                and sample.nvidia_gpu_percent is not None
                and sample.nvidia_gpu_percent > selected.gpu_watchdog_percent
            ):
                consecutive_gpu_breaches += 1
                gpu_exceeded = True
            else:
                consecutive_gpu_breaches = 0
            maximum_gpu_breaches = max(
                maximum_gpu_breaches,
                consecutive_gpu_breaches,
            )
            if consecutive_gpu_breaches >= selected.gpu_breach_samples:
                termination_reason = "gpu_watchdog_exceeded"
                terminated = True
                _terminate_guarded_process(process, job)
                break
            if selected.timeout_seconds is not None and elapsed >= selected.timeout_seconds:
                termination_reason = "timeout"
                terminated = True
                _terminate_guarded_process(process, job)
                break
            sleep(selected.poll_interval_seconds)

        exit_code = process.wait(timeout=5)
        finished_at = datetime.now(UTC)
        duration = max(0.0, monotonic() - started_monotonic)
        telemetry_available = any(
            item.nvidia_gpu_percent is not None for item in [*baseline, *run_samples]
        )
        gpu_notes = [
            "NVIDIA readings are device-wide telemetry, not per-process attribution.",
            "The watchdog can terminate after a breach but cannot prevent transient GPU peaks.",
        ]
        if selected.force_cpu:
            gpu_notes.append(
                "CUDA_VISIBLE_DEVICES=-1 was set before the gated child imported inference code."
            )
        if not telemetry_available:
            gpu_notes.append("NVIDIA telemetry was unavailable; no GPU percentage was verified.")
        return BenchmarkResourceReport(
            platform=selected_platform,
            executable=Path(normalized_command[0]).name,
            process_id=process.pid,
            started_at=started_at,
            finished_at=finished_at,
            duration_seconds=duration,
            exit_code=exit_code,
            termination_reason=termination_reason,
            force_cpu=selected.force_cpu,
            thread_limit=thread_limit,
            controlled_environment=controlled_environment,
            cpu_control=CpuControlReport(
                requested_max_ratio=selected.max_cpu_ratio,
                windows_job_hard_cap_supported=hard_cap_supported,
                windows_job_hard_cap_applied=hard_cap_applied,
                windows_job_cpu_rate=job_rate,
                affinity_logical_processors=affinity,
                below_normal_priority_applied=priority_applied,
                notes=tuple(control_notes),
            ),
            gpu_watchdog=GpuWatchdogReport(
                threshold_percent=selected.gpu_watchdog_percent,
                telemetry_available=telemetry_available,
                exceeded=gpu_exceeded,
                consecutive_breach_limit=selected.gpu_breach_samples,
                maximum_consecutive_breaches=maximum_gpu_breaches,
                terminated_process_tree=(
                    terminated and termination_reason == "gpu_watchdog_exceeded"
                ),
                notes=tuple(gpu_notes),
            ),
            baseline=ResourceSummary.from_samples(baseline),
            run=ResourceSummary.from_samples(run_samples),
        )
    except Exception:
        if not gate_released and process.poll() is None:
            process.terminate()
        raise
    finally:
        if job is not None:
            job.close()


def _default_process_factory(command: Sequence[str], **kwargs: Any) -> GuardedProcess:
    return cast(GuardedProcess, subprocess.Popen(list(command), **kwargs))


def _terminate_guarded_process(
    process: GuardedProcess,
    job: WindowsJobController | None,
) -> None:
    if job is not None:
        job.terminate()
        return
    try:
        psutil = importlib.import_module("psutil")
        root = psutil.Process(process.pid)
        descendants = root.children(recursive=True)
        for child in reversed(descendants):
            try:
                child.terminate()
            except Exception:
                continue
        root.terminate()
    except Exception:
        process.terminate()


def _average_float(values: Iterable[float | None]) -> float | None:
    present = [float(value) for value in values if value is not None]
    return sum(present) / len(present) if present else None


def _maximum_float(values: Iterable[float | None]) -> float | None:
    present = [float(value) for value in values if value is not None]
    return max(present) if present else None


def _average_int(values: Iterable[int | None]) -> int | None:
    present = [int(value) for value in values if value is not None]
    return round(sum(present) / len(present)) if present else None


def _maximum_int(values: Iterable[int | None]) -> int | None:
    present = [int(value) for value in values if value is not None]
    return max(present) if present else None


def _guard_child_main() -> int:
    line = sys.stdin.readline()
    if not line:
        return 125
    try:
        payload = json.loads(line)
        command = tuple(str(item) for item in payload["command"])
        cwd = payload.get("cwd")
        if not command:
            return 125
        result = subprocess.run(
            command,
            cwd=str(cwd) if cwd is not None else None,
            check=False,
        )
    except (KeyError, TypeError, ValueError, OSError, json.JSONDecodeError):
        return 125
    return int(result.returncode)


def main(argv: Sequence[str] | None = None) -> int:
    arguments = tuple(sys.argv[1:] if argv is None else argv)
    if arguments == ("--guard-child",):
        return _guard_child_main()
    raise SystemExit("benchmark_guard is an internal gated-child module")


if __name__ == "__main__":
    raise SystemExit(main())
