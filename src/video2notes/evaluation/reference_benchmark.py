"""Reproducible, resource-guarded Fast/Balanced/Accurate reference benchmark.

The parent process only prepares a local-only registry and launches three serial
workers.  Every worker is placed behind :mod:`video2notes.system.benchmark_guard`
before it imports an inference runtime.  This keeps the benchmark useful on a
developer workstation without pretending that device-wide GPU telemetry is a
hard per-process limiter.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import importlib.metadata
import json
import math
import os
import platform
import shutil
import subprocess
import sys
import uuid
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, cast

from video2notes.evaluation.diagnostics import compare_runs
from video2notes.evaluation.reference_analysis import write_reference_analysis
from video2notes.evaluation.render import render_comparison_markdown, render_json
from video2notes.pipeline import PipelineRequest, Video2NotesPipeline
from video2notes.providers import ModelRegistry
from video2notes.runtime import build_pipeline_runtime
from video2notes.sources import AcquisitionPolicy, SourceInput
from video2notes.sources.models import QualityMode as AcquisitionQualityMode
from video2notes.system import (
    ExperienceMode,
    PerformanceOverrides,
    QualityMode,
    ResourcePreference,
    detect_acceleration_capabilities,
    detect_hardware,
)
from video2notes.system.benchmark_guard import (
    BenchmarkGuardConfig,
    BenchmarkResourceReport,
    run_guarded_benchmark,
)

REFERENCE_URL = "https://www.bilibili.com/video/BV12hsEz3ELL"
REFERENCE_TITLE = "bro问我怎么多平台直播和发布视频"
_PROFILES = (QualityMode.FAST, QualityMode.BALANCED, QualityMode.ACCURATE)
InferenceDevice = Literal["cpu", "cuda"]
AsrComputeType = Literal["default", "int8", "int8_float16", "float16", "float32"]


class ReferenceBenchmarkError(RuntimeError):
    """A benchmark session could not produce a comparable three-profile set."""


def prepare_local_registry(
    destination: str | Path,
    *,
    asr_model_dir: str | Path,
    ocr_detection_model_dir: str | Path,
    ocr_recognition_model_dir: str | Path,
    asr_device: InferenceDevice = "cpu",
    asr_compute_type: AsrComputeType = "int8",
    ocr_device: InferenceDevice = "cpu",
) -> Path:
    """Write a local-only benchmark registry after validating all model payloads."""

    asr = _require_model_directory(asr_model_dir, required_file="model.bin")
    detection = _require_model_directory(
        ocr_detection_model_dir,
        required_file="inference.pdiparams",
    )
    recognition = _require_model_directory(
        ocr_recognition_model_dir,
        required_file="inference.pdiparams",
    )
    registry = ModelRegistry.with_local_defaults()
    registry.models["faster-whisper"].settings = {
        "engine": "faster_whisper",
        "model_path": str(asr),
        "device": asr_device,
        "compute_type": asr_compute_type,
        "beam_size": 5,
    }
    registry.models["paddleocr"].settings = {
        "engine": "paddleocr",
        "detection_model_dir": str(detection),
        "recognition_model_dir": str(recognition),
        "language": "ch",
        "device": "gpu:0" if ocr_device == "cuda" else ocr_device,
        "enable_mkldnn": False,
        "api_family": "auto",
    }
    return registry.save(destination)


def run_reference_session(
    *,
    source: str | Path,
    session_root: str | Path,
    asr_model_dir: str | Path,
    ocr_detection_model_dir: str | Path,
    ocr_recognition_model_dir: str | Path,
    max_cpu_ratio: float = 0.25,
    timeout_seconds: float = 7_200,
    ffmpeg_path: str = "ffmpeg",
    ffprobe_path: str = "ffprobe",
    reference_url: str = REFERENCE_URL,
    title: str = REFERENCE_TITLE,
    language_hints: Sequence[str] = ("zh",),
    python_executable: str | Path = sys.executable,
    working_directory: str | Path | None = None,
    asr_device: InferenceDevice = "cpu",
    asr_compute_type: AsrComputeType = "int8",
    ocr_device: InferenceDevice = "cpu",
    gpu_watchdog_percent: float | None = 95.0,
    gpu_breach_samples: int = 8,
) -> Path:
    """Run all quality profiles serially and return the comparison Markdown path."""

    source_path = Path(source).expanduser().resolve()
    if not source_path.is_file():
        raise FileNotFoundError(f"benchmark source does not exist: {source_path}")
    root = Path(session_root).expanduser().resolve()
    if root.exists() and any(root.iterdir()):
        raise FileExistsError(f"benchmark session root is not empty: {root}")
    root.mkdir(parents=True, exist_ok=True)
    reports_root = root / "reports"
    reports_root.mkdir()
    data_root = root / "data"
    registry_path = prepare_local_registry(
        data_root / "config" / "providers.json",
        asr_model_dir=asr_model_dir,
        ocr_detection_model_dir=ocr_detection_model_dir,
        ocr_recognition_model_dir=ocr_recognition_model_dir,
        asr_device=asr_device,
        asr_compute_type=asr_compute_type,
        ocr_device=ocr_device,
    )
    logical_processors = max(1, os.cpu_count() or 1)
    cpu_threads = max(1, math.floor(logical_processors * max_cpu_ratio))
    force_cpu = asr_device == "cpu" and ocr_device == "cpu"
    guard_config = BenchmarkGuardConfig(
        max_cpu_ratio=max_cpu_ratio,
        force_cpu=force_cpu,
        poll_interval_seconds=0.5,
        baseline_samples=4,
        gpu_watchdog_percent=gpu_watchdog_percent,
        gpu_breach_samples=gpu_breach_samples,
        timeout_seconds=timeout_seconds,
    )
    manifest = {
        "schema_version": 1,
        "created_at": datetime.now(UTC).isoformat(),
        "reference_url": reference_url,
        "title": title,
        "source": {
            "path": str(source_path),
            "sha256": _sha256_file(source_path),
            "size_bytes": source_path.stat().st_size,
        },
        "models": {
            "asr": _model_identity(
                Path(asr_model_dir).expanduser().resolve(),
                "model.bin",
            ),
            "ocr_detection": _model_identity(
                Path(ocr_detection_model_dir).expanduser().resolve(),
                "inference.pdiparams",
            ),
            "ocr_recognition": _model_identity(
                Path(ocr_recognition_model_dir).expanduser().resolve(),
                "inference.pdiparams",
            ),
        },
        "runtime": _runtime_identity(),
        "acceleration_probe": detect_acceleration_capabilities().model_dump(mode="json"),
        "profiles": [item.value for item in _PROFILES],
        "resource_policy": {
            "max_cpu_ratio": max_cpu_ratio,
            "force_cpu": force_cpu,
            "cpu_threads": cpu_threads,
            "gpu_watchdog_percent": gpu_watchdog_percent,
            "gpu_breach_samples": gpu_breach_samples,
            "requested_asr_device": asr_device,
            "requested_asr_compute_type": asr_compute_type,
            "requested_ocr_device": ocr_device,
        },
    }
    _atomic_write_json(root / "benchmark-manifest.json", manifest)

    run_directories: list[Path] = []
    cwd = Path(working_directory or Path.cwd()).expanduser().resolve()
    for profile in _PROFILES:
        profile_runs_root = root / "runs" / profile.value
        result_path = reports_root / f"{profile.value}.worker.json"
        command = [
            str(Path(python_executable).expanduser().resolve()),
            "-m",
            "video2notes.evaluation.reference_benchmark",
            "--worker",
            "--source",
            str(source_path),
            "--registry",
            str(registry_path),
            "--runs-root",
            str(profile_runs_root),
            "--result",
            str(result_path),
            "--profile",
            profile.value,
            "--cpu-threads",
            str(cpu_threads),
            "--asr-device",
            asr_device,
            "--asr-compute-type",
            asr_compute_type,
            "--ocr-device",
            ocr_device,
            "--ffmpeg",
            ffmpeg_path,
            "--ffprobe",
            ffprobe_path,
            "--title",
            title,
        ]
        for hint in language_hints:
            command.extend(("--language", hint))
        report = run_guarded_benchmark(
            command,
            config=guard_config,
            cwd=cwd,
        )
        _atomic_write_text(
            reports_root / f"{profile.value}.resource.json",
            report.model_dump_json(indent=2),
        )
        worker = _load_worker_result(result_path)
        _require_successful_worker(profile, report, worker)
        run_directory = Path(str(worker["run_directory"])).resolve()
        run_directories.append(run_directory)

    comparison = compare_runs(run_directories)
    _atomic_write_text(root / "comparison.json", render_json(comparison))
    comparison_path = root / "comparison.md"
    _atomic_write_text(comparison_path, render_comparison_markdown(comparison))
    detailed_json_path, detailed_markdown_path = write_reference_analysis(
        root,
        run_directories,
    )
    _atomic_write_json(
        root / "session-result.json",
        {
            "schema_version": 1,
            "status": "completed",
            "comparison_markdown": str(comparison_path),
            "comparison_json": str(root / "comparison.json"),
            "detailed_comparison_markdown": str(detailed_markdown_path),
            "detailed_comparison_json": str(detailed_json_path),
            "run_directories": [str(item) for item in run_directories],
        },
    )
    return comparison_path


def run_worker(
    *,
    source: str | Path,
    registry_path: str | Path,
    runs_root: str | Path,
    result_path: str | Path,
    profile: QualityMode,
    cpu_threads: int,
    ffmpeg_path: str,
    ffprobe_path: str,
    title: str,
    language_hints: Sequence[str],
    asr_device: InferenceDevice,
    asr_compute_type: AsrComputeType,
    ocr_device: InferenceDevice,
) -> int:
    """Run one profile.  This function is invoked only inside the guarded child."""

    result = Path(result_path).expanduser().resolve()
    workspace_root: Path | None = None
    try:
        source_path = Path(source).expanduser().resolve()
        registry = ModelRegistry.load(registry_path)
        resolved_runs_root = Path(runs_root).expanduser().resolve()
        resolved_runs_root.mkdir(parents=True, exist_ok=True)
        hardware = detect_hardware(disk_path=resolved_runs_root)
        threads = max(1, cpu_threads)
        built = build_pipeline_runtime(
            registry,
            hardware=hardware,
            experience_mode=ExperienceMode.PROFESSIONAL,
            resource_preference=ResourcePreference.RESPONSIVE,
            performance_overrides=PerformanceOverrides(
                concurrent_gpu_stages=(
                    1 if asr_device == "cuda" or ocr_device == "cuda" else 0
                ),
                cpu_workers=threads,
                visual_decode_threads=min(threads, 8),
                ocr_device=ocr_device,
                ocr_cpu_threads=min(threads, 4),
                asr_device=asr_device,
                asr_compute_type=asr_compute_type,
                asr_cpu_threads=min(threads, 8),
            ),
            ffmpeg_path=ffmpeg_path,
            ffprobe_path=ffprobe_path,
        )
        pipeline = Video2NotesPipeline(resolved_runs_root, runtime=built.runtime)
        acquisition_mode = (
            AcquisitionQualityMode.FAST
            if profile is QualityMode.FAST
            else AcquisitionQualityMode.ACCURATE
        )
        request = PipelineRequest(
            source=SourceInput.local(source_path),
            acquisition=AcquisitionPolicy(
                mode=acquisition_mode,
                max_height=1080 if profile is QualityMode.BALANCED else None,
            ),
            quality_mode=profile,
            title_override=title,
            language_hints=list(language_hints),
            include_screenshots=True,
            generate_pdf=False,
        )
        workspace = pipeline.create_run(request, run_id=f"reference-{profile.value}")
        workspace_root = workspace.root

        def emit(
            stage: str,
            *,
            progress: float | None = None,
            message: str | None = None,
            metrics: dict[str, float | int | str | bool | None] | None = None,
        ) -> None:
            payload: dict[str, Any] = {
                "event": "progress",
                "profile": profile.value,
                "stage": stage,
                "progress": progress,
                "metrics": metrics or {},
            }
            if message is not None:
                payload["message"] = message
            with contextlib.suppress(OSError):
                print(json.dumps(payload, ensure_ascii=False), flush=True)

        outcome = pipeline.run(workspace, request, emit=emit)
        _atomic_write_json(
            result,
            {
                "schema_version": 1,
                "status": "completed",
                "profile": profile.value,
                "run_id": outcome.run_id,
                "run_directory": str(workspace.root),
                "runtime_warnings": list(built.warnings),
            },
        )
        return 0
    except Exception as error:
        _atomic_write_json(
            result,
            {
                "schema_version": 1,
                "status": "failed",
                "profile": profile.value,
                "run_directory": str(workspace_root) if workspace_root is not None else None,
                "error_type": type(error).__name__,
            },
        )
        return 1


def _require_successful_worker(
    profile: QualityMode,
    report: BenchmarkResourceReport,
    worker: Mapping[str, object],
) -> None:
    status = worker.get("status")
    run_directory = worker.get("run_directory")
    if (
        report.exit_code != 0
        or report.termination_reason is not None
        or status != "completed"
        or not isinstance(run_directory, str)
    ):
        error_type = worker.get("error_type")
        raise ReferenceBenchmarkError(
            f"{profile.value} benchmark failed: exit={report.exit_code}, "
            f"termination={report.termination_reason}, error={error_type}"
        )


def _load_worker_result(path: Path) -> Mapping[str, object]:
    if not path.is_file():
        raise ReferenceBenchmarkError(f"benchmark worker did not write a result: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ReferenceBenchmarkError("benchmark worker result is invalid JSON") from error
    if not isinstance(payload, dict):
        raise ReferenceBenchmarkError("benchmark worker result must be a JSON object")
    return payload


def _model_identity(directory: Path, payload_name: str) -> dict[str, object]:
    payload = directory / payload_name
    return {
        "directory": str(directory),
        "payload": payload_name,
        "payload_size_bytes": payload.stat().st_size,
        "payload_sha256": _sha256_file(payload),
    }


def _runtime_identity() -> dict[str, object]:
    distributions = (
        "video2notes",
        "faster-whisper",
        "ctranslate2",
        "paddleocr",
        "paddlepaddle",
        "nvidia-cublas-cu12",
        "nvidia-cuda-nvrtc-cu12",
        "nvidia-cudnn-cu12",
    )
    versions: dict[str, str | None] = {}
    for distribution in distributions:
        try:
            versions[distribution] = importlib.metadata.version(distribution)
        except importlib.metadata.PackageNotFoundError:
            versions[distribution] = None
    revision, dirty = _git_identity(Path.cwd())
    return {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "packages": versions,
        "git_commit": revision,
        "git_tracked_changes_present": dirty,
    }


def _git_identity(cwd: Path) -> tuple[str | None, bool | None]:
    try:
        revision = subprocess.run(
            ("git", "rev-parse", "HEAD"),
            cwd=cwd,
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
        status = subprocess.run(
            ("git", "status", "--porcelain", "--untracked-files=no"),
            cwd=cwd,
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return None, None
    if revision.returncode != 0:
        return None, None
    dirty = bool(status.stdout.strip()) if status.returncode == 0 else None
    return revision.stdout.strip() or None, dirty


def _require_model_directory(value: str | Path, *, required_file: str) -> Path:
    path = Path(value).expanduser().resolve()
    payload = path / required_file
    if not path.is_dir() or not payload.is_file() or payload.stat().st_size <= 0:
        raise FileNotFoundError(f"local model payload is unavailable: {payload}")
    return path


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_write_json(path: Path, payload: Mapping[str, object]) -> None:
    _atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2))


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(text + ("" if text.endswith("\n") else "\n"), encoding="utf-8")
    os.replace(temporary, path)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the Video2Notes reference video through all quality profiles safely."
    )
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--session-root", type=Path)
    parser.add_argument("--asr-model-dir", type=Path)
    parser.add_argument("--ocr-detection-model-dir", type=Path)
    parser.add_argument("--ocr-recognition-model-dir", type=Path)
    parser.add_argument("--max-cpu-ratio", type=float, default=0.25)
    parser.add_argument("--timeout-seconds", type=float, default=7_200)
    parser.add_argument("--ffmpeg", default=shutil.which("ffmpeg") or "ffmpeg")
    parser.add_argument("--ffprobe", default=shutil.which("ffprobe") or "ffprobe")
    parser.add_argument("--reference-url", default=REFERENCE_URL)
    parser.add_argument("--title", default=REFERENCE_TITLE)
    parser.add_argument("--language", action="append", default=[])
    parser.add_argument("--asr-device", choices=("cpu", "cuda"), default="cpu")
    parser.add_argument(
        "--asr-compute-type",
        choices=("default", "int8", "int8_float16", "float16", "float32"),
    )
    parser.add_argument("--ocr-device", choices=("cpu", "cuda"), default="cpu")
    parser.add_argument("--gpu-watchdog-percent", type=float, default=95.0)
    parser.add_argument("--gpu-breach-samples", type=int, default=8)
    parser.add_argument("--disable-gpu-watchdog", action="store_true")
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--registry", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--runs-root", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--result", type=Path, help=argparse.SUPPRESS)
    parser.add_argument(
        "--profile",
        choices=[item.value for item in _PROFILES],
        help=argparse.SUPPRESS,
    )
    parser.add_argument("--cpu-threads", type=int, help=argparse.SUPPRESS)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    languages = tuple(args.language or ["zh"])
    asr_device = cast(InferenceDevice, args.asr_device)
    ocr_device = cast(InferenceDevice, args.ocr_device)
    asr_compute_type = cast(
        AsrComputeType,
        args.asr_compute_type or ("float16" if asr_device == "cuda" else "int8"),
    )
    if args.worker:
        required = {
            "registry": args.registry,
            "runs_root": args.runs_root,
            "result": args.result,
            "profile": args.profile,
            "cpu_threads": args.cpu_threads,
        }
        missing = [name for name, value in required.items() if value is None]
        if missing:
            raise SystemExit("worker arguments missing: " + ", ".join(missing))
        return run_worker(
            source=args.source,
            registry_path=args.registry,
            runs_root=args.runs_root,
            result_path=args.result,
            profile=QualityMode(args.profile),
            cpu_threads=args.cpu_threads,
            ffmpeg_path=args.ffmpeg,
            ffprobe_path=args.ffprobe,
            title=args.title,
            language_hints=languages,
            asr_device=asr_device,
            asr_compute_type=asr_compute_type,
            ocr_device=ocr_device,
        )

    required = {
        "session_root": args.session_root,
        "asr_model_dir": args.asr_model_dir,
        "ocr_detection_model_dir": args.ocr_detection_model_dir,
        "ocr_recognition_model_dir": args.ocr_recognition_model_dir,
    }
    missing = [name for name, value in required.items() if value is None]
    if missing:
        raise SystemExit("benchmark arguments missing: " + ", ".join(missing))
    comparison = run_reference_session(
        source=args.source,
        session_root=args.session_root,
        asr_model_dir=args.asr_model_dir,
        ocr_detection_model_dir=args.ocr_detection_model_dir,
        ocr_recognition_model_dir=args.ocr_recognition_model_dir,
        max_cpu_ratio=args.max_cpu_ratio,
        timeout_seconds=args.timeout_seconds,
        ffmpeg_path=args.ffmpeg,
        ffprobe_path=args.ffprobe,
        reference_url=args.reference_url,
        title=args.title,
        language_hints=languages,
        asr_device=asr_device,
        asr_compute_type=asr_compute_type,
        ocr_device=ocr_device,
        gpu_watchdog_percent=(
            None if args.disable_gpu_watchdog else args.gpu_watchdog_percent
        ),
        gpu_breach_samples=args.gpu_breach_samples,
    )
    print(str(comparison), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
