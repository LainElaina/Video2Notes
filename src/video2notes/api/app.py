"""Authenticated loopback API; no cloud service and no telemetry."""

from __future__ import annotations

import hmac
import re
import secrets
import threading
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path, PurePosixPath
from typing import Annotated, Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from video2notes.artifacts import RunWorkspace
from video2notes.domain import ArtifactManifest, SourceDescriptor
from video2notes.jobs import (
    JobAlreadyRunningError,
    JobManager,
    JobNotFoundError,
    JobSnapshot,
)
from video2notes.jobs.manager import EventEmitter
from video2notes.pipeline import (
    PipelineOutcome,
    PipelineRequest,
    PipelineRuntime,
    Video2NotesPipeline,
)
from video2notes.providers import (
    KeyringSecretStore,
    ModelRegistry,
    ProviderSpec,
    SecretStatus,
)
from video2notes.runtime import build_pipeline_runtime
from video2notes.sources import (
    AcquisitionPolicy,
    AuthSpec,
    CancellationToken,
    SourceError,
    SourceInput,
    SourceManifest,
    SourceRegistry,
    enumerate_browser_profiles,
)
from video2notes.system import (
    HardwareSnapshot,
    ProcessingEstimate,
    QualityMode,
    build_execution_plan,
    detect_hardware,
    estimate_processing_time,
    recommend_hardware_tier,
)

_SENSITIVE_ASSIGNMENT = re.compile(
    r"(?i)\b(api[_ -]?key|authorization|cookie|token|sessdata|auth_token)"
    r"\b\s*[:=]\s*[^\s,;]+"
)
_BEARER_VALUE = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+")
_OPENAI_STYLE_KEY = re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b")
_SENSITIVE_QUERY_KEYS = {
    "access_token",
    "api_key",
    "auth",
    "authorization",
    "cookie",
    "key",
    "sessdata",
    "signature",
    "token",
}


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SourceProbeRequest(ApiModel):
    source: SourceInput
    auth: AuthSpec = Field(default_factory=AuthSpec)
    policy: AcquisitionPolicy = Field(default_factory=AcquisitionPolicy)


class CreateRunRequest(ApiModel):
    source: SourceInput
    quality_mode: QualityMode = QualityMode.BALANCED


class ProviderSecretRequest(ApiModel):
    secret: SecretStr


class SystemReport(ApiModel):
    hardware: HardwareSnapshot
    recommended_tier: str
    plans: dict[str, dict[str, Any]]


class ProcessingEstimateRequest(ApiModel):
    duration_seconds: float = Field(ge=0)
    quality_mode: QualityMode = QualityMode.BALANCED
    source_height: int | None = Field(default=None, ge=1)
    source_fps: float | None = Field(default=None, gt=0)


class RuntimeStatus(ApiModel):
    injected: bool
    warnings: list[str] = Field(default_factory=list)


class ProcessingRunResponse(ApiModel):
    run: ArtifactManifest
    job: JobSnapshot
    result: PipelineOutcome | None = None
    runtime_warnings: list[str] = Field(default_factory=list)


