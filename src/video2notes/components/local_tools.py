"""Discover and bind optional programs available on the local machine.

The runtime-pack catalog describes self-contained worker archives.  A normal
desktop installation also needs to reuse programs which are already installed
by the user (FFmpeg, a browser, Python, CUDA, and so on).  This module keeps
that concern deliberately separate from archive lifecycle management:

* discovery is read-only and searches ``PATH`` plus conservative Windows
  locations;
* probing executes only a known executable with a fixed ``--version`` style
  argument and never invokes a shell;
* bindings are a small JSON file containing paths, so unbinding cannot remove
  user-owned files;
* every result includes Chinese and English labels/details so the UI can switch
  locale without exposing opaque technical exceptions.

No optional third-party package is imported while scanning.  Python modules are
inspected with :func:`importlib.util.find_spec`, which keeps the settings page
fast even when OCR/ASR packages are not installed.
"""

from __future__ import annotations

import importlib.metadata
import importlib.util
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import threading
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from .runtime_models import (
    LocalToolBinding,
    LocalToolCandidate,
    LocalToolInventory,
    LocalToolResult,
)

LocalToolStatus = Literal["ready", "missing", "incompatible", "error"]
LocalToolSource = Literal["binding", "path", "common", "python", "system", "none"]
LocalToolKind = Literal["executable", "python_module", "cuda_runtime"]


@dataclass(frozen=True, slots=True)
class _ToolSpec:
    dependency_id: str
    display_name: str
    display_name_zh: str
    kind: LocalToolKind
    executable_names: tuple[str, ...] = ()
    module_names: tuple[str, ...] = ()
    distribution_names: tuple[str, ...] = ()
    capabilities: tuple[str, ...] = ()
    version_args: tuple[tuple[str, ...], ...] = (("--version",), ("-version",))
    cuda: bool = False


# Keep these IDs aligned with runtime preflight requirements where possible.
# The UI can therefore show one dependency next to its corresponding feature.
TOOL_SPECS: tuple[_ToolSpec, ...] = (
    _ToolSpec(
        "tool.ffmpeg",
        "FFmpeg",
        "视频处理（FFmpeg）",
        "executable",
        ("ffmpeg",),
        capabilities=("tool.ffmpeg",),
    ),
    _ToolSpec(
        "tool.ffprobe",
        "FFprobe",
        "媒体探测（FFprobe）",
        "executable",
        ("ffprobe",),
        capabilities=("tool.ffprobe",),
    ),
    _ToolSpec(
        "download.ytdlp",
        "yt-dlp",
        "多平台下载（yt-dlp）",
        "executable",
        ("yt-dlp", "yt_dlp"),
        module_names=("yt_dlp",),
        distribution_names=("yt-dlp",),
        capabilities=("download.ytdlp",),
    ),
    _ToolSpec(
        "render.chromium_pdf",
        "Chromium browser",
        "网页/PDF 浏览器（Chromium）",
        "executable",
        ("msedge", "chrome", "chromium"),
        capabilities=("render.chromium_pdf",),
    ),
    _ToolSpec(
        "runtime.python",
        "Python",
        "Python 运行时",
        "executable",
        ("python", "python3", "py"),
        capabilities=("runtime.python",),
        version_args=(("--version",),),
    ),
    _ToolSpec(
        "asr.faster_whisper",
        "faster-whisper",
        "语音识别（faster-whisper）",
        "python_module",
        module_names=("faster_whisper",),
        distribution_names=("faster-whisper",),
        capabilities=("asr.faster_whisper",),
        cuda=True,
    ),
    _ToolSpec(
        "asr.ctranslate2",
        "CTranslate2",
        "语音推理引擎（CTranslate2）",
        "python_module",
        module_names=("ctranslate2",),
        distribution_names=("ctranslate2",),
        capabilities=("asr.ctranslate2",),
        cuda=True,
    ),
    _ToolSpec(
        "ocr.paddleocr",
        "PaddleOCR",
        "画面文字识别（PaddleOCR）",
        "python_module",
        module_names=("paddleocr",),
        distribution_names=("paddleocr",),
        capabilities=("ocr.paddleocr",),
        cuda=True,
    ),
    _ToolSpec(
        "ocr.paddlepaddle",
        "PaddlePaddle",
        "OCR 推理引擎（PaddlePaddle）",
        "python_module",
        module_names=("paddle",),
        distribution_names=("paddlepaddle", "paddlepaddle-gpu"),
        capabilities=("ocr.paddlepaddle",),
        cuda=True,
    ),
    _ToolSpec(
        "acceleration.cuda",
        "CUDA / NVIDIA runtime",
        "CUDA / NVIDIA 加速运行时",
        "cuda_runtime",
        executable_names=("nvidia-smi", "nvcc"),
        module_names=("torch", "numba.cuda"),
        distribution_names=("torch", "nvidia-cuda-runtime-cu12"),
        capabilities=("acceleration.cuda",),
        cuda=True,
    ),
)

