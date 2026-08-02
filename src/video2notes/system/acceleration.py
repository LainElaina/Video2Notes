"""Truthful local inference acceleration discovery and safe plan alignment.

GPU hardware discovery alone is not enough to claim that an inference engine
can use CUDA.  CTranslate2 also needs its CUDA 12 runtime DLLs, while
PaddleOCR needs a CUDA-enabled PaddlePaddle wheel.  This module keeps those
capabilities independent so one engine can use NVIDIA acceleration while the
other safely remains on CPU.
"""

from __future__ import annotations

import contextlib
import ctypes
import importlib
import importlib.util
import os
import sys
from functools import lru_cache
from pathlib import Path
from typing import Protocol, cast

from pydantic import BaseModel, ConfigDict, Field

from .profiles import ExecutionPlan

_NVIDIA_RUNTIME_PACKAGES = (
    "cublas",
    "cuda_nvrtc",
    "cuda_runtime",
    "cudnn",
    "cufft",
    "curand",
    "cusolver",
    "cusparse",
    "nvjitlink",
)
_WINDOWS_REQUIRED_ASR_DLLS = ("cublas64_12.dll", "cudnn64_9.dll")
_DLL_DIRECTORY_HANDLES: list[object] = []
_DLL_DIRECTORY_KEYS: set[str] = set()


class AccelerationModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class EngineAcceleration(AccelerationModel):
    engine: str
    cuda_available: bool
    device_count: int = Field(default=0, ge=0)
    supported_compute_types: tuple[str, ...] = ()
    runtime_directories: tuple[str, ...] = ()
    reason: str


class AccelerationCapabilities(AccelerationModel):
    schema_version: int = 1
    asr: EngineAcceleration
    ocr: EngineAcceleration


class _CTranslate2Module(Protocol):
    def get_cuda_device_count(self) -> int: ...

    def get_supported_compute_types(self, device: str) -> set[str]: ...


class _PaddleCudaDevice(Protocol):
    def device_count(self) -> int: ...


class _PaddleDevice(Protocol):
    cuda: _PaddleCudaDevice

    def is_compiled_with_cuda(self) -> bool: ...


class _PaddleModule(Protocol):
    device: _PaddleDevice


def prepare_nvidia_cuda_runtime() -> tuple[Path, ...]:
    """Expose bundled NVIDIA runtime DLL directories to Windows loaders.

    The NVIDIA PyPI wheels place DLLs below namespace-package ``bin``
    directories.  CTranslate2 loads cuBLAS/cuDNN dynamically, so those paths
    must be present in ``PATH`` before the first CUDA inference.  Keeping the
    ``add_dll_directory`` handles alive also supports extension-module loads.
    """

    if os.name != "nt":
        return ()
    directories = _nvidia_runtime_directories()
    if not directories:
        return ()

    existing = [item for item in os.environ.get("PATH", "").split(os.pathsep) if item]
    existing_keys = {os.path.normcase(os.path.abspath(item)) for item in existing}
    additions: list[str] = []
    for directory in directories:
        value = str(directory)
        key = os.path.normcase(os.path.abspath(value))
        if key not in existing_keys:
            additions.append(value)
            existing_keys.add(key)
        add_directory = getattr(os, "add_dll_directory", None)
        if callable(add_directory) and key not in _DLL_DIRECTORY_KEYS:
            with contextlib.suppress(OSError):
                _DLL_DIRECTORY_HANDLES.append(add_directory(value))
                _DLL_DIRECTORY_KEYS.add(key)
    if additions:
        os.environ["PATH"] = os.pathsep.join([*additions, *existing])
    return directories


