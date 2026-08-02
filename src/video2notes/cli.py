"""Windows-friendly local command line interface for Video2Notes."""

from __future__ import annotations

import argparse
import json
import os
import secrets
import stat
import sys
from collections.abc import Callable, Mapping, Sequence
from contextlib import suppress
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Never, TextIO
from urllib.parse import urlsplit

from platformdirs import user_data_path

from video2notes.pipeline import (
    PipelineOutcome,
    PipelineRequest,
    PipelineRuntime,
    ProcessingScope,
    Video2NotesPipeline,
)
from video2notes.providers import KeyringSecretStore, ModelRegistry
from video2notes.runtime import build_pipeline_runtime
from video2notes.sources import (
    AcquisitionPolicy,
    AuthSpec,
    BrowserKind,
    CancellationToken,
    SourceError,
    SourceInput,
    platform_for_url,
    redact_sensitive,
)
from video2notes.sources import (
    QualityMode as AcquisitionQualityMode,
)
from video2notes.system import (
    HardwareSnapshot,
    QualityMode,
    detect_hardware,
    recommend_hardware_tier,
)
from video2notes.vision.adaptive_sampler import (
    AdaptiveScanConfig,
    AdaptiveVideoScanner,
)

TOKEN_ENVIRONMENT_VARIABLE = "VIDEO2NOTES_TOKEN"
DEFAULT_SERVER_PORT = 43119


class CliUsageError(ValueError):
    """A safe command-line validation error."""


class CliRuntimeError(RuntimeError):
    """A safe, actionable local runtime error."""


class _JsonArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> Never:
        raise CliUsageError(message)


PipelinePreparer = Callable[
    [Path, Path, HardwareSnapshot],
    "PreparedPipeline",
]
ServerRunner = Callable[[Path, str, int, "PreparedServer"], None]


@dataclass(frozen=True, slots=True)
class PreparedPipeline:
    pipeline: Video2NotesPipeline
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class PreparedServer:
    warnings: tuple[str, ...] = ()
    pipeline_runtime: PipelineRuntime | None = None
    model_registry: ModelRegistry | None = None
    secret_store: KeyringSecretStore | None = None


class _UnavailableKeyringBackend:
    def set_password(self, service_name: str, username: str, password: str) -> None:
        del service_name, username, password
        raise CliRuntimeError("Credential storage requires the optional 'secrets' dependencies.")

    def get_password(self, service_name: str, username: str) -> str | None:
        del service_name, username
        return None

    def delete_password(self, service_name: str, username: str) -> None:
        del service_name, username


@dataclass(frozen=True, slots=True)
class CliDependencies:
    """Injectable process boundaries used by tests and the desktop launcher."""

    hardware_detector: Callable[[], HardwareSnapshot]
    pipeline_preparer: PipelinePreparer
    server_preparer: Callable[[Path, HardwareSnapshot], PreparedServer]
    server_runner: ServerRunner

    @classmethod
    def local_defaults(cls) -> CliDependencies:
        return cls(
            hardware_detector=detect_hardware,
            pipeline_preparer=_prepare_pipeline,
            server_preparer=_prepare_server,
            server_runner=_run_uvicorn_server,
        )


def _default_data_root() -> Path:
    return Path(user_data_path("Video2Notes", appauthor=False))


