"""Versioned JSON-lines contract shared by the core and runtime workers."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

RUNTIME_WORKER_PROTOCOL_VERSION: Literal[1] = 1


class RuntimeWorkerModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RuntimeWorkerRequest(RuntimeWorkerModel):
    protocol_version: Literal[1] = RUNTIME_WORKER_PROTOCOL_VERSION
    request_id: str = Field(min_length=1, max_length=120)
    method: Literal[
        "hello",
        "asr.transcribe",
        "ocr.recognize",
        "shutdown",
    ]
    params: dict[str, Any] = Field(default_factory=dict)


class RuntimeWorkerResponse(RuntimeWorkerModel):
    protocol_version: Literal[1] = RUNTIME_WORKER_PROTOCOL_VERSION
    request_id: str = Field(min_length=1, max_length=120)
    ok: bool
    result: dict[str, Any] | None = None
    error_code: str | None = Field(default=None, max_length=120)
    error_type: str | None = Field(default=None, max_length=120)

    @model_validator(mode="after")
    def validate_result(self) -> RuntimeWorkerResponse:
        if self.ok:
            if self.result is None or self.error_code is not None or self.error_type is not None:
                raise ValueError("successful runtime responses require only a result")
        elif self.result is not None or not self.error_code or not self.error_type:
            raise ValueError("failed runtime responses require a structured error")
        return self


class RuntimeWorkerHello(RuntimeWorkerModel):
    protocol_version: Literal[1] = RUNTIME_WORKER_PROTOCOL_VERSION
    package_id: str = Field(min_length=1, max_length=120)
    package_version: str = Field(min_length=1, max_length=80)
    manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    capabilities: tuple[str, ...]
    supported_devices: dict[str, tuple[str, ...]] = Field(default_factory=dict)
    engine_versions: dict[str, str] = Field(default_factory=dict)
    cuda_available: dict[str, bool] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_capabilities(self) -> RuntimeWorkerHello:
        if len(self.capabilities) != len(set(self.capabilities)):
            raise ValueError("runtime worker capabilities must be unique")
        unknown = set(self.supported_devices) - set(self.capabilities)
        if unknown:
            raise ValueError("runtime worker devices reference unknown capabilities")
        return self
