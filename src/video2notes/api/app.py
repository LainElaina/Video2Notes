"""Authenticated loopback API; no cloud service and no telemetry."""

from __future__ import annotations

import hmac
import json
import os
import re
import secrets
import threading
import time
import urllib.error
import urllib.request
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path, PurePosixPath
from typing import Annotated, Any, Literal, Self
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from fastapi import Depends, FastAPI, File, Header, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator

from video2notes.artifacts import RunWorkspace
from video2notes.components import (
    ComponentInventory,
    ComponentManager,
    ComponentManagerError,
    ComponentNotReadyError,
    PrepareResult,
    TierRecommendation,
)
from video2notes.domain import ArtifactManifest, SourceDescriptor
from video2notes.jobs import (
    JobAlreadyRunningError,
    JobManager,
    JobNotFoundError,
    JobSnapshot,
)
from video2notes.jobs.manager import EventEmitter
from video2notes.materials import MaterialStore, RunMaterial, TextMaterialRequest
from video2notes.notes import (
    ReportRevisionIndex,
    ReportRevisionRecord,
    ReportRevisionService,
    ReportSpec,
)
from video2notes.operations import (
    EvidenceView,
    OperationConflictError,
    OperationInputError,
    OperationNotFoundError,
    OperationRecord,
    OperationRequest,
    OperationService,
)
from video2notes.pipeline import (
    PipelineOutcome,
    PipelineRequest,
    PipelineRuntime,
    Video2NotesPipeline,
)
from video2notes.providers import (
    PROTOCOL_CATALOG,
    ROLE_REQUIREMENTS,
    AuthScheme,
    Capability,
    KeyringSecretStore,
    ModelRegistry,
    ProviderAuthError,
    ProviderProtocol,
    ProviderSpec,
    SecretStatus,
    provider_auth_headers,
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
    ExperienceMode,
    HardwareSnapshot,
    HardwareTier,
    PerformanceOverrides,
    ProcessingEstimate,
    QualityMode,
    ResourcePreference,
    ResourceRecommendation,
    ResourceReserve,
    build_execution_plan,
    detect_hardware,
    estimate_processing_time,
    recommend_hardware_tier,
    recommend_resources,
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


class PerformanceSettings(ApiModel):
    """Persisted scheduling intent; detected machine state is never persisted."""

    schema_version: Literal[1] = 1
    experience_mode: ExperienceMode = ExperienceMode.GUIDED
    preference: ResourcePreference = ResourcePreference.BALANCED
    reserve: ResourceReserve | None = None
    overrides: PerformanceOverrides = Field(default_factory=PerformanceOverrides)

    @model_validator(mode="after")
    def professional_controls_require_professional_mode(self) -> Self:
        has_overrides = any(
            value is not None for value in self.overrides.model_dump().values()
        )
        if has_overrides and self.experience_mode is not ExperienceMode.PROFESSIONAL:
            raise ValueError("performance overrides require professional experience mode")
        return self


class SystemReport(ApiModel):
    hardware: HardwareSnapshot
    recommended_tier: str
    performance: PerformanceSettings
    recommendation: ResourceRecommendation
    plans: dict[str, dict[str, Any]]


class ProtocolCatalogEntry(ApiModel):
    protocol: ProviderProtocol
    display_name: str
    default_auth_scheme: AuthScheme
    default_base_url: str | None
    request_path: str | None
    discovery_path: str | None
    request_content_type: str
    structured_generation_adapter: bool
    supports_json_schema_transport: bool
    supports_image_transport: bool
    supports_streaming_transport: bool
    stream_transport: str


class RoleCatalogEntry(ApiModel):
    role: str
    required_capabilities: list[Capability]


class ConfigurationCatalog(ApiModel):
    protocols: list[ProtocolCatalogEntry]
    roles: list[RoleCatalogEntry]
    capabilities: list[Capability]


class DiscoveredModel(ApiModel):
    model_id: str
    display_name: str
    context_window: int | None = Field(default=None, ge=1)


class ProviderDiscoveryResult(ApiModel):
    provider_id: str
    protocol: ProviderProtocol
    models: list[DiscoveredModel]


class ComponentReport(ApiModel):
    hardware_tier: HardwareTier
    recommendation: TierRecommendation
    inventory: ComponentInventory


class PrepareComponentsRequest(ApiModel):
    component_ids: list[str] = Field(default_factory=list, max_length=8)
    hardware_tier: HardwareTier | None = None
    activate: bool = True


class ComponentPreparationResponse(ApiModel):
    hardware_tier: HardwareTier
    results: list[PrepareResult]
    activated: bool
    report: ComponentReport


class ProcessingEstimateRequest(ApiModel):
    duration_seconds: float = Field(ge=0)
    quality_mode: QualityMode = QualityMode.BALANCED
    source_height: int | None = Field(default=None, ge=1)
    source_fps: float | None = Field(default=None, gt=0)


class RuntimeStatus(ApiModel):
    injected: bool
    warnings: list[str] = Field(default_factory=list)


class ProviderConnectionResult(ApiModel):
    provider_id: str
    status: str
    detail: str


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
        component_manager: ComponentManager | None = None,
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
        self.performance_path = self.config_root / "performance.json"
        if self.performance_path.is_file():
            try:
                self.performance_settings = PerformanceSettings.model_validate_json(
                    self.performance_path.read_text(encoding="utf-8")
                )
            except (OSError, ValueError):
                self.performance_settings = PerformanceSettings()
                self.save_performance_settings(self.performance_settings)
        else:
            self.performance_settings = PerformanceSettings()
            self.save_performance_settings(self.performance_settings)
        self.secret_store = secret_store or KeyringSecretStore()
        self.component_manager = component_manager or ComponentManager(self.data_root)
        self.job_manager = job_manager or JobManager(max_workers=2)
        self._owns_job_manager = job_manager is None
        self._pipeline_is_injected = pipeline is not None or pipeline_runtime is not None
        self.pipeline: Video2NotesPipeline
        self.runtime_warnings: tuple[str, ...]
        self._results: dict[str, PipelineOutcome] = {}
        self._results_lock = threading.RLock()
        self._operations_lock = threading.RLock()
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

    def save_performance_settings(
        self,
        settings: PerformanceSettings,
    ) -> PerformanceSettings:
        temporary = self.performance_path.with_name(
            f".{self.performance_path.name}.{uuid.uuid4().hex}.tmp"
        )
        temporary.write_text(
            json.dumps(
                settings.model_dump(mode="json", exclude_none=True),
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        os.replace(temporary, self.performance_path)
        self.performance_settings = settings
        return settings

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
            hardware_disk_path=str(self.data_root),
            experience_mode=self.performance_settings.experience_mode,
            resource_preference=self.performance_settings.preference,
            resource_reserve=self.performance_settings.reserve,
            performance_overrides=self.performance_settings.overrides,
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
        hardware = detect_hardware(disk_path=context.data_root)
        tier = recommend_hardware_tier(hardware)
        settings = context.performance_settings
        recommendation = recommend_resources(
            hardware,
            experience_mode=settings.experience_mode,
            preference=settings.preference,
            reserve=settings.reserve,
        )
        return SystemReport(
            hardware=hardware,
            recommended_tier=tier.value,
            performance=settings,
            recommendation=recommendation,
            plans={
                mode.value: build_execution_plan(
                    hardware,
                    mode,
                    hardware_tier=tier,
                    experience_mode=settings.experience_mode,
                    preference=settings.preference,
                    reserve=settings.reserve,
                    overrides=settings.overrides,
                ).model_dump(mode="json")
                for mode in QualityMode
            },
        )

    @app.get(
        "/api/performance",
        response_model=PerformanceSettings,
        dependencies=protected,
    )
    def get_performance_settings() -> PerformanceSettings:
        return context.performance_settings

    @app.put(
        "/api/performance",
        response_model=PerformanceSettings,
        dependencies=protected,
    )
    def put_performance_settings(
        settings: PerformanceSettings,
    ) -> PerformanceSettings:
        saved = context.save_performance_settings(settings)
        context.refresh_pipeline()
        return saved

    @app.post(
        "/api/estimate",
        response_model=ProcessingEstimate,
        dependencies=protected,
    )
    def estimate(request: ProcessingEstimateRequest) -> ProcessingEstimate:
        return estimate_processing_time(
            request.duration_seconds,
            detect_hardware(disk_path=context.data_root),
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

    @app.get(
        "/api/components",
        response_model=ComponentReport,
        dependencies=protected,
    )
    def component_report() -> ComponentReport:
        return _component_report(context)

    @app.post(
        "/api/components/prepare",
        response_model=ComponentPreparationResponse,
        dependencies=protected,
    )
    def prepare_components(
        request: PrepareComponentsRequest,
    ) -> ComponentPreparationResponse:
        tier = request.hardware_tier or recommend_hardware_tier(
            detect_hardware(disk_path=context.data_root)
        )
        recommendation = context.component_manager.recommendation(tier)
        component_ids = request.component_ids or [
            recommendation.asr_component_id,
            recommendation.ocr_component_id,
        ]
        if len(component_ids) != len(set(component_ids)):
            raise HTTPException(status_code=422, detail="component IDs must be unique")
        try:
            results = [
                context.component_manager.prepare(component_id)
                for component_id in component_ids
            ]
            activated = False
            inventory = context.component_manager.inventory(tier)
            if (
                request.activate
                and inventory.capabilities.get("asr")
                and inventory.capabilities.get("ocr")
            ):
                _activate_managed_local_models(context, tier)
                activated = True
            report = _component_report(context, tier=tier)
        except ComponentManagerError as error:
            raise HTTPException(status_code=422, detail=str(error)) from None
        return ComponentPreparationResponse(
            hardware_tier=tier,
            results=results,
            activated=activated,
            report=report,
        )

    @app.get("/api/browser-profiles", dependencies=protected)
    def browser_profiles() -> list[dict[str, Any]]:
        return [item.model_dump(mode="json") for item in enumerate_browser_profiles()]

    @app.get(
        "/api/configuration-catalog",
        response_model=ConfigurationCatalog,
        dependencies=protected,
    )
    def configuration_catalog() -> ConfigurationCatalog:
        return ConfigurationCatalog(
            protocols=[
                ProtocolCatalogEntry.model_validate(asdict(template))
                for template in PROTOCOL_CATALOG.values()
            ],
            roles=[
                RoleCatalogEntry(
                    role=role,
                    required_capabilities=sorted(
                        requirements,
                        key=lambda item: item.value,
                    ),
                )
                for role, requirements in ROLE_REQUIREMENTS.items()
            ],
            capabilities=list(Capability),
        )

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

    @app.post(
        "/api/providers/{provider_id}/test",
        response_model=ProviderConnectionResult,
        dependencies=protected,
    )
    def test_provider(provider_id: str) -> ProviderConnectionResult:
        provider = _require_provider(context, provider_id)
        if not provider.enabled:
            return ProviderConnectionResult(
                provider_id=provider_id,
                status="disconnected",
                detail="Provider 已禁用；请先在本机注册表中启用。",
            )
        if provider.base_url is None:
            return ProviderConnectionResult(
                provider_id=provider_id,
                status="connected",
                detail="本地执行器配置可用；具体模型会在任务首次调用时延迟加载。",
            )
        api_key = _provider_api_key(context, provider)
        if provider.auth_scheme is not AuthScheme.NONE and api_key is None:
            return ProviderConnectionResult(
                provider_id=provider_id,
                status="disconnected",
                detail="该协议需要凭据，但 Windows 凭据库中没有可用密钥。",
            )
        template = PROTOCOL_CATALOG[provider.protocol]
        endpoint = _provider_endpoint(
            provider,
            template.discovery_path,
        )
        headers = _provider_headers(provider, api_key)
        headers["Accept"] = "application/json"
        request = urllib.request.Request(endpoint, headers=headers, method="GET")
        try:
            with urllib.request.urlopen(
                request,
                timeout=min(provider.request_timeout_seconds, 15),
            ) as response:
                status_code = int(response.status)
        except urllib.error.HTTPError as error:
            status_code = int(error.code)
        except (OSError, TimeoutError, urllib.error.URLError):
            return ProviderConnectionResult(
                provider_id=provider_id,
                status="disconnected",
                detail="Provider endpoint 未在 15 秒内返回可用响应。",
            )
        if 200 <= status_code < 300:
            return ProviderConnectionResult(
                provider_id=provider_id,
                status="connected",
                detail=(
                    "Provider endpoint 与模型目录接口连接正常。"
                    if template.discovery_path is not None
                    else "Provider endpoint 可以访问；该实验协议没有标准模型目录。"
                ),
            )
        return ProviderConnectionResult(
            provider_id=provider_id,
            status="disconnected",
            detail=f"Provider endpoint 返回 HTTP {status_code}；请检查地址或本机凭据。",
        )

    @app.get(
        "/api/providers/{provider_id}/discover",
        response_model=ProviderDiscoveryResult,
        dependencies=protected,
    )
    def discover_provider_models(provider_id: str) -> ProviderDiscoveryResult:
        provider = _require_provider(context, provider_id)
        template = PROTOCOL_CATALOG[provider.protocol]
        if provider.base_url is None or template.discovery_path is None:
            raise HTTPException(
                status_code=409,
                detail="this provider protocol has no model discovery endpoint",
            )
        api_key = _provider_api_key(context, provider)
        if provider.auth_scheme is not AuthScheme.NONE and api_key is None:
            raise HTTPException(
                status_code=409,
                detail="provider credential is not configured",
            )
        endpoint = _provider_endpoint(provider, template.discovery_path)
        headers = _provider_headers(provider, api_key)
        headers["Accept"] = "application/json"
        request = urllib.request.Request(endpoint, headers=headers, method="GET")
        try:
            with urllib.request.urlopen(
                request,
                timeout=min(provider.request_timeout_seconds, 30),
            ) as response:
                body = response.read(4_000_001)
        except urllib.error.HTTPError as error:
            raise HTTPException(
                status_code=422,
                detail=f"provider model discovery returned HTTP {int(error.code)}",
            ) from None
        except (OSError, TimeoutError, urllib.error.URLError):
            raise HTTPException(
                status_code=422,
                detail="provider model discovery failed or timed out",
            ) from None
        if len(body) > 4_000_000:
            raise HTTPException(status_code=422, detail="provider model catalog is too large")
        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise HTTPException(
                status_code=422,
                detail="provider model discovery returned invalid JSON",
            ) from None
        return ProviderDiscoveryResult(
            provider_id=provider.id,
            protocol=provider.protocol,
            models=_parse_discovered_models(provider.protocol, payload),
        )

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
        "/api/runs/{run_id}/materials",
        response_model=list[RunMaterial],
        dependencies=protected,
    )
    def list_materials(run_id: str) -> list[RunMaterial]:
        try:
            return MaterialStore(context.get_workspace(run_id)).list()
        except (FileNotFoundError, ValueError):
            raise HTTPException(status_code=404, detail="run not found") from None

    @app.post(
        "/api/runs/{run_id}/materials/text",
        response_model=RunMaterial,
        dependencies=protected,
    )
    def add_text_material(run_id: str, request: TextMaterialRequest) -> RunMaterial:
        try:
            return MaterialStore(context.get_workspace(run_id)).add_text(request)
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail="run not found") from None
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from None

    @app.post(
        "/api/runs/{run_id}/materials/files",
        response_model=RunMaterial,
        dependencies=protected,
    )
    async def add_file_material(
        run_id: str,
        file: Annotated[UploadFile, File()],
        title: Annotated[str | None, Query(max_length=240)] = None,
        start_us: Annotated[int | None, Query(ge=0)] = None,
        end_us: Annotated[int | None, Query(ge=0)] = None,
    ) -> RunMaterial:
        try:
            workspace = context.get_workspace(run_id)
            content = await file.read(25 * 1024 * 1024 + 1)
            return MaterialStore(workspace).add_file(
                filename=file.filename or "material",
                content_type=file.content_type,
                content=content,
                title=title,
                start_us=start_us,
                end_us=end_us,
            )
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail="run not found") from None
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from None
        finally:
            await file.close()

    @app.delete(
        "/api/runs/{run_id}/materials/{material_id}",
        response_model=RunMaterial,
        dependencies=protected,
    )
    def delete_material(run_id: str, material_id: str) -> RunMaterial:
        try:
            return MaterialStore(context.get_workspace(run_id)).delete(material_id)
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail="run not found") from None
        except KeyError:
            raise HTTPException(status_code=404, detail="material not found") from None

    @app.get(
        "/api/runs/{run_id}/operations",
        response_model=list[OperationRecord],
        dependencies=protected,
    )
    def list_operations(run_id: str) -> list[OperationRecord]:
        try:
            service = _operation_service(context, context.get_workspace(run_id))
            return service.list_operations()
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail="run not found") from None
        except OperationConflictError as error:
            raise HTTPException(status_code=409, detail=str(error)) from None

    @app.post(
        "/api/runs/{run_id}/operations",
        response_model=OperationRecord,
        dependencies=protected,
    )
    def create_operation(
        run_id: str,
        request: OperationRequest,
    ) -> OperationRecord:
        try:
            service = _operation_service(context, context.get_workspace(run_id))
            return service.execute(request)
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail="run not found") from None
        except OperationConflictError as error:
            raise HTTPException(status_code=409, detail=str(error)) from None
        except OperationInputError as error:
            raise HTTPException(status_code=422, detail=str(error)) from None

    @app.get(
        "/api/runs/{run_id}/evidence",
        response_model=EvidenceView,
        dependencies=protected,
    )
    def get_evidence(
        run_id: str,
        revision: Annotated[str | None, Query(max_length=100)] = None,
    ) -> EvidenceView:
        try:
            service = _operation_service(context, context.get_workspace(run_id))
            return service.get_evidence(revision)
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail="run not found") from None
        except OperationNotFoundError:
            raise HTTPException(status_code=404, detail="evidence revision not found") from None
        except OperationConflictError as error:
            raise HTTPException(status_code=409, detail=str(error)) from None

    @app.post(
        "/api/runs/{run_id}/report-revisions",
        response_model=ReportRevisionRecord,
        dependencies=protected,
    )
    def create_report_revision(
        run_id: str,
        request: ReportSpec,
    ) -> ReportRevisionRecord:
        try:
            workspace = context.get_workspace(run_id)
        except (FileNotFoundError, ValueError):
            raise HTTPException(status_code=404, detail="run not found") from None
        try:
            return _report_revision_service(context, workspace).create_revision(request)
        except FileNotFoundError as error:
            raise HTTPException(
                status_code=409,
                detail=f"run is not ready for report revision: {error}",
            ) from None
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from None

    @app.get(
        "/api/runs/{run_id}/report-revisions",
        response_model=ReportRevisionIndex,
        dependencies=protected,
    )
    def list_report_revisions(run_id: str) -> ReportRevisionIndex:
        try:
            workspace = context.get_workspace(run_id)
            return _report_revision_service(context, workspace).list_revisions()
        except (FileNotFoundError, ValueError):
            raise HTTPException(status_code=404, detail="run not found") from None

    @app.get(
        "/api/runs/{run_id}/report-revisions/latest",
        response_model=ReportRevisionRecord,
        dependencies=protected,
    )
    def get_latest_report_revision(run_id: str) -> ReportRevisionRecord:
        try:
            workspace = context.get_workspace(run_id)
        except (FileNotFoundError, ValueError):
            raise HTTPException(status_code=404, detail="run not found") from None
        revision = _report_revision_service(context, workspace).latest_revision()
        if revision is None:
            raise HTTPException(status_code=404, detail="no report revision exists")
        return revision

    @app.get(
        "/api/runs/{run_id}/report-revisions/{revision_id}",
        response_model=ReportRevisionRecord,
        dependencies=protected,
    )
    def get_report_revision(
        run_id: str,
        revision_id: str,
    ) -> ReportRevisionRecord:
        try:
            workspace = context.get_workspace(run_id)
            return _report_revision_service(context, workspace).get_revision(revision_id)
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail="run not found") from None
        except KeyError:
            raise HTTPException(
                status_code=404,
                detail="report revision not found",
            ) from None
        except ValueError:
            raise HTTPException(status_code=404, detail="run not found") from None

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