def _build_parser() -> argparse.ArgumentParser:
    parser = _JsonArgumentParser(
        prog="video2notes",
        description="High-precision, evidence-first video note tooling.",
    )
    subparsers = parser.add_subparsers(
        dest="command",
        required=True,
        parser_class=_JsonArgumentParser,
    )

    scan = subparsers.add_parser(
        "scan-changes",
        help="discover persistent visual changes with adaptive two-pass scanning",
    )
    scan.add_argument("video", type=Path, help="local video file")
    scan.add_argument(
        "--output",
        type=Path,
        default=Path("artifacts/visual-events.json"),
        help="JSON event manifest",
    )
    scan.add_argument(
        "--preview-dir",
        type=Path,
        default=None,
        help="optionally extract original-resolution event previews",
    )
    scan.add_argument("--coarse-fps", type=float, default=3.0)
    scan.add_argument("--fine-fps", type=float, default=12.0)
    scan.add_argument("--analysis-width", type=int, default=640)
    scan.add_argument("--analysis-height", type=int, default=360)
    scan.add_argument(
        "--print-config",
        action="store_true",
        help="print the resolved detector configuration before scanning",
    )

    process = subparsers.add_parser(
        "process",
        help="download or import one supported video and produce evidence-backed notes",
    )
    process.add_argument(
        "source",
        help="local video path or a Bilibili, YouTube, or X video URL",
    )
    process.add_argument(
        "--mode",
        choices=[item.value for item in QualityMode],
        default=QualityMode.BALANCED.value,
        help="quality/performance intent (default: balanced)",
    )
    process.add_argument(
        "--cookie-file",
        type=Path,
        default=None,
        help="Netscape cookies.txt path; the file contents are never logged",
    )
    process.add_argument(
        "--browser",
        choices=[item.value for item in BrowserKind],
        default=None,
        help="browser whose existing signed-in profile yt-dlp should use",
    )
    process.add_argument(
        "--profile",
        "--browser-profile",
        dest="profile",
        default=None,
        help="browser profile directory or profile identifier",
    )
    process.add_argument(
        "--language",
        action="append",
        default=[],
        metavar="LANG",
        help="language hint; repeat the option or use comma-separated BCP-47 tags",
    )
    process.add_argument(
        "--screenshots",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="include selected evidence screenshots (default: enabled)",
    )
    process.add_argument(
        "--audio-only",
        action="store_true",
        help=(
            "transcribe audio and platform captions without visual scanning, OCR, or screenshots"
        ),
    )
    process.add_argument(
        "--pdf",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="render a local PDF in addition to Markdown and HTML (default: enabled)",
    )
    process.add_argument(
        "--title",
        default=None,
        help="optional note title override",
    )
    process.add_argument(
        "--data-root",
        type=Path,
        default=_default_data_root(),
        help="local configuration root (default: the current user's app-data directory)",
    )
    process.add_argument(
        "--runs-root",
        type=Path,
        default=None,
        help="artifact root (default: DATA_ROOT/runs)",
    )

    serve = subparsers.add_parser(
        "serve",
        help="start the authenticated local desktop API on 127.0.0.1",
    )
    serve.add_argument(
        "--port",
        type=int,
        default=DEFAULT_SERVER_PORT,
        help=f"loopback port (default: {DEFAULT_SERVER_PORT})",
    )
    serve.add_argument(
        "--data-root",
        type=Path,
        default=_default_data_root(),
        help="local API data root (default: the current user's app-data directory)",
    )
    serve.add_argument(
        "--token",
        default=None,
        help=(
            f"session token; prefer the {TOKEN_ENVIRONMENT_VARIABLE} environment "
            "variable to avoid command-line history"
        ),
    )
    return parser


def _load_registry(data_root: Path) -> ModelRegistry:
    registry_path = data_root / "config" / "providers.json"
    if registry_path.is_file():
        return ModelRegistry.load(registry_path)
    registry = ModelRegistry.with_local_defaults()
    registry.save(registry_path)
    return registry


def _prepare_server(
    data_root: Path,
    hardware: HardwareSnapshot,
) -> PreparedServer:
    registry = _load_registry(data_root)
    preflight_warnings: list[str] = []
    try:
        secret_store = KeyringSecretStore()
    except ImportError:
        secret_store = KeyringSecretStore(_UnavailableKeyringBackend())
        preflight_warnings.append(
            "The optional keyring package is unavailable; credential-backed providers are disabled."
        )
    result = build_pipeline_runtime(
        registry,
        secret_store=secret_store,
        hardware=hardware,
    )
    return PreparedServer(
        pipeline_runtime=result.runtime,
        model_registry=registry,
        secret_store=secret_store,
        warnings=tuple(dict.fromkeys([*preflight_warnings, *result.warnings])),
    )


def _prepare_pipeline(
    runs_root: Path,
    data_root: Path,
    hardware: HardwareSnapshot,
) -> PreparedPipeline:
    built = _prepare_server(data_root, hardware)
    if built.pipeline_runtime is None:
        raise CliRuntimeError("pipeline runtime preparation returned no runtime")
    return PreparedPipeline(
        pipeline=Video2NotesPipeline(runs_root, runtime=built.pipeline_runtime),
        warnings=built.warnings,
    )


