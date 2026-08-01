"""Local-only hardware discovery without uploading machine information."""

from __future__ import annotations

import csv
import ctypes
import importlib
import os
import platform
import shutil
import subprocess
from collections.abc import Callable, Sequence
from enum import StrEnum
from io import StringIO
from pathlib import Path
from typing import Protocol, cast

from pydantic import BaseModel, ConfigDict, Field

CommandRunner = Callable[[Sequence[str]], subprocess.CompletedProcess[str]]


class HardwareModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class HardwareTier(StrEnum):
    """Coarse scheduling capacity, deliberately separate from quality mode."""

    CPU_IGPU = "cpu_igpu"
    GPU_8GB = "gpu_8gb"
    GPU_12GB = "gpu_12gb"
    GPU_24GB_PLUS = "gpu_24gb_plus"


class GpuDevice(HardwareModel):
    name: str
    vendor: str
    memory_total_bytes: int | None = Field(default=None, ge=0)
    memory_free_bytes: int | None = Field(default=None, ge=0)
    memory_used_bytes: int | None = Field(default=None, ge=0)
    utilization_percent: float | None = Field(default=None, ge=0, le=100)
    driver_version: str | None = None


class HardwareSnapshot(HardwareModel):
    os_name: str
    os_version: str
    architecture: str
    cpu_name: str
    logical_cores: int = Field(ge=1)
    cpu_load_percent: float | None = Field(default=None, ge=0, le=100)
    memory_total_bytes: int | None = Field(default=None, ge=0)
    memory_available_bytes: int | None = Field(default=None, ge=0)
    memory_load_percent: float | None = Field(default=None, ge=0, le=100)
    disk_total_bytes: int | None = Field(default=None, ge=0)
    disk_available_bytes: int | None = Field(default=None, ge=0)
    gpus: tuple[GpuDevice, ...] = ()
    ffmpeg_hwaccels: tuple[str, ...] = ()

    @property
    def primary_gpu(self) -> GpuDevice | None:
        candidates = [gpu for gpu in self.gpus if gpu.memory_total_bytes is not None]
        if candidates:
            return max(
                candidates,
                key=lambda gpu: gpu.memory_total_bytes or 0,
            )
        return self.gpus[0] if self.gpus else None


def _default_runner(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(command),
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=10,
    )


def _detect_windows_cpu_name() -> str | None:
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"HARDWARE\DESCRIPTION\System\CentralProcessor\0",
        ) as key:
            value, _ = winreg.QueryValueEx(key, "ProcessorNameString")
            return str(value).strip() or None
    except (ImportError, OSError):
        return None


class _PsutilVirtualMemory(Protocol):
    total: int
    available: int
    percent: float


class _PsutilDiskUsage(Protocol):
    total: int
    free: int


class _PsutilModule(Protocol):
    def cpu_percent(self, interval: float | None = None) -> float: ...

    def virtual_memory(self) -> _PsutilVirtualMemory: ...

    def disk_usage(self, path: str) -> _PsutilDiskUsage: ...


def _detect_psutil_metrics() -> tuple[
    float | None,
    int | None,
    int | None,
    float | None,
    int | None,
    int | None,
] | None:
    """Prefer psutil for one coherent live snapshot when it is installed."""

    try:
        psutil = cast(_PsutilModule, importlib.import_module("psutil"))
        memory = psutil.virtual_memory()
        disk = psutil.disk_usage(str(Path.cwd()))
        # A short blocking sample avoids psutil's meaningless first-call zero
        # while keeping hardware preflight responsive.
        cpu_load = float(psutil.cpu_percent(interval=0.05))
    except (ImportError, OSError, RuntimeError, ValueError):
        return None
    return (
        max(0.0, min(100.0, cpu_load)),
        max(0, int(memory.total)),
        max(0, int(memory.available)),
        max(0.0, min(100.0, float(memory.percent))),
        max(0, int(disk.total)),
        max(0, int(disk.free)),
    )