_SPEC_BY_ID = {item.dependency_id: item for item in TOOL_SPECS}
_VERSION_PATTERN = re.compile(r"(?<!\d)(\d+(?:\.\d+){1,5}(?:[-+._][0-9A-Za-z.-]+)?)")
_LABELLED_VERSION_PATTERN = re.compile(r"(?i)\bversion\s+([^\s,;]+)")


class LocalToolManagerError(RuntimeError):
    """Base class for local tool management failures."""


class LocalToolNotFoundError(LocalToolManagerError):
    """The requested dependency ID is not in the supported catalog."""


class LocalToolPathError(LocalToolManagerError):
    """A manually selected path is not usable for the requested dependency."""


class LocalToolManager:
    """Read-only scanner plus a small persisted binding registry."""

    def __init__(
        self,
        data_root: str | Path,
        *,
        extra_search_paths: tuple[str | Path, ...] = (),
        probe_timeout_seconds: float = 3.0,
    ) -> None:
        if probe_timeout_seconds <= 0 or probe_timeout_seconds > 30:
            raise ValueError("probe timeout must be between 0 and 30 seconds")
        self.data_root = Path(data_root).expanduser().resolve()
        self.config_root = (self.data_root / "config").resolve()
        self.config_path = (self.config_root / "local-tools.json").resolve()
        self.config_root.mkdir(parents=True, exist_ok=True)
        self.extra_search_paths = tuple(Path(item).expanduser() for item in extra_search_paths)
        self.probe_timeout_seconds = probe_timeout_seconds
        self._lock = threading.RLock()
        self._cached: LocalToolInventory | None = None

    def inventory(self) -> LocalToolInventory:
        """Return the most recent snapshot, scanning once on first use."""

        with self._lock:
            if self._cached is None:
                return self.discover()
            # Re-read bindings so a settings edit is reflected immediately.
            bindings = self._read_bindings()
            if bindings != self._cached.bindings:
                self._cached = self._apply_bindings(self._cached, bindings)
            return self._cached

    def discover(self) -> LocalToolInventory:
        """Scan PATH, common Windows locations, and importable Python modules."""

        with self._lock:
            checked_at = _utc_now()
            bindings = self._read_bindings()
            results = tuple(
                self._probe_spec(spec, bindings.get(spec.dependency_id)) for spec in TOOL_SPECS
            )
            self._cached = LocalToolInventory(
                tools=results,
                bindings=bindings,
                scanned_at_utc=checked_at,
                platform=platform.system(),
                architecture=platform.machine(),
            )
            return self._cached

    def bind(self, dependency_id: str, path: str | Path) -> LocalToolResult:
        """Bind a user-selected executable, directory, Python, or CUDA path."""

        spec = _SPEC_BY_ID.get(dependency_id)
        if spec is None:
            raise LocalToolNotFoundError(f"unknown local dependency: {dependency_id}")
        selected = Path(path).expanduser().resolve()
        if not selected.exists():
            raise LocalToolPathError("selected dependency path does not exist")
        if selected.is_dir() and spec.kind == "executable":
            executable = self._find_in_directory(spec, selected)
            if executable is None:
                raise LocalToolPathError(
                    f"selected directory does not contain {', '.join(spec.executable_names)}"
                )
            selected = executable
        if selected.is_dir() and spec.kind == "cuda_runtime":
            executable = self._find_in_directory(spec, selected)
            if executable is None:
                executable = self._find_in_directory(spec, selected / "bin")
            if executable is not None:
                selected = executable
        # A Python executable is a useful binding anchor for an isolated venv.
        if spec.kind == "python_module" and selected.is_file() and not _looks_like_python(selected):
            raise LocalToolPathError("please choose a Python executable or site-packages directory")
        if spec.kind == "executable" and not selected.is_file():
            raise LocalToolPathError("please choose an executable file or its containing directory")

        candidate = self._probe_candidate(spec, selected, source="binding")
        if not candidate.compatible:
            raise LocalToolPathError(
                candidate.detail_zh or candidate.detail or "selected path is not compatible"
            )
        binding = LocalToolBinding(
            dependency_id=dependency_id,
            path=str(selected),
            kind=spec.kind,
            bound_at_utc=_utc_now(),
            last_version=candidate.version,
        )
        with self._lock:
            bindings = self._read_bindings()
            bindings[dependency_id] = binding
            self._write_bindings(bindings)
            # Re-scan so candidates and the bound marker stay accurate.
            self._cached = None
            return self.inventory().tools[_tool_index(dependency_id)]

    def unbind(self, dependency_id: str) -> bool:
        with self._lock:
            bindings = self._read_bindings()
            if dependency_id not in bindings:
                return False
            del bindings[dependency_id]
            self._write_bindings(bindings)
            self._cached = None
            self.discover()
            return True

    def binding(self, dependency_id: str) -> LocalToolBinding | None:
        return self._read_bindings().get(dependency_id)

    def _probe_spec(self, spec: _ToolSpec, binding: LocalToolBinding | None) -> LocalToolResult:
        if binding is not None:
            bound_candidate = self._probe_candidate(spec, Path(binding.path), source="binding")
            if bound_candidate.compatible:
                return _result_from_candidate(spec, bound_candidate, bound=True)
            # Keep the failed binding visible rather than silently switching to PATH.
            return _result_from_candidate(
                spec,
                bound_candidate,
                bound=True,
                status="error" if Path(binding.path).exists() else "missing",
                suggestion="Edit the binding or unbind it, then scan again.",
                suggestion_zh="修改绑定路径或解除绑定后重新检测。",
            )

        candidates = self._candidates_for(spec)
        probes: list[LocalToolCandidate] = []
        for candidate_path, source in candidates:
            candidate = self._probe_candidate(spec, candidate_path, source=source)
            probes.append(candidate)
            if candidate.compatible:
                return _result_from_candidate(spec, candidate, candidates=tuple(probes))
        if probes:
            first = probes[0]
            return _result_from_candidate(
                spec,
                first,
                status="incompatible" if first.path else "error",
                candidates=tuple(probes),
                suggestion="Choose a compatible version or bind another path.",
                suggestion_zh="请选择兼容版本的程序，或绑定新的路径。",
            )
        return LocalToolResult(
            dependency_id=spec.dependency_id,
            display_name=spec.display_name,
            display_name_zh=spec.display_name_zh,
            kind=spec.kind,
            status="missing",
            compatible=False,
            capabilities=spec.capabilities,
            cuda_supported=False,
            detail=f"{spec.display_name} was not found in PATH or common locations.",
            detail_zh=f"未在 PATH 或常见安装目录中找到 {spec.display_name_zh}。",
            suggestion="Install it, then scan again, or manually bind its path.",
            suggestion_zh="安装后点击重新检测，或手动绑定程序路径。",
            checked_at_utc=_utc_now(),
        )

    def _candidates_for(self, spec: _ToolSpec) -> tuple[tuple[Path, LocalToolSource], ...]:
        paths: list[tuple[Path, LocalToolSource]] = []
        if spec.kind == "python_module":
            # A selected Python executable/site-packages directory is still useful
            # for display, but module discovery below supplies the authoritative result.
            for module in spec.module_names:
                try:
                    module_spec = importlib.util.find_spec(module)
                except (ImportError, AttributeError, ValueError):
                    module_spec = None
                if module_spec is not None and module_spec.origin:
                    paths.append((Path(module_spec.origin), "python"))
            return _dedupe_candidates(paths)
        if spec.kind == "cuda_runtime":
            for name in spec.executable_names:
                found = shutil.which(name)
                if found:
                    paths.append((Path(found), "path"))
            for module in spec.module_names:
                try:
                    module_spec = importlib.util.find_spec(module)
                except (ImportError, AttributeError, ValueError):
                    module_spec = None
                if module_spec is not None and module_spec.origin:
                    paths.append((Path(module_spec.origin), "python"))
            return _dedupe_candidates(paths)

        if spec.dependency_id == "runtime.python":
            paths.append((Path(sys.executable), "system"))
        for name in spec.executable_names:
            found = shutil.which(name)
            if found:
                paths.append((Path(found), "path"))
        for directory in self._common_directories(spec):
            found_path = self._find_in_directory(spec, directory)
            if found_path is not None:
                paths.append((found_path, "common"))
        for module in spec.module_names:
            try:
                module_spec = importlib.util.find_spec(module)
            except (ImportError, AttributeError, ValueError):
                module_spec = None
            if module_spec is not None and module_spec.origin:
                paths.append((Path(module_spec.origin), "python"))
        return _dedupe_candidates(paths)

    def _common_directories(self, spec: _ToolSpec) -> tuple[Path, ...]:
        runtime_root = Path(sys.executable).resolve().parent
        roots: list[Path] = [
            Path.cwd(),
            self.data_root,
            runtime_root,
            runtime_root / "tools",
            runtime_root / "runtime",
            *self.extra_search_paths,
        ]
        if os.name == "nt":
            env = os.environ
            for key in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA", "APPDATA"):
                value = env.get(key)
                if value:
                    roots.append(Path(value))
            roots.extend(
                (
                    Path(r"C:\ProgramData\chocolatey\bin"),
                    Path(env.get("USERPROFILE", "")) / "scoop" / "shims",
                    Path(env.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Packages",
                )
            )
            if spec.dependency_id == "render.chromium_pdf":
                for key in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
                    base = env.get(key)
                    if not base:
                        continue
                    roots.extend(
                        (
                            Path(base) / "Microsoft" / "Edge" / "Application",
                            Path(base) / "Google" / "Chrome" / "Application",
                            Path(base) / "Chromium" / "Application",
                        )
                    )
            if spec.dependency_id in {"tool.ffmpeg", "tool.ffprobe"}:
                roots.extend((Path(r"C:\ffmpeg\bin"), Path(r"C:\Program Files\ffmpeg\bin")))
        expanded: list[Path] = []
        names = {name.casefold() for name in spec.executable_names}
        for root in roots:
            if not str(root):
                continue
            candidate = root.expanduser()
            if not candidate.exists() or not candidate.is_dir():
                continue
            expanded.append(candidate)
            # Search a shallow set of conventional ``bin``/``Scripts`` folders;
            # avoid walking an entire user profile on every settings refresh.
            for child in _safe_children(candidate, max_count=160):
                if child.is_dir() and child.name.casefold() in {
                    "bin",
                    "scripts",
                    "application",
                    "current",
                    *names,
                }:
                    expanded.append(child)
                if child.is_dir() and child.name.casefold() in {
                    "ffmpeg",
                    "yt-dlp",
                    "yt_dlp",
                    "google",
                    "microsoft",
                    "chromium",
                }:
                    expanded.append(child)
                    for grandchild in _safe_children(child, max_count=80):
                        if grandchild.is_dir() and grandchild.name.casefold() in {
                            "bin",
                            "application",
                            "current",
                        }:
                            expanded.append(grandchild)
        return tuple(dict.fromkeys(item.resolve() for item in expanded))

    def _find_in_directory(self, spec: _ToolSpec, directory: Path) -> Path | None:
        names = {name.casefold() for name in spec.executable_names}
        for child in _safe_children(directory, max_count=240):
            if not child.is_file():
                continue
            stem = child.stem.casefold()
            name = child.name.casefold()
            if stem in names or name in names or (os.name == "nt" and f"{stem}.exe" in names):
                return child.resolve()
        return None

    def _probe_candidate(
        self,
        spec: _ToolSpec,
        path: Path,
        *,
        source: LocalToolSource,
    ) -> LocalToolCandidate:
        resolved = path.expanduser().resolve()
        if spec.kind == "python_module":
            return self._probe_python_module(spec, resolved, source)
        if spec.kind == "cuda_runtime":
            return self._probe_cuda(spec, resolved, source)
        if source == "python" and spec.module_names:
            return self._probe_python_module(spec, resolved, source)
        if not resolved.is_file():
            return LocalToolCandidate(
                path=str(resolved),
                source=source,
                compatible=False,
                detail="Executable path is missing or not a regular file.",
                detail_zh="程序路径不存在，或不是普通文件。",
            )
        expected_names = {name.casefold() for name in spec.executable_names}
        if source == "binding" and resolved.stem.casefold() not in expected_names:
            return LocalToolCandidate(
                path=str(resolved),
                source=source,
                compatible=False,
                detail=f"Expected one of: {', '.join(spec.executable_names)}.",
                detail_zh=f"所选程序不是预期的 {', '.join(spec.executable_names)}。",
            )
        version, output, error = _run_version(
            resolved, spec.version_args, self.probe_timeout_seconds
        )
        compatible = error is None
        detail = error or output or None
        detail_zh = (
            f"已检测到{spec.display_name_zh}，程序可以正常启动。"
            if compatible
            else "程序无法启动或版本信息不可读取。"
        )
        return LocalToolCandidate(
            path=str(resolved),
            source=source,
            version=version,
            compatible=compatible,
            detail=detail[:500] if detail else None,
            detail_zh=detail_zh,
        )

    def _probe_python_module(
        self,
        spec: _ToolSpec,
        path: Path,
        source: LocalToolSource,
    ) -> LocalToolCandidate:
        if path.is_file() and _looks_like_python(path):
            return _probe_module_with_python(
                path,
                spec,
                source=source,
                timeout_seconds=self.probe_timeout_seconds,
            )
        if path.is_dir():
            selected_module_path = _module_in_directory(path, spec.module_names)
            if selected_module_path is None:
                return LocalToolCandidate(
                    path=str(path),
                    source=source,
                    compatible=False,
                    detail=f"The directory does not contain {', '.join(spec.module_names)}.",
                    detail_zh=f"所选目录不包含模块：{', '.join(spec.module_names)}。",
                )
            return LocalToolCandidate(
                path=str(path),
                source=source,
                version=_distribution_version_in_directory(path, spec.distribution_names),
                compatible=True,
                detail=f"Python module found at {selected_module_path}.",
                detail_zh=f"已在所选目录找到 Python 模块：{selected_module_path.name}。",
            )
        available = False
        version: str | None = None
        module_path: Path | None = None
        for module in spec.module_names:
            try:
                module_spec = importlib.util.find_spec(module)
            except (ImportError, AttributeError, ValueError):
                module_spec = None
            if module_spec is not None:
                available = True
                module_path = Path(module_spec.origin).resolve() if module_spec.origin else path
                for distribution in spec.distribution_names:
                    try:
                        version = importlib.metadata.version(distribution)
                    except importlib.metadata.PackageNotFoundError:
                        continue
                    break
                break
        if not available:
            return LocalToolCandidate(
                path=str(path),
                source=source,
                compatible=False,
                detail=f"Python module {', '.join(spec.module_names)} is not importable.",
                detail_zh=f"未找到可导入的 Python 模块：{', '.join(spec.module_names)}。",
            )
        return LocalToolCandidate(
            path=str(module_path or path),
            source=source,
            version=version,
            compatible=True,
            detail="Python module is importable.",
            detail_zh="Python 模块可以正常导入。",
        )

    def _probe_cuda(
        self,
        spec: _ToolSpec,
        path: Path,
        source: LocalToolSource,
    ) -> LocalToolCandidate:
        # ``nvidia-smi`` is the least invasive and most reliable Windows check.
        if path.name.casefold() in {"nvidia-smi.exe", "nvidia-smi"} and path.is_file():
            version, output, error = _run_version(
                path,
                (("--query-gpu=driver_version", "--format=csv,noheader"), ("--version",)),
                self.probe_timeout_seconds,
            )
            if error is None:
                return LocalToolCandidate(
                    path=str(path),
                    source=source,
                    version=version,
                    compatible=True,
                    detail=output,
                    detail_zh="已检测到 NVIDIA 驱动，可使用 CUDA。",
                )
            return LocalToolCandidate(
                path=str(path),
                source=source,
                compatible=False,
                detail=error,
                detail_zh="NVIDIA 驱动探测失败。",
            )
        if path.name.casefold() in {"nvcc.exe", "nvcc"} and path.is_file():
            version, output, error = _run_version(
                path,
                (("--version",),),
                self.probe_timeout_seconds,
            )
            return LocalToolCandidate(
                path=str(path),
                source=source,
                version=version,
                compatible=error is None,
                detail=output or error,
                detail_zh=(
                    "已检测到 CUDA Toolkit。" if error is None else "CUDA Toolkit 探测失败。"
                ),
            )
        if path.is_file() and path.suffix.casefold() in {".py", ".pyd", ".so", ".dll"}:
            return LocalToolCandidate(
                path=str(path),
                source=source,
                compatible=True,
                detail="CUDA-capable Python runtime candidate found.",
                detail_zh="已找到可能支持 CUDA 的 Python 运行时。",
            )
        return LocalToolCandidate(
            path=str(path),
            source=source,
            compatible=False,
            detail="CUDA runtime candidate is unavailable.",
            detail_zh="未确认 CUDA 运行时。",
        )

    def _read_bindings(self) -> dict[str, LocalToolBinding]:
        try:
            payload = json.loads(self.config_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (OSError, json.JSONDecodeError):
            return {}
        raw = payload.get("bindings", {}) if isinstance(payload, dict) else {}
        if not isinstance(raw, dict):
            return {}
        bindings: dict[str, LocalToolBinding] = {}
        for key, value in raw.items():
            try:
                binding = LocalToolBinding.model_validate(value)
            except (TypeError, ValueError):
                continue
            if binding.dependency_id == str(key) and binding.dependency_id in _SPEC_BY_ID:
                bindings[binding.dependency_id] = binding
        return bindings

    def _write_bindings(self, bindings: dict[str, LocalToolBinding]) -> None:
        self.config_root.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": 1,
            "bindings": {key: value.model_dump(mode="json") for key, value in bindings.items()},
        }
        temporary = self.config_path.with_name(f".{self.config_path.name}.{uuid.uuid4().hex}.tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, self.config_path)

    @staticmethod
    def _apply_bindings(
        snapshot: LocalToolInventory, bindings: dict[str, LocalToolBinding]
    ) -> LocalToolInventory:
        by_id = {item.dependency_id: item for item in snapshot.tools}
        updated: list[LocalToolResult] = []
        for spec in TOOL_SPECS:
            item = by_id.get(spec.dependency_id)
            binding = bindings.get(spec.dependency_id)
            if item is None:
                continue
            updated.append(
                item.model_copy(
                    update={
                        "bound": binding is not None,
                        "source": "binding" if binding else item.source,
                        "path": binding.path if binding else item.path,
                    }
                )
            )
        return snapshot.model_copy(update={"tools": tuple(updated), "bindings": bindings})


def _result_from_candidate(
    spec: _ToolSpec,
    candidate: LocalToolCandidate,
    *,
    bound: bool = False,
    candidates: tuple[LocalToolCandidate, ...] = (),
    status: LocalToolStatus | None = None,
    suggestion: str | None = None,
    suggestion_zh: str | None = None,
) -> LocalToolResult:
    resolved_status: LocalToolStatus = status or (
        "ready" if candidate.compatible else "incompatible"
    )
    cuda = (
        candidate.compatible
        and spec.cuda
        and (spec.dependency_id == "acceleration.cuda" or shutil.which("nvidia-smi") is not None)
    )
    return LocalToolResult(
        dependency_id=spec.dependency_id,
        display_name=spec.display_name,
        display_name_zh=spec.display_name_zh,
        kind=spec.kind,
        status=resolved_status,
        compatible=candidate.compatible,
        path=candidate.path,
        version=candidate.version,
        source=candidate.source,
        capabilities=spec.capabilities,
        cpu_supported=True,
        cuda_supported=cuda,
        bound=bound,
        detail=candidate.detail,
        detail_zh=candidate.detail_zh,
        suggestion=suggestion
        or (
            None
            if candidate.compatible
            else "Bind a compatible executable or install the optional dependency."
        ),
        suggestion_zh=suggestion_zh
        or (None if candidate.compatible else "绑定兼容程序路径，或安装这个可选依赖。"),
        candidates=candidates,
        checked_at_utc=_utc_now(),
    )


def _tool_index(dependency_id: str) -> int:
    for index, spec in enumerate(TOOL_SPECS):
        if spec.dependency_id == dependency_id:
            return index
    raise LocalToolNotFoundError(dependency_id)


def _dedupe_candidates(
    items: list[tuple[Path, LocalToolSource]],
) -> tuple[tuple[Path, LocalToolSource], ...]:
    seen: set[str] = set()
    output: list[tuple[Path, LocalToolSource]] = []
    for path, source in items:
        try:
            resolved = path.expanduser().resolve()
        except (OSError, RuntimeError):
            continue
        key = str(resolved).casefold()
        if key in seen:
            continue
        seen.add(key)
        output.append((resolved, source))
    return tuple(output)


def _safe_children(directory: Path, *, max_count: int) -> tuple[Path, ...]:
    try:
        children = sorted(directory.iterdir(), key=lambda item: item.name.casefold())
    except (OSError, PermissionError):
        return ()
    return tuple(children[:max_count])


def _probe_module_with_python(
    executable: Path,
    spec: _ToolSpec,
    *,
    source: LocalToolSource,
    timeout_seconds: float,
) -> LocalToolCandidate:
    # Query versions separately in the generated expression.  Keeping the
    # child process independent avoids importing heavyweight OCR/ASR engines.
    version_script = (
        "import importlib.metadata,importlib.util,json,sys;"
        "mods=json.loads(sys.argv[1]);dists=json.loads(sys.argv[2]);"
        "found=next((m for m in mods if importlib.util.find_spec(m) is not None),None);"
        "version=None;"
        "\nfor d in dists:\n"
        " try:\n  version=importlib.metadata.version(d);break\n"
        " except importlib.metadata.PackageNotFoundError:\n  pass\n"
        "print(json.dumps({'module':found,'version':version}))"
    )
    try:
        completed = subprocess.run(
            [
                str(executable),
                "-c",
                version_script,
                json.dumps(spec.module_names),
                json.dumps(spec.distribution_names),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
            check=False,
            shell=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError) as error:
        return LocalToolCandidate(
            path=str(executable),
            source=source,
            compatible=False,
            detail=f"{type(error).__name__}: {error}",
            detail_zh="所选 Python 运行时无法启动。",
        )
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError:
        payload = {}
    found = payload.get("module") if isinstance(payload, dict) else None
    version = payload.get("version") if isinstance(payload, dict) else None
    if completed.returncode != 0 or not isinstance(found, str):
        detail = (completed.stderr or completed.stdout or "module was not found").strip()
        return LocalToolCandidate(
            path=str(executable),
            source=source,
            compatible=False,
            detail=detail[:500],
            detail_zh=f"所选 Python 中没有模块：{', '.join(spec.module_names)}。",
        )
    return LocalToolCandidate(
        path=str(executable),
        source=source,
        version=str(version) if version is not None else None,
        compatible=True,
        detail=f"Python module {found} is available in this interpreter.",
        detail_zh=f"所选 Python 运行时可以导入模块 {found}。",
    )


def _module_in_directory(directory: Path, module_names: tuple[str, ...]) -> Path | None:
    for module_name in module_names:
        relative = Path(*module_name.split("."))
        module_file = directory / relative.with_suffix(".py")
        package = directory / relative / "__init__.py"
        if module_file.is_file():
            return module_file
        if package.is_file():
            return package
    return None


def _distribution_version_in_directory(
    directory: Path,
    distribution_names: tuple[str, ...],
) -> str | None:
    normalized = {name.casefold().replace("-", "_") for name in distribution_names}
    for metadata_directory in directory.glob("*.dist-info"):
        stem = metadata_directory.name.removesuffix(".dist-info")
        name, separator, version = stem.rpartition("-")
        if separator and name.casefold().replace("-", "_") in normalized:
            return version
    return None


def _run_version(
    executable: Path,
    argument_sets: tuple[tuple[str, ...], ...],
    timeout_seconds: float,
) -> tuple[str | None, str, str | None]:
    last_error: str | None = None
    output = ""
    for arguments in argument_sets:
        try:
            completed = subprocess.run(
                [str(executable), *arguments],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout_seconds,
                check=False,
                shell=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except (OSError, subprocess.SubprocessError) as error:
            last_error = f"{type(error).__name__}: {error}"
            continue
        output = (completed.stdout or completed.stderr or "").strip()
        if completed.returncode == 0:
            return _parse_version(output), output[:500], None
        last_error = f"exit code {completed.returncode}"
    return _parse_version(output), output[:500], last_error or "version probe failed"


def _parse_version(text: str) -> str | None:
    labelled = _LABELLED_VERSION_PATTERN.search(text)
    if labelled:
        return labelled.group(1)
    match = _VERSION_PATTERN.search(text)
    return match.group(1) if match else None


def _looks_like_python(path: Path) -> bool:
    name = path.name.casefold()
    return name.startswith("python") or name in {"py.exe", "py"}


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


__all__ = [
    "LocalToolBinding",
    "LocalToolCandidate",
    "LocalToolInventory",
    "LocalToolManager",
    "LocalToolManagerError",
    "LocalToolNotFoundError",
    "LocalToolPathError",
    "LocalToolResult",
    "TOOL_SPECS",
]
