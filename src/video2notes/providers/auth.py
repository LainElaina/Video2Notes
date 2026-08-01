"""Shared, protocol-aware HTTP authentication header construction."""

from __future__ import annotations

import re

from .registry import AuthScheme, ProviderProtocol, ProviderSpec

_HEADER_NAME = re.compile(r"^[A-Za-z0-9-]{1,64}$")
_FORBIDDEN_CUSTOM_HEADERS = {
    "connection",
    "content-length",
    "cookie",
    "host",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
    "x-video2notes-token",
}


class ProviderAuthError(ValueError):
    """A persisted authentication configuration cannot form a safe request."""


def provider_auth_headers(
    provider: ProviderSpec,
    secret: str | None,
) -> dict[str, str]:
    """Return secret-bearing request headers without mutating the registry."""

    if provider.auth_scheme is AuthScheme.NONE:
        return {}
    if not secret:
        raise ProviderAuthError("provider credential is not configured")
    if provider.auth_scheme is AuthScheme.BEARER:
        return {"Authorization": f"Bearer {secret}"}
    if provider.auth_scheme is AuthScheme.X_API_KEY:
        headers = {"x-api-key": secret}
        if provider.protocol is ProviderProtocol.ANTHROPIC_MESSAGES:
            version = provider.protocol_options.get(
                "anthropic_version",
                "2023-06-01",
            )
            if not isinstance(version, str) or not version.strip():
                raise ProviderAuthError("Anthropic protocol version is invalid")
            headers["anthropic-version"] = version.strip()
        return headers
    if provider.auth_scheme is AuthScheme.X_GOOG_API_KEY:
        return {"x-goog-api-key": secret}

    header_name = provider.protocol_options.get("auth_header_name")
    if (
        not isinstance(header_name, str)
        or _HEADER_NAME.fullmatch(header_name) is None
        or header_name.casefold() in _FORBIDDEN_CUSTOM_HEADERS
    ):
        raise ProviderAuthError("custom authentication header name is invalid")
    prefix = provider.protocol_options.get("auth_header_prefix", "")
    if not isinstance(prefix, str) or "\r" in prefix or "\n" in prefix:
        raise ProviderAuthError("custom authentication header prefix is invalid")
    return {header_name: f"{prefix}{secret}"}