def _detect_native_memory() -> tuple[int | None, int | None, float | None]:
    if os.name == "nt":

        class MemoryStatusEx(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        status = MemoryStatusEx()
        status.dwLength = ctypes.sizeof(MemoryStatusEx)
        try:
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
                return (
                    int(status.ullTotalPhys),
                    int(status.ullAvailPhys),
                    float(status.dwMemoryLoad),
                )
        except (AttributeError, OSError):
            return None, None, None
        return None, None, None

    raw_sysconf = getattr(os, "sysconf", None)
    if not callable(raw_sysconf):
        return None, None, None
    sysconf = cast(Callable[[str], int], raw_sysconf)
    try:
        page_size = sysconf("SC_PAGE_SIZE")
        page_count = sysconf("SC_PHYS_PAGES")
        available_page_count = sysconf("SC_AVPHYS_PAGES")
    except (OSError, ValueError):
        return None, None, None
    total = int(page_size * page_count)
    available = int(page_size * available_page_count)
    load = 100.0 * (1.0 - available / total) if total else None
    return total, available, load


def _detect_native_disk() -> tuple[int | None, int | None]:
    try:
        usage = shutil.disk_usage(Path.cwd())
    except OSError:
        return None, None
    return int(usage.total), int(usage.free)


def _mib_to_bytes(value: str) -> int | None:
    try:
        return int(float(value) * 1024 * 1024)
    except ValueError:
        return None


def _optional_percent(value: str) -> float | None:
    try:
        return max(0.0, min(100.0, float(value)))
    except ValueError:
        return None


def _detect_nvidia_gpus(runner: CommandRunner) -> tuple[GpuDevice, ...]:
    result = runner(
        (
            "nvidia-smi",
            "--query-gpu=name,memory.total,memory.free,memory.used,utilization.gpu,driver_version",
            "--format=csv,noheader,nounits",
        )
    )
    if result.returncode != 0:
        return ()

    devices: list[GpuDevice] = []
    for row in csv.reader(StringIO(result.stdout)):
        if len(row) < 3:
            continue
        fields = [field.strip() for field in row]
        name = fields[0]
        memory_bytes = _mib_to_bytes(fields[1])
        if len(fields) >= 6:
            memory_free_bytes = _mib_to_bytes(fields[2])
            memory_used_bytes = _mib_to_bytes(fields[3])
            utilization_percent = _optional_percent(fields[4])
            driver_version = fields[5]
        else:
            # Keep compatibility with the original three-column command and its
            # deterministic runner fixtures.
            memory_free_bytes = None
            memory_used_bytes = None
            utilization_percent = None
            driver_version = fields[2]
        devices.append(
            GpuDevice(
                name=name,
                vendor="NVIDIA",
                memory_total_bytes=memory_bytes,
                memory_free_bytes=memory_free_bytes,
                memory_used_bytes=memory_used_bytes,
                utilization_percent=utilization_percent,
                driver_version=driver_version or None,
            )
        )
    return tuple(devices)


def _detect_ffmpeg_hwaccels(runner: CommandRunner) -> tuple[str, ...]:
    result = runner(("ffmpeg", "-hide_banner", "-hwaccels"))
    if result.returncode != 0:
        return ()
    accelerators = []
    for line in result.stdout.splitlines():
        candidate = line.strip().lower()
        if candidate and "hardware acceleration" not in candidate:
            accelerators.append(candidate)
    return tuple(dict.fromkeys(accelerators))


def detect_hardware(*, runner: CommandRunner | None = None) -> HardwareSnapshot:
    """Return a serializable local snapshot suitable for scheduling and diagnostics."""

    command_runner = runner or _default_runner
    cpu_name = (_detect_windows_cpu_name() if os.name == "nt" else None) or platform.processor()
    psutil_metrics = _detect_psutil_metrics()
    if psutil_metrics is None:
        memory_total, memory_available, memory_load = _detect_native_memory()
        disk_total, disk_available = _detect_native_disk()
        cpu_load = None
    else:
        (
            cpu_load,
            memory_total,
            memory_available,
            memory_load,
            disk_total,
            disk_available,
        ) = psutil_metrics
    return HardwareSnapshot(
        os_name=platform.system() or os.name,
        os_version=platform.version(),
        architecture=platform.machine() or "unknown",
        cpu_name=cpu_name or "Unknown CPU",
        logical_cores=os.cpu_count() or 1,
        cpu_load_percent=cpu_load,
        memory_total_bytes=memory_total,
        memory_available_bytes=memory_available,
        memory_load_percent=memory_load,
        disk_total_bytes=disk_total,
        disk_available_bytes=disk_available,
        gpus=_detect_nvidia_gpus(command_runner),
        ffmpeg_hwaccels=_detect_ffmpeg_hwaccels(command_runner),
    )


def recommend_hardware_tier(snapshot: HardwareSnapshot) -> HardwareTier:
    """Choose memory-safe concurrency defaults; this does not choose quality."""

    gpu = snapshot.primary_gpu
    if gpu is None or gpu.memory_total_bytes is None:
        return HardwareTier.CPU_IGPU

    gib = gpu.memory_total_bytes / (1024**3)
    if gib >= 20:
        return HardwareTier.GPU_24GB_PLUS
    if gib >= 10:
        return HardwareTier.GPU_12GB
    if gib >= 6:
        return HardwareTier.GPU_8GB
    return HardwareTier.CPU_IGPU
