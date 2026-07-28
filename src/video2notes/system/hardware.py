"""Local-only hardware discovery without uploading machine information."""

from __future__ import annotations

import csv
import ctypes
import os
import platform
import subprocess
from collections.abc import Callable, Sequence
from enum import StrEnum
from io import StringIO
from typing import cast

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
    driver_version: str | None = None


class HardwareSnapshot(HardwareModel):
    os_name: str
    os_version: str
    architecture: str
    cpu_name: str
    logical_cores: int = Field(ge=1)
    memory_total_bytes: int | None = Field(default=None, ge=0)
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


def _detect_total_memory() -> int | None:
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
                return int(status.ullTotalPhys)
        except (AttributeError, OSError):
            return None
        return None

    raw_sysconf = getattr(os, "sysconf", None)
    if not callable(raw_sysconf):
        return None
    sysconf = cast(Callable[[str], int], raw_sysconf)
    try:
        page_size = sysconf("SC_PAGE_SIZE")
        page_count = sysconf("SC_PHYS_PAGES")
    except (OSError, ValueError):
        return None
    return int(page_size * page_count)


def _detect_nvidia_gpus(runner: CommandRunner) -> tuple[GpuDevice, ...]:
    result = runner(
        (
            "nvidia-smi",
            "--query-gpu=name,memory.total,driver_version",
            "--format=csv,noheader,nounits",
        )
    )
    if result.returncode != 0:
        return ()

    devices: list[GpuDevice] = []
    for row in csv.reader(StringIO(result.stdout)):
        if len(row) < 3:
            continue
        name, memory_mib, driver_version = (field.strip() for field in row[:3])
        try:
            memory_bytes = int(float(memory_mib) * 1024 * 1024)
        except ValueError:
            memory_bytes = None
        devices.append(
            GpuDevice(
                name=name,
                vendor="NVIDIA",
                memory_total_bytes=memory_bytes,
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
    return HardwareSnapshot(
        os_name=platform.system() or os.name,
        os_version=platform.version(),
        architecture=platform.machine() or "unknown",
        cpu_name=cpu_name or "Unknown CPU",
        logical_cores=os.cpu_count() or 1,
        memory_total_bytes=_detect_total_memory(),
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
