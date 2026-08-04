"""Persistent, restartable client for a trusted runtime worker executable."""

from __future__ import annotations

import os
import queue
import subprocess
import threading
import uuid
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from video2notes.components.runtime_downloaders import runtime_manifest_sha256
from video2notes.components.runtime_models import RuntimePackageManifest, RuntimeTransport

from .protocol import RuntimeWorkerHello, RuntimeWorkerRequest, RuntimeWorkerResponse


class RuntimeWorkerError(RuntimeError):
    """A worker is unavailable, incompatible, timed out, or rejected a request."""


@dataclass(frozen=True, slots=True)
class RuntimeWorkerIdentity:
    source: str
    package_id: str
    package_version: str
    manifest_sha256: str
    protocol_version: int
    instance_id: str
    capability_id: str

    def as_dict(self) -> dict[str, str | int]:
        return {
            "source": self.source,
            "package_id": self.package_id,
            "package_version": self.package_version,
            "manifest_sha256": self.manifest_sha256,
            "protocol_version": self.protocol_version,
            "instance_id": self.instance_id,
            "capability_id": self.capability_id,
        }


class RuntimeWorkerClient:
    """Serialize requests over JSON lines and restart once after a worker crash."""

    def __init__(
        self,
        root: str | Path,
        manifest: RuntimePackageManifest,
        *,
        source: str,
        instance_id: str,
        capability_id: str,
        timeout_seconds: float = 900.0,
    ) -> None:
        capability = next(
            (
                item
                for item in manifest.capabilities
                if item.capability_id == capability_id
            ),
            None,
        )
        if (
            capability is None
            or capability.transport is not RuntimeTransport.WORKER
            or capability.entrypoint is None
        ):
            raise ValueError("runtime package does not provide the requested worker capability")
        self.root = Path(root).expanduser().resolve()
        self.manifest = manifest
        self.capability = capability
        self.timeout_seconds = timeout_seconds
        self.identity = RuntimeWorkerIdentity(
            source=source,
            package_id=manifest.package_id,
            package_version=manifest.version,
            manifest_sha256=runtime_manifest_sha256(manifest),
            protocol_version=manifest.runtime_protocol_version,
            instance_id=instance_id,
            capability_id=capability_id,
        )
        self._process: subprocess.Popen[str] | None = None
        self._responses: queue.Queue[str | None] = queue.Queue()
        self._reader: threading.Thread | None = None
        self._lock = threading.RLock()
        self._closed = False

    def hello(self) -> RuntimeWorkerHello:
        result = self.request("hello", {}, retry_once=True)
        hello = RuntimeWorkerHello.model_validate(result)
        expected = self.identity
        if (
            hello.package_id != expected.package_id
            or hello.package_version != expected.package_version
            or hello.manifest_sha256 != expected.manifest_sha256
            or hello.protocol_version != expected.protocol_version
        ):
            self.close(force=True)
            raise RuntimeWorkerError("runtime worker identity does not match its package manifest")
        return hello

    def request(
        self,
        method: str,
        params: dict[str, Any],
        *,
        retry_once: bool = True,
    ) -> dict[str, Any]:
        with self._lock:
            if self._closed:
                raise RuntimeWorkerError("runtime worker client is closed")
            attempts = 2 if retry_once else 1
            last_error: RuntimeWorkerError | None = None
            for attempt in range(attempts):
                try:
                    return self._request_once(method, params)
                except RuntimeWorkerError as error:
                    last_error = error
                    self._stop_process()
                    if attempt + 1 >= attempts:
                        break
            raise last_error or RuntimeWorkerError("runtime worker request failed")

    def close(self, *, force: bool = False) -> None:
        with self._lock:
            if self._closed:
                return
            if not force and self._process is not None and self._process.poll() is None:
                with suppress(RuntimeWorkerError):
                    self._request_once("shutdown", {})
            self._closed = True
            self._stop_process()

    def _request_once(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        process = self._ensure_process()
        request = RuntimeWorkerRequest.model_validate(
            {
                "request_id": uuid.uuid4().hex,
                "method": method,
                "params": params,
            }
        )
        if process.stdin is None:
            raise RuntimeWorkerError("runtime worker stdin is unavailable")
        try:
            process.stdin.write(request.model_dump_json() + "\n")
            process.stdin.flush()
        except (BrokenPipeError, OSError):
            raise RuntimeWorkerError(
                "runtime worker stopped before accepting the request"
            ) from None

        try:
            raw = self._responses.get(timeout=self.timeout_seconds)
        except queue.Empty:
            raise RuntimeWorkerError("runtime worker request timed out") from None
        if raw is None:
            raise RuntimeWorkerError("runtime worker exited without a response")
        try:
            response = RuntimeWorkerResponse.model_validate_json(raw)
        except ValueError:
            raise RuntimeWorkerError("runtime worker returned an invalid response") from None
        if response.request_id != request.request_id:
            raise RuntimeWorkerError("runtime worker response ID does not match the request")
        if not response.ok:
            raise RuntimeWorkerError(
                f"runtime worker request failed: {response.error_code or 'unknown_error'}"
            )
        return response.result or {}

    def _ensure_process(self) -> subprocess.Popen[str]:
        if self._process is not None and self._process.poll() is None:
            return self._process
        entrypoint = self.capability.entrypoint
        if entrypoint is None:  # pragma: no cover - constructor protects this
            raise RuntimeWorkerError("runtime worker entrypoint is unavailable")
        executable = (self.root / Path(entrypoint.replace("/", os.sep))).resolve()
        if not executable.is_relative_to(self.root) or not executable.is_file():
            raise RuntimeWorkerError("runtime worker executable is missing")
        creation_flags = 0x08000000 if os.name == "nt" else 0
        try:
            process = subprocess.Popen(
                [str(executable), "serve", "--package-root", str(self.root)],
                cwd=self.root,
                env=os.environ.copy(),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                errors="strict",
                bufsize=1,
                shell=False,
                creationflags=creation_flags,
            )
        except OSError:
            raise RuntimeWorkerError("runtime worker could not be started") from None
        self._responses = queue.Queue()
        self._process = process
        self._reader = threading.Thread(
            target=self._read_responses,
            args=(process,),
            name=f"runtime-worker-{self.identity.package_id}",
            daemon=True,
        )
        self._reader.start()
        return process

    def _read_responses(self, process: subprocess.Popen[str]) -> None:
        stdout = process.stdout
        if stdout is None:
            self._responses.put(None)
            return
        try:
            for line in stdout:
                self._responses.put(line)
        finally:
            self._responses.put(None)

    def _stop_process(self) -> None:
        process = self._process
        self._process = None
        if process is None:
            return
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)
        for stream in (process.stdin, process.stdout):
            if stream is not None:
                stream.close()