class ApiContext:
    """Mutable application services, injectable for tests and desktop startup."""

    def __init__(
        self,
        data_root: str | Path,
        *,
        token: str | None = None,
        source_registry: SourceRegistry | None = None,
        model_registry: ModelRegistry | None = None,
        secret_store: KeyringSecretStore | None = None,
        job_manager: JobManager | None = None,
        pipeline: Video2NotesPipeline | None = None,
        pipeline_runtime: PipelineRuntime | None = None,
    ):
        if pipeline is not None and pipeline_runtime is not None:
            raise ValueError("provide pipeline or pipeline_runtime, not both")
        self.data_root = Path(data_root).expanduser().resolve()
        self.runs_root = self.data_root / "runs"
        self.config_root = self.data_root / "config"
        self.runs_root.mkdir(parents=True, exist_ok=True)
        self.config_root.mkdir(parents=True, exist_ok=True)
        self.token = token or secrets.token_urlsafe(32)
        self.source_registry = source_registry or SourceRegistry.default()
        self.registry_path = self.config_root / "providers.json"
        if model_registry is not None:
            self.model_registry = model_registry
        elif self.registry_path.is_file():
            self.model_registry = ModelRegistry.load(self.registry_path)
        else:
            self.model_registry = ModelRegistry.with_local_defaults()
            self.model_registry.save(self.registry_path)
        self.secret_store = secret_store or KeyringSecretStore()
        self.job_manager = job_manager or JobManager(max_workers=2)
        self._owns_job_manager = job_manager is None
        self._pipeline_is_injected = pipeline is not None or pipeline_runtime is not None
        self.pipeline: Video2NotesPipeline
        self.runtime_warnings: tuple[str, ...]
        self._results: dict[str, PipelineOutcome] = {}
        self._results_lock = threading.RLock()
        if pipeline is not None:
            self.pipeline = pipeline
            self.runtime_warnings = ()
        elif pipeline_runtime is not None:
            self.pipeline = Video2NotesPipeline(
                self.runs_root,
                runtime=pipeline_runtime,
            )
            self.runtime_warnings = ()
        else:
            self.pipeline = Video2NotesPipeline(self.runs_root)
            self.runtime_warnings = ()
            self.refresh_pipeline()

    def close(self) -> None:
        if self._owns_job_manager:
            self.job_manager.shutdown(wait=False, cancel_pending=True)

    def list_runs(self) -> list[ArtifactManifest]:
        manifests: list[ArtifactManifest] = []
        for child in self.runs_root.iterdir():
            manifest_path = child / "manifest.json"
            if not child.is_dir() or not manifest_path.is_file():
                continue
            try:
                manifest = ArtifactManifest.model_validate_json(
                    manifest_path.read_text(encoding="utf-8")
                )
            except (OSError, ValueError):
                continue
            manifests.append(manifest)
        return sorted(manifests, key=lambda item: item.created_at, reverse=True)

    def get_workspace(self, run_id: str) -> RunWorkspace:
        candidate = (self.runs_root / run_id).resolve()
        if not candidate.is_relative_to(self.runs_root):
            raise FileNotFoundError(run_id)
        for attempt in range(5):
            try:
                return RunWorkspace(candidate)
            except PermissionError:
                if attempt == 4:
                    raise
                time.sleep(0.005)
        raise RuntimeError("workspace retry loop exhausted")  # pragma: no cover

    @property
    def pipeline_is_injected(self) -> bool:
        return self._pipeline_is_injected

    def refresh_pipeline(self) -> None:
        """Rebuild lazy role backends without replacing an injected test runtime."""

        if self._pipeline_is_injected:
            return
        result = build_pipeline_runtime(
            self.model_registry,
            secret_store=self.secret_store,
            source_registry=self.source_registry,
        )
        self.pipeline = Video2NotesPipeline(
            self.runs_root,
            runtime=result.runtime,
        )
        self.runtime_warnings = tuple(_sanitize_message(warning) for warning in result.warnings)

    def store_result(self, result: PipelineOutcome) -> None:
        with self._results_lock:
            self._results[result.run_id] = result

    def get_result(self, run_id: str) -> PipelineOutcome | None:
        with self._results_lock:
            result = self._results.get(run_id)
        if result is not None:
            return result
        try:
            outcome_path = self.get_workspace(run_id).root / "render" / "outcome.json"
            if not outcome_path.is_file():
                return None
            return PipelineOutcome.model_validate_json(outcome_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None


def create_app(
    context: ApiContext,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        context.close()

    app = FastAPI(
        title="Video2Notes Local API",
        version="0.1.0",
        docs_url=None,
        redoc_url=None,
        openapi_url="/api/openapi.json",
        lifespan=lifespan,
    )
    app.state.video2notes = context
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "tauri://localhost",
            "http://tauri.localhost",
            "https://tauri.localhost",
            "http://127.0.0.1:1420",
            "http://localhost:1420",
        ],
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "DELETE"],
        allow_headers=["Content-Type", "X-Video2Notes-Token"],
    )

    def require_token(
        supplied: Annotated[
            str | None,
            Header(alias="X-Video2Notes-Token"),
        ] = None,
    ) -> None:
        if supplied is None or not hmac.compare_digest(supplied, context.token):
            raise HTTPException(status_code=401, detail="invalid loopback session token")

    protected = [Depends(require_token)]

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "version": "0.1.0", "scope": "local-only"}

    @app.get(
        "/api/system",
        response_model=SystemReport,
        dependencies=protected,
    )
    def system_report() -> SystemReport:
        hardware = detect_hardware()
        tier = recommend_hardware_tier(hardware)
        return SystemReport(
            hardware=hardware,
            recommended_tier=tier.value,
            plans={
                mode.value: build_execution_plan(
                    hardware,
                    mode,
                    hardware_tier=tier,
                ).model_dump(mode="json")
                for mode in QualityMode
            },
        )

    @app.post(
        "/api/estimate",
        response_model=ProcessingEstimate,
        dependencies=protected,
    )
    def estimate(request: ProcessingEstimateRequest) -> ProcessingEstimate:
        return estimate_processing_time(
            request.duration_seconds,
            detect_hardware(),
            request.quality_mode,
            source_height=request.source_height,
            source_fps=request.source_fps,
        )

    @app.get(
        "/api/runtime",
        response_model=RuntimeStatus,
        dependencies=protected,
    )
    def runtime_status() -> RuntimeStatus:
        return RuntimeStatus(
            injected=context.pipeline_is_injected,
            warnings=[_sanitize_message(warning) for warning in context.runtime_warnings],
        )

    @app.get("/api/browser-profiles", dependencies=protected)
    def browser_profiles() -> list[dict[str, Any]]:
        return [item.model_dump(mode="json") for item in enumerate_browser_profiles()]

    @app.post(
        "/api/sources/probe",
        response_model=SourceManifest,
        dependencies=protected,
    )
    async def probe_source(request: SourceProbeRequest) -> SourceManifest:
        try:
            adapter = context.source_registry.resolve(request.source)
            result = await adapter.probe(
                request.source,
                request.auth,
                request.policy,
            )
        except SourceError:
            raise HTTPException(
                status_code=422,
                detail="source could not be probed",
            ) from None
        except FileNotFoundError:
            raise HTTPException(
                status_code=422,
                detail="source file was not found",
            ) from None
        except ValueError:
            raise HTTPException(
                status_code=422,
                detail="source configuration is invalid",
            ) from None
        if not isinstance(result, SourceManifest):
            raise HTTPException(status_code=500, detail="source adapter returned invalid data")
        return result

    @app.get("/api/providers", response_model=ModelRegistry, dependencies=protected)
    def get_providers() -> ModelRegistry:
        return context.model_registry

    @app.put("/api/providers", response_model=ModelRegistry, dependencies=protected)
    def put_providers(registry: ModelRegistry) -> ModelRegistry:
        registry.save(context.registry_path)
        context.model_registry = registry
        context.refresh_pipeline()
        return context.model_registry

    @app.get("/api/providers/{provider_id}/secret", dependencies=protected)
    def provider_secret_status(provider_id: str) -> dict[str, str]:
        _require_provider(context, provider_id)
        status = context.secret_store.status(provider_id)
        return {"provider_id": provider_id, "status": status.value}

    @app.put("/api/providers/{provider_id}/secret", dependencies=protected)
    def put_provider_secret(
        provider_id: str,
        request: ProviderSecretRequest,
    ) -> dict[str, str]:
        provider = _require_provider(context, provider_id)
        reference = context.secret_store.set(
            provider_id,
            request.secret.get_secret_value(),
        )
        provider.credential_ref = reference
        context.model_registry.save(context.registry_path)
        context.refresh_pipeline()
        return {
            "provider_id": provider_id,
            "status": SecretStatus.CONFIGURED.value,
        }

    @app.delete("/api/providers/{provider_id}/secret", dependencies=protected)
    def delete_provider_secret(provider_id: str) -> dict[str, str]:
        provider = _require_provider(context, provider_id)
        context.secret_store.delete(provider_id)
        provider.credential_ref = None
        context.model_registry.save(context.registry_path)
        context.refresh_pipeline()
        return {
            "provider_id": provider_id,
            "status": SecretStatus.NOT_CONFIGURED.value,
        }

    @app.get(
        "/api/runs",
        response_model=list[ArtifactManifest],
        dependencies=protected,
    )
    def list_runs() -> list[ArtifactManifest]:
        return [_safe_manifest(item) for item in context.list_runs()]

    @app.post(
        "/api/runs",
        response_model=ArtifactManifest,
        dependencies=protected,
    )
    def create_run(request: CreateRunRequest) -> ArtifactManifest:
        source = SourceDescriptor(
            kind=request.source.kind.value,
            locator=request.source.value,
        )
        workspace = RunWorkspace.create(
            context.runs_root,
            source=source,
            profile=request.quality_mode.value,
        )
        return _safe_manifest(workspace.manifest)

    @app.get(
        "/api/runs/{run_id}",
        response_model=ArtifactManifest,
        dependencies=protected,
    )
    def get_run(run_id: str) -> ArtifactManifest:
        try:
            return _safe_manifest(context.get_workspace(run_id).manifest)
        except (FileNotFoundError, ValueError):
            raise HTTPException(status_code=404, detail="run not found") from None

    @app.get("/api/runs/{run_id}/artifact", dependencies=protected)
    def get_artifact(
        run_id: str,
        path: Annotated[str, Query(min_length=1)],
    ) -> FileResponse:
        try:
            workspace = context.get_workspace(run_id)
            relative = PurePosixPath(path.replace("\\", "/"))
            if (
                relative.is_absolute()
                or ".." in relative.parts
                or relative == PurePosixPath("manifest.json")
            ):
                raise ValueError("invalid artifact path")
            target = (workspace.root / relative).resolve()
            if not target.is_relative_to(workspace.root) or not target.is_file():
                raise FileNotFoundError(path)
        except (FileNotFoundError, ValueError):
            raise HTTPException(status_code=404, detail="artifact not found") from None
        return FileResponse(target)

    @app.get(
        "/api/jobs",
        response_model=list[JobSnapshot],
        dependencies=protected,
    )
    def list_jobs() -> list[JobSnapshot]:
        return [_safe_job(item) for item in context.job_manager.list()]

    @app.post(
        "/api/jobs",
        response_model=ProcessingRunResponse,
        status_code=202,
        dependencies=protected,
    )
    def submit_job(request: PipelineRequest) -> ProcessingRunResponse:
        pipeline = context.pipeline
        try:
            workspace = pipeline.create_run(request)
            safe_warnings = [_sanitize_message(warning) for warning in context.runtime_warnings]
            for warning in safe_warnings:
                workspace.add_warning(warning)
            submitted_manifest = _safe_manifest(workspace.manifest)

            def worker(cancel: CancellationToken, emit: EventEmitter) -> None:
                result = pipeline.run(
                    workspace,
                    request,
                    cancel=cancel,
                    emit=emit,
                )
                context.store_result(result)

            snapshot = context.job_manager.submit(workspace.manifest.run_id, worker)
        except (FileExistsError, ValueError):
            raise HTTPException(
                status_code=422,
                detail="processing task could not be created",
            ) from None
        except JobAlreadyRunningError:
            raise HTTPException(
                status_code=409,
                detail="processing task is already running",
            ) from None
        except Exception:
            raise HTTPException(
                status_code=500,
                detail="processing task could not be submitted",
            ) from None
        return ProcessingRunResponse(
            run=submitted_manifest,
            job=_safe_job(snapshot),
            runtime_warnings=safe_warnings,
        )

    @app.get(
        "/api/jobs/{run_id}",
        response_model=JobSnapshot,
        dependencies=protected,
    )
    def get_job(
        run_id: str,
        after_sequence: Annotated[int, Query(ge=0)] = 0,
    ) -> JobSnapshot:
        try:
            return _safe_job(
                context.job_manager.get(
                    run_id,
                    after_sequence=after_sequence,
                )
            )
        except JobNotFoundError:
            raise HTTPException(status_code=404, detail="job not found") from None

    @app.get(
        "/api/jobs/{run_id}/result",
        response_model=ProcessingRunResponse,
        dependencies=protected,
    )
    def get_job_result(run_id: str) -> ProcessingRunResponse:
        try:
            snapshot = context.job_manager.get(run_id)
            workspace = context.get_workspace(run_id)
        except JobNotFoundError:
            raise HTTPException(status_code=404, detail="job not found") from None
        except (FileNotFoundError, ValueError):
            raise HTTPException(status_code=404, detail="run not found") from None
        return ProcessingRunResponse(
            run=_safe_manifest(workspace.manifest),
            job=_safe_job(snapshot),
            result=context.get_result(run_id),
            runtime_warnings=[_sanitize_message(warning) for warning in context.runtime_warnings],
        )

    @app.post(
        "/api/jobs/{run_id}/cancel",
        response_model=JobSnapshot,
        dependencies=protected,
    )
    def cancel_job(run_id: str) -> JobSnapshot:
        try:
            return _safe_job(context.job_manager.request_cancel(run_id))
        except JobNotFoundError:
            raise HTTPException(status_code=404, detail="job not found") from None
        except JobAlreadyRunningError:
            raise HTTPException(
                status_code=409,
                detail="processing task cannot be cancelled",
            ) from None

    return app