def _operation_service(
    context: ApiContext,
    workspace: RunWorkspace,
) -> OperationService:
    runtime = getattr(context.pipeline, "runtime", None)
    return OperationService(
        workspace,
        asr_backend=(runtime.asr_backend if runtime is not None else None),
        ocr_backend=(runtime.ocr_backend if runtime is not None else None),
        ffmpeg_path=(runtime.ffmpeg_path if runtime is not None else "ffmpeg"),
        ffprobe_path=(runtime.ffprobe_path if runtime is not None else "ffprobe"),
        lock=context._operations_lock,
    )


def _report_revision_service(
    context: ApiContext,
    workspace: RunWorkspace,
) -> ReportRevisionService:
    return ReportRevisionService(
        workspace,
        composer=context.pipeline.runtime.note_composer,
        pdf_browser_executable=context.pipeline.runtime.pdf_browser_executable,
    )


def _require_provider(context: ApiContext, provider_id: str) -> ProviderSpec:
    provider = context.model_registry.providers.get(provider_id)
    if provider is None:
        raise HTTPException(status_code=404, detail="provider not found")
    return provider


def _component_report(
    context: ApiContext,
    *,
    tier: HardwareTier | None = None,
) -> ComponentReport:
    resolved_tier = tier or recommend_hardware_tier(
        detect_hardware(disk_path=context.data_root)
    )
    return ComponentReport(
        hardware_tier=resolved_tier,
        recommendation=context.component_manager.recommendation(resolved_tier),
        inventory=context.component_manager.inventory(resolved_tier),
    )


