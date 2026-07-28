"""Windows Credential Manager-backed provider secret references."""

from __future__ import annotations

from enum import StrEnum
from typing import Protocol


class KeyringBackend(Protocol):
    def set_password(self, service_name: str, username: str, password: str) -> None: ...

    def get_password(self, service_name: str, username: str) -> str | None: ...

    def delete_password(self, service_name: str, username: str) -> None: ...


class SecretStatus(StrEnum):
    CONFIGURED = "configured"
    NOT_CONFIGURED = "not_configured"


class KeyringSecretStore:
    """Store secrets out of SQLite/config files and expose status, not values."""

    SERVICE_NAME = "Video2Notes"

    def __init__(self, backend: KeyringBackend | None = None):
        if backend is None:
            import keyring

            backend = keyring
        self._backend = backend

    def credential_ref(self, provider_id: str) -> str:
        return f"keyring://{self.SERVICE_NAME}/providers/{provider_id}"

    def set(self, provider_id: str, secret: str) -> str:
        if not secret:
            raise ValueError("provider secret cannot be empty")
        self._backend.set_password(self.SERVICE_NAME, provider_id, secret)
        return self.credential_ref(provider_id)

    def get(self, provider_id: str) -> str | None:
        return self._backend.get_password(self.SERVICE_NAME, provider_id)

    def status(self, provider_id: str) -> SecretStatus:
        value = self.get(provider_id)
        return SecretStatus.CONFIGURED if value is not None else SecretStatus.NOT_CONFIGURED

    def delete(self, provider_id: str) -> None:
        if self.get(provider_id) is not None:
            self._backend.delete_password(self.SERVICE_NAME, provider_id)