def preload_ctranslate2_before_paddle() -> None:
    """Load CTranslate2 before Paddle can claim duplicate Windows DLL names.

    PaddlePaddle GPU and CTranslate2 both ship a ``cudnn64_9.dll``.  Windows
    reuses the first module loaded under that basename, so importing Paddle
    first can make a later CTranslate2 import fail with loader error 127.  The
    verified CUDA 12.9 stack is safe when CTranslate2 is loaded first.

    CPU-only OCR installations do not require CTranslate2.  In that case the
    optional module is simply absent and this function remains a no-op.  When
    it is installed but cannot load, callers must not continue into Paddle and
    poison the process for ASR.
    """

    prepare_nvidia_cuda_runtime()
    try:
        spec = importlib.util.find_spec("ctranslate2")
    except (ImportError, ModuleNotFoundError, ValueError):
        spec = None
    if spec is None:
        return
    try:
        importlib.import_module("ctranslate2")
    except (ImportError, OSError, RuntimeError, ValueError) as error:
        raise RuntimeError(
            "CTranslate2 must load before PaddlePaddle on Windows, but its "
            f"runtime preload failed: {_failure_reason(error)}"
        ) from error


@lru_cache(maxsize=1)
def detect_acceleration_capabilities() -> AccelerationCapabilities:
    """Probe each local inference engine without initializing model weights."""

    runtime_directories = prepare_nvidia_cuda_runtime()
    return AccelerationCapabilities(
        asr=_detect_ctranslate2_cuda(runtime_directories),
        # Keep this probe after CTranslate2.  This order is a correctness
        # requirement for the shared Windows cuDNN DLL basename.
        ocr=_detect_paddle_cuda(runtime_directories),
    )


def align_execution_plan_with_acceleration(
    plan: ExecutionPlan,
    capabilities: AccelerationCapabilities,
) -> ExecutionPlan:
    """Downgrade only unavailable engines and record the exact reason."""

    updates: dict[str, object] = {}
    notes = list(plan.notes)
    asr_device = plan.asr_device
    ocr_device = plan.ocr_device
    if asr_device == "cuda" and not capabilities.asr.cuda_available:
        asr_device = "cpu"
        updates.update(asr_device="cpu", asr_compute_type="int8")
        notes.append(
            "ASR CUDA was unavailable and safely fell back to CPU: "
            f"{capabilities.asr.reason}"
        )
    elif (
        asr_device == "cuda"
        and plan.asr_compute_type != "default"
        and capabilities.asr.supported_compute_types
        and plan.asr_compute_type not in capabilities.asr.supported_compute_types
    ):
        fallback_compute_type = next(
            (
                item
                for item in ("int8_float16", "float16", "int8", "float32")
                if item in capabilities.asr.supported_compute_types
            ),
            "default",
        )
        updates["asr_compute_type"] = fallback_compute_type
        notes.append(
            f"ASR CUDA compute type {plan.asr_compute_type} is unsupported; "
            f"using {fallback_compute_type}."
        )
    if ocr_device == "cuda" and not capabilities.ocr.cuda_available:
        ocr_device = "cpu"
        updates["ocr_device"] = "cpu"
        notes.append(
            "OCR CUDA was unavailable and safely fell back to CPU: "
            f"{capabilities.ocr.reason}"
        )
    if asr_device != "cuda" and ocr_device != "cuda":
        updates["concurrent_gpu_stages"] = 0
    if not updates:
        return plan
    updates["notes"] = tuple(dict.fromkeys(notes))
    return plan.model_copy(update=updates)