def _activate_managed_local_models(
    context: ApiContext,
    tier: HardwareTier,
) -> None:
    try:
        settings = context.component_manager.local_adapter_settings(tier)
    except ComponentNotReadyError:
        raise ComponentManagerError(
            "recommended local ASR/OCR model payloads are not complete"
        ) from None

    registry = context.model_registry.model_copy(deep=True)
    defaults = ModelRegistry.with_local_defaults()
    local_provider = registry.providers.get("local")
    if local_provider is None:
        registry.providers["local"] = defaults.providers["local"].model_copy(deep=True)
    elif local_provider.protocol is not ProviderProtocol.LOCAL:
        raise ComponentManagerError(
            "provider ID 'local' is occupied by a non-local protocol"
        )

    for model_id, selected_settings in (
        ("faster-whisper", settings.asr),
        ("paddleocr", settings.ocr),
    ):
        default_model = defaults.models[model_id]
        model = registry.models.get(model_id)
        if model is None:
            model = default_model.model_copy(deep=True)
            registry.models[model_id] = model
        elif model.provider_id != "local":
            raise ComponentManagerError(
                f"model ID '{model_id}' is occupied by a non-local provider"
            )
        model.capabilities.update(default_model.capabilities)
        model.settings = dict(selected_settings)
        model.enabled = True

    for role, model_id in (
        ("asr.primary", "faster-whisper"),
        ("ocr.primary", "paddleocr"),
        ("vision.text_detector", "paddleocr"),
    ):
        binding = registry.roles.get(role)
        if binding is None or binding.primary_model_id == model_id:
            registry.bind(role, model_id)

    validated = ModelRegistry.model_validate(registry.model_dump(mode="json"))
    validated.save(context.registry_path)
    context.model_registry = validated
    context.refresh_pipeline()


