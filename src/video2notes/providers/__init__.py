"""Capability-aware model registry and local credential references."""

from .registry import (
    PROTOCOL_CATALOG,
    ROLE_REQUIREMENTS,
    AuthScheme,
    Capability,
    Locality,
    ModelRegistry,
    ModelSpec,
    ProtocolTemplate,
    ProviderKind,
    ProviderProtocol,
    ProviderSpec,
    RoleBinding,
    StreamTransport,
)
from .secrets import KeyringSecretStore, SecretStatus

__all__ = [
    "AuthScheme",
    "Capability",
    "KeyringSecretStore",
    "Locality",
    "ModelRegistry",
    "ModelSpec",
    "ProviderKind",
    "ProviderProtocol",
    "ProviderSpec",
    "PROTOCOL_CATALOG",
    "ROLE_REQUIREMENTS",
    "ProtocolTemplate",
    "RoleBinding",
    "SecretStatus",
    "StreamTransport",
]
