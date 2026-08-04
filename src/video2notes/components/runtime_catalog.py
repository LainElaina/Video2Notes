"""Trusted release catalog for isolated runtime packages.

The built-in catalog is intentionally empty until release archives have real,
published SHA-256 values.  Tests, development builds, and future signed catalog
updates can inject the exact same schema without weakening package validation.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Self

from pydantic import Field, model_validator

from .runtime_models import RuntimeModel, RuntimePackageRelease

RUNTIME_CATALOG_ENVIRONMENT = "VIDEO2NOTES_RUNTIME_CATALOG"


class RuntimePackageCatalog(RuntimeModel):
    schema_version: int = Field(default=1, alias="schema", ge=1, le=1)
    catalog_id: str | None = Field(default=None, min_length=1, max_length=160)
    target_triple: str | None = Field(default=None, min_length=1, max_length=128)
    runtime_protocol_version: int | None = Field(default=None, ge=1, le=1000)
    release_profile: str | None = Field(default=None, min_length=1, max_length=80)
    releases: tuple[RuntimePackageRelease, ...] = Field(default=(), alias="packages")

    @model_validator(mode="after")
    def validate_releases(self) -> Self:
        identities = [(item.package_id, item.version) for item in self.releases]
        if len(identities) != len(set(identities)):
            raise ValueError("runtime catalog releases must have unique package/version pairs")
        if self.target_triple is not None and any(
            item.target_triple != self.target_triple for item in self.releases
        ):
            raise ValueError("runtime catalog target must match every package")
        if self.runtime_protocol_version is not None and any(
            item.runtime_protocol_version != self.runtime_protocol_version
            for item in self.releases
        ):
            raise ValueError("runtime catalog protocol must match every package")
        return self

    def get(self, package_id: str, version: str) -> RuntimePackageRelease:
        for release in self.releases:
            if release.package_id == package_id and release.version == version:
                return release
        raise KeyError(f"unknown runtime release: {package_id}@{version}")

    def versions(self, package_id: str) -> tuple[RuntimePackageRelease, ...]:
        return tuple(
            sorted(
                (item for item in self.releases if item.package_id == package_id),
                key=lambda item: _version_key(item.version),
            )
        )

    def latest(self, package_id: str) -> RuntimePackageRelease:
        versions = self.versions(package_id)
        if not versions:
            raise KeyError(f"unknown runtime package: {package_id}")
        return versions[-1]

    def merge(self, other: RuntimePackageCatalog) -> RuntimePackageCatalog:
        if not self.releases and all(
            item is None
            for item in (
                self.catalog_id,
                self.target_triple,
                self.runtime_protocol_version,
                self.release_profile,
            )
        ):
            return other
        if not other.releases and all(
            item is None
            for item in (
                other.catalog_id,
                other.target_triple,
                other.runtime_protocol_version,
                other.release_profile,
            )
        ):
            return self
        return RuntimePackageCatalog(
            catalog_id=(
                self.catalog_id if self.catalog_id == other.catalog_id else None
            ),
            target_triple=(
                self.target_triple if self.target_triple == other.target_triple else None
            ),
            runtime_protocol_version=(
                self.runtime_protocol_version
                if self.runtime_protocol_version == other.runtime_protocol_version
                else None
            ),
            release_profile=(
                self.release_profile
                if self.release_profile == other.release_profile
                else None
            ),
            packages=(*self.releases, *other.releases),
        )


DEFAULT_RUNTIME_PACKAGE_CATALOG = RuntimePackageCatalog()


def load_runtime_package_catalog(path: str | Path) -> RuntimePackageCatalog:
    catalog_path = Path(path).expanduser().resolve()
    if not catalog_path.is_file():
        raise FileNotFoundError(f"runtime package catalog does not exist: {catalog_path}")
    return RuntimePackageCatalog.model_validate_json(catalog_path.read_text(encoding="utf-8"))


def runtime_catalog_from_environment(
    environment: dict[str, str] | None = None,
) -> RuntimePackageCatalog:
    """Load one or more explicitly trusted local catalog files.

    Multiple files use the platform path separator.  Merely importing this
    module never reads the environment or filesystem; callers opt in here.
    """

    selected = os.environ if environment is None else environment
    raw_paths = selected.get(RUNTIME_CATALOG_ENVIRONMENT, "").strip()
    if not raw_paths:
        return DEFAULT_RUNTIME_PACKAGE_CATALOG
    catalog = DEFAULT_RUNTIME_PACKAGE_CATALOG
    for raw_path in raw_paths.split(os.pathsep):
        if raw_path.strip():
            catalog = catalog.merge(load_runtime_package_catalog(raw_path.strip()))
    return catalog


def _version_key(version: str) -> tuple[tuple[int, int | str], ...]:
    """Provide deterministic natural ordering without adding a packaging dependency."""

    parts = re.split(r"[._-]", version)
    return tuple((1, int(part)) if part.isdigit() else (0, part.casefold()) for part in parts)