def _run_uvicorn_server(
    data_root: Path,
    token: str,
    port: int,
    prepared: PreparedServer,
) -> None:
    try:
        import uvicorn

        from video2notes.api import ApiContext, create_app
    except ImportError:
        raise CliRuntimeError(
            "The local API dependencies are not installed. "
            "Install Video2Notes with the 'api' extra."
        ) from None

    if (
        prepared.pipeline_runtime is None
        or prepared.model_registry is None
        or prepared.secret_store is None
    ):
        raise CliRuntimeError("local API runtime preparation is incomplete")
    context = ApiContext(
        data_root,
        token=token,
        source_registry=prepared.pipeline_runtime.source_registry,
        model_registry=prepared.model_registry,
        secret_store=prepared.secret_store,
    )
    app = create_app(context)
    uvicorn.run(
        app,
        host="127.0.0.1",
        port=port,
        access_log=False,
        log_level="critical",
    )


def _source_input(raw: str) -> SourceInput:
    candidate = raw.strip()
    if not candidate:
        raise CliUsageError("source cannot be empty")
    parsed = urlsplit(candidate)
    if parsed.scheme.lower() in {"http", "https"} and parsed.netloc:
        try:
            platform_for_url(candidate)
        except SourceError as error:
            raise CliUsageError(str(error)) from None
        return SourceInput.url(candidate)

    local_path = Path(candidate).expanduser().resolve()
    if not local_path.is_file():
        raise CliUsageError("local source is not a readable file")
    return SourceInput.local(local_path)


def _auth_spec(args: argparse.Namespace) -> AuthSpec:
    cookie_file: Path | None = args.cookie_file
    browser: str | None = args.browser
    profile: str | None = args.profile
    if cookie_file is not None and (browser is not None or profile is not None):
        raise CliUsageError("--cookie-file cannot be combined with --browser or --profile")
    if (browser is None) != (profile is None):
        raise CliUsageError("--browser and --profile must be provided together")
    if cookie_file is not None:
        resolved_cookie = cookie_file.expanduser().resolve()
        if not resolved_cookie.is_file():
            raise CliUsageError("cookie file is not a readable regular file")
        return AuthSpec.cookies_txt(resolved_cookie)
    if browser is not None and profile is not None:
        return AuthSpec.browser_profile(BrowserKind(browser), profile)
    return AuthSpec()


def _language_hints(raw_values: Sequence[str]) -> list[str]:
    hints: list[str] = []
    for raw in raw_values:
        for item in raw.split(","):
            normalized = item.strip().replace("_", "-")
            if not normalized:
                continue
            if len(normalized) > 35 or any(ord(character) < 32 for character in normalized):
                raise CliUsageError(f"invalid language hint: {normalized!r}")
            if normalized not in hints:
                hints.append(normalized)
    return hints


def _acquisition_policy(mode: QualityMode) -> AcquisitionPolicy:
    if mode is QualityMode.FAST:
        return AcquisitionPolicy(mode=AcquisitionQualityMode.FAST)
    if mode is QualityMode.BALANCED:
        return AcquisitionPolicy(
            mode=AcquisitionQualityMode.ACCURATE,
            max_height=1080,
        )
    return AcquisitionPolicy(mode=AcquisitionQualityMode.ACCURATE)


def _safe_text(value: object, *, secret_values: Sequence[str] = ()) -> str:
    safe = redact_sensitive(value)
    for secret_value in secret_values:
        if secret_value:
            safe = safe.replace(secret_value, "<redacted>")
    return safe


def _safe_metrics(
    metrics: Mapping[str, float | int | str | bool | None] | None,
) -> dict[str, float | int | str | bool | None]:
    if metrics is None:
        return {}
    return {
        key: (_safe_text(value) if isinstance(value, str) else value)
        for key, value in metrics.items()
    }


def _emit_event(stream: TextIO, event: Mapping[str, Any]) -> None:
    stream.write(
        json.dumps(
            dict(event),
            ensure_ascii=False,
            separators=(",", ":"),
            default=str,
        )
        + "\n"
    )
    stream.flush()