def _provider_api_key(
    context: ApiContext,
    provider: ProviderSpec,
) -> str | None:
    """Resolve a provider secret only at the request boundary."""

    if provider.credential_ref is None:
        return None
    return context.secret_store.get(provider.id)


def _provider_endpoint(provider: ProviderSpec, path: str | None) -> str:
    """Join a protocol path without discarding a reverse-proxy base path."""

    if provider.base_url is None:
        raise HTTPException(status_code=422, detail="provider base URL is missing")
    parts = urlsplit(provider.base_url)
    if (
        parts.scheme not in {"http", "https"}
        or not parts.hostname
        or parts.username is not None
        or parts.password is not None
    ):
        raise HTTPException(status_code=422, detail="provider base URL is invalid")
    base_path = parts.path.rstrip("/")
    if path is None:
        joined_path = base_path or "/"
    else:
        suffix = f"/{path.lstrip('/')}"
        joined_path = base_path if base_path.endswith(suffix) else f"{base_path}{suffix}"
    return urlunsplit((parts.scheme, parts.netloc, joined_path, "", ""))


def _provider_headers(
    provider: ProviderSpec,
    api_key: str | None,
) -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    try:
        headers.update(provider_auth_headers(provider, api_key))
    except ProviderAuthError as error:
        status_code = 409 if "not configured" in str(error) else 422
        raise HTTPException(status_code=status_code, detail=str(error)) from None
    return headers