def _detect_ctranslate2_cuda(
    runtime_directories: tuple[Path, ...],
) -> EngineAcceleration:
    paths = tuple(str(item) for item in runtime_directories)
    try:
        module = cast(_CTranslate2Module, importlib.import_module("ctranslate2"))
        count = max(0, int(module.get_cuda_device_count()))
        if count < 1:
            return EngineAcceleration(
                engine="faster-whisper/CTranslate2",
                cuda_available=False,
                runtime_directories=paths,
                reason="CTranslate2 did not find a CUDA device.",
            )
        _load_required_windows_dlls(_WINDOWS_REQUIRED_ASR_DLLS)
        compute_types = tuple(
            sorted(str(item) for item in module.get_supported_compute_types("cuda"))
        )
        if not compute_types:
            return EngineAcceleration(
                engine="faster-whisper/CTranslate2",
                cuda_available=False,
                device_count=count,
                runtime_directories=paths,
                reason="CTranslate2 reported no supported CUDA compute types.",
            )
        return EngineAcceleration(
            engine="faster-whisper/CTranslate2",
            cuda_available=True,
            device_count=count,
            supported_compute_types=compute_types,
            runtime_directories=paths,
            reason="CUDA device and required cuBLAS/cuDNN runtime libraries are available.",
        )
    except (ImportError, OSError, RuntimeError, ValueError) as error:
        return EngineAcceleration(
            engine="faster-whisper/CTranslate2",
            cuda_available=False,
            runtime_directories=paths,
            reason=_failure_reason(error),
        )


def _detect_paddle_cuda(
    runtime_directories: tuple[Path, ...],
) -> EngineAcceleration:
    paths = tuple(str(item) for item in runtime_directories)
    try:
        paddle = cast(_PaddleModule, importlib.import_module("paddle"))
        if not bool(paddle.device.is_compiled_with_cuda()):
            return EngineAcceleration(
                engine="PaddleOCR/PaddlePaddle",
                cuda_available=False,
                runtime_directories=paths,
                reason="The installed PaddlePaddle runtime is a CPU build.",
            )
        count = max(0, int(paddle.device.cuda.device_count()))
        if count < 1:
            return EngineAcceleration(
                engine="PaddleOCR/PaddlePaddle",
                cuda_available=False,
                runtime_directories=paths,
                reason="CUDA-enabled PaddlePaddle did not find a GPU.",
            )
        return EngineAcceleration(
            engine="PaddleOCR/PaddlePaddle",
            cuda_available=True,
            device_count=count,
            runtime_directories=paths,
            reason="CUDA-enabled PaddlePaddle found at least one GPU.",
        )
    except (ImportError, OSError, RuntimeError, ValueError) as error:
        return EngineAcceleration(
            engine="PaddleOCR/PaddlePaddle",
            cuda_available=False,
            runtime_directories=paths,
            reason=_failure_reason(error),
        )


def _nvidia_runtime_directories() -> tuple[Path, ...]:
    candidates: list[Path] = []
    explicit_root = os.environ.get("VIDEO2NOTES_NVIDIA_RUNTIME_ROOT")
    if explicit_root:
        root = Path(explicit_root).expanduser()
        candidates.extend(root / package / "bin" for package in _NVIDIA_RUNTIME_PACKAGES)

    for package in _NVIDIA_RUNTIME_PACKAGES:
        try:
            spec = importlib.util.find_spec(f"nvidia.{package}")
        except (ImportError, ModuleNotFoundError, ValueError):
            spec = None
        if spec is not None and spec.submodule_search_locations is not None:
            candidates.extend(Path(item) / "bin" for item in spec.submodule_search_locations)

    frozen_roots = [
        Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent)),
        Path(sys.executable).parent / "_internal",
    ]
    for root in frozen_roots:
        candidates.extend(root / "nvidia" / package / "bin" for package in _NVIDIA_RUNTIME_PACKAGES)

    resolved: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        try:
            path = candidate.resolve()
        except OSError:
            continue
        key = os.path.normcase(str(path))
        if key in seen or not path.is_dir():
            continue
        seen.add(key)
        resolved.append(path)
    return tuple(resolved)


def _load_required_windows_dlls(names: tuple[str, ...]) -> None:
    if os.name != "nt":
        return
    for name in names:
        ctypes.WinDLL(name)


def _failure_reason(error: Exception) -> str:
    detail = " ".join(str(error).split())
    return f"{type(error).__name__}: {detail}" if detail else type(error).__name__