def _outcome_event(
    outcome: PipelineOutcome,
    *,
    run_root: Path,
) -> dict[str, Any]:
    def absolute(relative_path: str) -> str:
        return str((run_root / relative_path).resolve())

    return {
        "event": "completed",
        "ok": True,
        "run_id": outcome.run_id,
        "processing_scope": outcome.processing_scope.value,
        "run_root": str(run_root),
        "outputs": {
            "markdown": absolute(outcome.markdown.relative_path),
            "html": absolute(outcome.html.relative_path),
            "pdf": absolute(outcome.pdf.relative_path) if outcome.pdf is not None else None,
            "note_document": absolute(outcome.note_document.relative_path),
        },
        "metrics": {
            "processing_scope": outcome.processing_scope.value,
            "evidence_count": outcome.evidence_count,
            "visual_state_count": outcome.visual_state_count,
            "used_deterministic_note_fallback": (outcome.used_deterministic_note_fallback),
        },
        "artifacts": outcome.model_dump(mode="json"),
    }


def _run_process(
    args: argparse.Namespace,
    *,
    dependencies: CliDependencies,
    stdout: TextIO,
) -> int:
    data_root = args.data_root.expanduser().resolve()
    runs_root = (
        args.runs_root.expanduser().resolve() if args.runs_root is not None else data_root / "runs"
    )
    mode = QualityMode(args.mode)
    request = PipelineRequest(
        source=_source_input(args.source),
        auth=_auth_spec(args),
        acquisition=_acquisition_policy(mode),
        quality_mode=mode,
        processing_scope=(
            ProcessingScope.AUDIO_ONLY if args.audio_only else ProcessingScope.AUDIO_VISUAL
        ),
        title_override=args.title,
        language_hints=_language_hints(args.language),
        include_screenshots=args.screenshots,
        generate_pdf=args.pdf,
    )
    hardware = dependencies.hardware_detector()
    prepared = dependencies.pipeline_preparer(runs_root, data_root, hardware)
    for warning in prepared.warnings:
        _emit_event(
            stdout,
            {
                "event": "warning",
                "scope": "runtime",
                "message": _safe_text(warning),
            },
        )
    workspace = prepared.pipeline.create_run(request)
    tier = recommend_hardware_tier(hardware)
    _emit_event(
        stdout,
        {
            "event": "started",
            "run_id": workspace.manifest.run_id,
            "quality_mode": mode.value,
            "processing_scope": request.processing_scope.value,
            "hardware_tier": tier.value,
        },
    )
    cancellation = CancellationToken()

    def emit_progress(
        stage: str,
        *,
        progress: float | None = None,
        message: str | None = None,
        metrics: dict[str, float | int | str | bool | None] | None = None,
    ) -> None:
        event: dict[str, Any] = {
            "event": "progress",
            "run_id": workspace.manifest.run_id,
            "stage": stage,
            "progress": progress,
            "metrics": _safe_metrics(metrics),
        }
        if message is not None:
            event["message"] = _safe_text(message)
        _emit_event(stdout, event)

    try:
        outcome = prepared.pipeline.run(
            workspace,
            request,
            cancel=cancellation,
            emit=emit_progress,
        )
    except KeyboardInterrupt:
        cancellation.cancel()
        workspace.mark_cancelled()
        raise
    _emit_event(stdout, _outcome_event(outcome, run_root=workspace.root))
    return 0


def _write_generated_token(data_root: Path, token: str) -> Path:
    data_root.mkdir(parents=True, exist_ok=True)
    for _ in range(4):
        destination = data_root / (f".session-token-{os.getpid()}-{secrets.token_hex(4)}")
        try:
            descriptor = os.open(
                destination,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                stat.S_IRUSR | stat.S_IWUSR,
            )
        except FileExistsError:
            continue
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(token)
        with suppress(OSError):
            destination.chmod(stat.S_IRUSR | stat.S_IWUSR)
        return destination
    raise CliRuntimeError("could not create a unique local session-token file")


def _resolve_server_token(
    explicit_token: str | None,
    *,
    data_root: Path,
) -> tuple[str, str, Path | None]:
    if explicit_token is not None:
        token = explicit_token.strip()
        source = "argument"
        token_file = None
    elif os.environ.get(TOKEN_ENVIRONMENT_VARIABLE, "").strip():
        token = os.environ[TOKEN_ENVIRONMENT_VARIABLE].strip()
        source = "environment"
        token_file = None
    else:
        token = secrets.token_urlsafe(32)
        source = "generated_file"
        token_file = _write_generated_token(data_root, token)
    if len(token) < 16:
        raise CliUsageError("the loopback session token must contain at least 16 characters")
    return token, source, token_file


