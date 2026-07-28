"""Capability-aware model registry and local credential references."""

from .registry import (
    Capability,
    Locality,
    ModelRegistry,
    ModelSpec,
    ProviderKind,
    ProviderSpec,
    RoleBinding,
)
from .secrets import KeyringSecretStore, SecretStatus

__all__ = [
    "Capability",
    "KeyringSecretStore",
    "Locality",
    "ModelRegistry",
    "ModelSpec",
    "ProviderKind",
    "ProviderSpec",
    "RoleBinding",
    "SecretStatus",
]