def _require_provider(context: ApiContext, provider_id: str) -> ProviderSpec:
    provider = context.model_registry.providers.get(provider_id)
    if provider is None:
        raise HTTPException(status_code=404, detail="provider not found")
    return provider


def _safe_manifest(manifest: ArtifactManifest) -> ArtifactManifest:
    safe = manifest.model_copy(deep=True)
    safe.source.locator = _sanitize_locator(safe.source.locator)
    if safe.source.canonical_url is not None:
        safe.source.canonical_url = _sanitize_locator(safe.source.canonical_url)
    safe.warnings = [_sanitize_message(item) for item in safe.warnings]
    for stage in safe.stages.values():
        stage.warnings = [_sanitize_message(item) for item in stage.warnings]
        stage.metrics = _sanitize_metrics(stage.metrics)
        if stage.error is not None:
            stage.error = _exception_type_only(stage.error)
    return safe


def _safe_job(snapshot: JobSnapshot) -> JobSnapshot:
    safe = snapshot.model_copy(deep=True)
    safe.stage = _sanitize_message(safe.stage)
    if safe.message is not None:
        safe.message = _sanitize_message(safe.message)
    for event in safe.events:
        event.stage = _sanitize_message(event.stage)
        if event.message is not None:
            event.message = _sanitize_message(event.message)
        event.metrics = _sanitize_metrics(event.metrics)
    return safe