def _remove_generated_token(path: Path | None, token: str) -> None:
    if path is None:
        return
    try:
        if path.is_file() and path.read_text(encoding="utf-8") == token:
            path.unlink()
    except OSError:
        pass


def _run_serve(
    args: argparse.Namespace,
    *,
    dependencies: CliDependencies,
    stdout: TextIO,
) -> int:
    port: int = args.port
    if not 1 <= port <= 65535:
        raise CliUsageError("--port must be between 1 and 65535")
    data_root = args.data_root.expanduser().resolve()
    data_root.mkdir(parents=True, exist_ok=True)
    hardware = dependencies.hardware_detector()
    prepared = dependencies.server_preparer(data_root, hardware)
    for warning in prepared.warnings:
        _emit_event(
            stdout,
            {
                "event": "warning",
                "scope": "runtime",
                "message": _safe_text(warning),
            },
        )

    token, token_source, token_file = _resolve_server_token(
        args.token,
        data_root=data_root,
    )
    try:
        _emit_event(
            stdout,
            {
                "event": "server_starting",
                "host": "127.0.0.1",
                "port": port,
                "data_root": str(data_root),
                "token_source": token_source,
                "token_file": str(token_file) if token_file is not None else None,
            },
        )
        dependencies.server_runner(data_root, token, port, prepared)
    finally:
        _remove_generated_token(token_file, token)
    _emit_event(
        stdout,
        {
            "event": "server_stopped",
            "host": "127.0.0.1",
            "port": port,
        },
    )
    return 0


def _run_scan(args: argparse.Namespace, *, stdout: TextIO) -> int:
    config = AdaptiveScanConfig(
        coarse_fps=args.coarse_fps,
        fine_fps=args.fine_fps,
        analysis_width=args.analysis_width,
        analysis_height=args.analysis_height,
    )
    if args.print_config:
        stdout.write(json.dumps(asdict(config), ensure_ascii=False, indent=2) + "\n")
        stdout.flush()
    scanner = AdaptiveVideoScanner(config)
    result = scanner.scan(args.video, preview_dir=args.preview_dir)
    output = result.write_json(args.output)
    stdout.write(
        f"Detected {len(result.events)} stable visual states; manifest: {output.resolve()}\n"
    )
    stdout.flush()
    return 0


def main(
    argv: Sequence[str] | None = None,
    *,
    dependencies: CliDependencies | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    output_stream = stdout or sys.stdout
    error_stream = stderr or sys.stderr
    resolved_dependencies = dependencies or CliDependencies.local_defaults()
    parser = _build_parser()
    token_to_redact: str | None = None
    try:
        args = parser.parse_args(argv)
        if args.command == "scan-changes":
            return _run_scan(args, stdout=output_stream)
        if args.command == "process":
            return _run_process(
                args,
                dependencies=resolved_dependencies,
                stdout=output_stream,
            )
        if args.command == "serve":
            token_to_redact = args.token
            return _run_serve(
                args,
                dependencies=resolved_dependencies,
                stdout=output_stream,
            )
        raise CliUsageError(f"unknown command: {args.command}")
    except CliUsageError as error:
        _emit_event(
            error_stream,
            {
                "event": "error",
                "ok": False,
                "error_type": "usage",
                "message": _safe_text(
                    error,
                    secret_values=([token_to_redact] if token_to_redact else []),
                ),
            },
        )
        return 2
    except KeyboardInterrupt:
        _emit_event(
            error_stream,
            {
                "event": "error",
                "ok": False,
                "error_type": "cancelled",
                "message": "operation cancelled by user",
            },
        )
        return 130
    except Exception as error:
        _emit_event(
            error_stream,
            {
                "event": "error",
                "ok": False,
                "error_type": type(error).__name__,
                "message": _safe_text(
                    error,
                    secret_values=([token_to_redact] if token_to_redact else []),
                ),
            },
        )
        return 1
