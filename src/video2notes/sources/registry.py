"""Strict source routing.

Routing is based on parsed hostnames, never substring matching.  This prevents
lookalike inputs such as ``youtube.com.attacker.invalid`` from reaching an
authenticated downloader.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any, Protocol
from urllib.parse import SplitResult, urlsplit

from video2notes.sources.errors import UnsupportedSourceError
from video2notes.sources.models import Platform, SourceInput, SourceKind


class SourceAdapter(Protocol):
    platform: Platform

    def matches(self, source: SourceInput) -> bool: ...

    async def probe(self, source: SourceInput, *args: Any, **kwargs: Any) -> Any: ...

    async def acquire(self, *args: Any, **kwargs: Any) -> Any: ...


_PLATFORM_DOMAINS: dict[Platform, tuple[str, ...]] = {
    Platform.BILIBILI: ("bilibili.com", "b23.tv"),
    Platform.YOUTUBE: ("youtube.com", "youtube-nocookie.com", "youtu.be"),
    Platform.X: ("x.com", "twitter.com"),
}


def parse_supported_url(value: str) -> tuple[Platform, SplitResult]:
    try:
        parsed = urlsplit(value.strip())
    except ValueError as exc:
        raise UnsupportedSourceError("invalid source URL") from exc
    if parsed.scheme.lower() not in {"http", "https"}:
        raise UnsupportedSourceError("source URL must use http or https")
    if parsed.username is not None or parsed.password is not None:
        raise UnsupportedSourceError("source URL cannot contain credentials")
    try:
        raw_hostname = parsed.hostname
    except ValueError as exc:
        raise UnsupportedSourceError("invalid source hostname") from exc
    if not raw_hostname:
        raise UnsupportedSourceError("source URL is missing a hostname")
    try:
        hostname = raw_hostname.rstrip(".").encode("idna").decode("ascii").lower()
    except UnicodeError as exc:
        raise UnsupportedSourceError("invalid source hostname") from exc

    for platform, domains in _PLATFORM_DOMAINS.items():
        if any(hostname == domain or hostname.endswith(f".{domain}") for domain in domains):
            return platform, parsed
    raise UnsupportedSourceError("URL is not from Bilibili, YouTube, or X")


def platform_for_url(value: str) -> Platform:
    return parse_supported_url(value)[0]


class SourceRegistry:
    def __init__(self, adapters: Iterable[SourceAdapter]):
        self._adapters: dict[Platform, SourceAdapter] = {}
        for adapter in adapters:
            if adapter.platform in self._adapters:
                raise ValueError(f"duplicate source adapter for {adapter.platform}")
            self._adapters[adapter.platform] = adapter

    @classmethod
    def default(cls) -> SourceRegistry:
        from video2notes.sources.local import LocalFileAdapter
        from video2notes.sources.ytdlp import (
            BilibiliAdapter,
            XAdapter,
            YouTubeAdapter,
        )

        return cls(
            [
                LocalFileAdapter(),
                BilibiliAdapter(),
                YouTubeAdapter(),
                XAdapter(),
            ]
        )

    def resolve(self, source: SourceInput) -> SourceAdapter:
        if source.kind is SourceKind.LOCAL:
            platform = Platform.LOCAL
        else:
            platform = platform_for_url(source.value)
        adapter = self._adapters.get(platform)
        if adapter is None:
            raise UnsupportedSourceError(f"no adapter is registered for {platform}")
        if not adapter.matches(source):
            raise UnsupportedSourceError(f"source does not match the {platform} adapter")
        return adapter

