"""Authenticated loopback API; no cloud service and no telemetry."""

from __future__ import annotations

import hmac
import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path, PurePosixPath
from typing import Annotated, Any

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
from video2notes.providers import (
    KeyringSecretStore,
    ModelRegistry,
    ProviderSpec,
    SecretStatus,
)
from video2notes.sources import (
    AcquisitionPolicy,
    AuthSpec,
    SourceError,
    SourceInput,
    SourceManifest,
    SourceRegistry,
    enumerate_browser_profiles,
)
from video2notes.system import (
    HardwareSnapshot,
    QualityMode,
    build_execution_plan,
    detect_hardware,
    recommend_hardware_tier,
)


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
    ):
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
        return RunWorkspace(candidate)


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
        except SourceError as error:
            raise HTTPException(status_code=422, detail=str(error)) from None
        except (FileNotFoundError, ValueError) as error:
            raise HTTPException(status_code=422, detail=str(error)) from None
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
        return context.list_runs()

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
        return workspace.manifest

    @app.get(
        "/api/runs/{run_id}",
        response_model=ArtifactManifest,
        dependencies=protected,
    )
    def get_run(run_id: str) -> ArtifactManifest:
        try:
            return context.get_workspace(run_id).manifest
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
            if relative.is_absolute() or ".." in relative.parts:
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
        return context.job_manager.list()

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
            return context.job_manager.get(
                run_id,
                after_sequence=after_sequence,
            )
        except JobNotFoundError:
            raise HTTPException(status_code=404, detail="job not found") from None

    @app.post(
        "/api/jobs/{run_id}/cancel",
        response_model=JobSnapshot,
        dependencies=protected,
    )
    def cancel_job(run_id: str) -> JobSnapshot:
        try:
            return context.job_manager.request_cancel(run_id)
        except JobNotFoundError:
            raise HTTPException(status_code=404, detail="job not found") from None
        except JobAlreadyRunningError as error:
            raise HTTPException(status_code=409, detail=str(error)) from None

    return app


def _require_provider(context: ApiContext, provider_id: str) -> ProviderSpec:
    provider = context.model_registry.providers.get(provider_id)
    if provider is None:
        raise HTTPException(status_code=404, detail="provider not found")
    return provider