def _sanitize_message(message: str) -> str:
    sanitized = _SENSITIVE_ASSIGNMENT.sub(
        lambda match: f"{match.group(1)}=<redacted>",
        message,
    )
    sanitized = _BEARER_VALUE.sub("Bearer <redacted>", sanitized)
    return _OPENAI_STYLE_KEY.sub("<redacted-api-key>", sanitized)


def _sanitize_metrics(
    metrics: dict[str, float | int | str | bool | None],
) -> dict[str, float | int | str | bool | None]:
    return {
        _sanitize_message(key): (_sanitize_message(value) if isinstance(value, str) else value)
        for key, value in metrics.items()
    }


def _exception_type_only(error: str) -> str:
    candidate = error.partition(":")[0].strip()
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.]*", candidate):
        return candidate
    return "ProcessingError"


def _sanitize_locator(locator: str) -> str:
    parts = urlsplit(locator)
    if parts.scheme not in {"http", "https"}:
        return locator
    hostname = parts.hostname or ""
    if ":" in hostname and not hostname.startswith("["):
        hostname = f"[{hostname}]"
    try:
        port = parts.port
    except ValueError:
        return f"{parts.scheme}://<invalid-url>"
    if port is not None:
        hostname = f"{hostname}:{port}"
    query = [
        (key, "<redacted>" if key.casefold() in _SENSITIVE_QUERY_KEYS else value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
    ]
    return urlunsplit(
        (
            parts.scheme,
            hostname,
            parts.path,
            urlencode(query),
            _sanitize_message(parts.fragment),
        )
    )