def _positive_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value > 0:
        return value
    return None


def _parse_discovered_models(
    protocol: ProviderProtocol,
    payload: object,
) -> list[DiscoveredModel]:
    if not isinstance(payload, dict):
        raise HTTPException(
            status_code=422,
            detail="provider model discovery returned an invalid catalog",
        )
    if protocol in {
        ProviderProtocol.OPENAI_RESPONSES,
        ProviderProtocol.OPENAI_CHAT_COMPLETIONS,
        ProviderProtocol.OPENAI_AUDIO_TRANSCRIPTIONS,
        ProviderProtocol.ANTHROPIC_MESSAGES,
    }:
        raw_models = payload.get("data")
    else:
        raw_models = payload.get("models")
    if not isinstance(raw_models, list):
        raise HTTPException(
            status_code=422,
            detail="provider model discovery returned an invalid model list",
        )

    discovered: dict[str, DiscoveredModel] = {}
    for raw in raw_models[:5_000]:
        if not isinstance(raw, dict):
            continue
        raw_id = raw.get("id")
        if protocol in {
            ProviderProtocol.GEMINI_GENERATE_CONTENT,
            ProviderProtocol.GEMINI_INTERACTIONS,
        }:
            raw_id = raw.get("name", raw_id)
        elif protocol is ProviderProtocol.OLLAMA_NATIVE_CHAT:
            raw_id = raw.get("model", raw.get("name", raw_id))
        if not isinstance(raw_id, str) or not raw_id.strip():
            continue
        model_id = raw_id.strip()
        if model_id.startswith("models/"):
            model_id = model_id.removeprefix("models/")
        display_value = raw.get("display_name", raw.get("displayName", model_id))
        display_name = (
            display_value.strip()
            if isinstance(display_value, str) and display_value.strip()
            else model_id
        )
        context_window = _positive_int(
            raw.get(
                "context_window",
                raw.get("inputTokenLimit", raw.get("max_input_tokens")),
            )
        )
        discovered[model_id] = DiscoveredModel(
            model_id=model_id,
            display_name=display_name,
            context_window=context_window,
        )
    return sorted(
        discovered.values(),
        key=lambda item: (item.display_name.casefold(), item.model_id.casefold()),
    )


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
