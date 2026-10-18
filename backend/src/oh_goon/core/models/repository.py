from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import AwareDatetime, Field, TypeAdapter, model_validator

from oh_goon.core.models.base import ImmutableModel
from oh_goon.core.product_identity import canonical_repository_path, repository_id
from oh_goon.core.vocabulary import FleetScope


class RepositoryIdentity(ImmutableModel):
    repository_id: str
    name: str
    path: Path

    @model_validator(mode="before")
    @classmethod
    def normalize_identity(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        data = dict(value)
        if "path" in data:
            canonical = canonical_repository_path(data["path"])
            data["path"] = str(canonical)
            data.setdefault("name", canonical.name)
            data.setdefault("repository_id", repository_id(canonical))
        return data

    @model_validator(mode="after")
    def validate_repository_id(self) -> RepositoryIdentity:
        expected = repository_id(self.path)
        if self.repository_id != expected:
            raise ValueError("RepositoryIdentity repository_id must match its canonical path")
        return self


class _RepositoryCatalogEntryBase(ImmutableModel):
    """Discovery + scope only. Readiness and Git facts come from operation results."""

    repository: RepositoryIdentity
    scope: FleetScope


class IncludedRepositoryCatalogEntry(_RepositoryCatalogEntryBase):
    scope: Literal[FleetScope.INCLUDED]
    exclusion_reason: Literal[None] = None
    excluded_at: Literal[None] = None


class ExcludedRepositoryCatalogEntry(_RepositoryCatalogEntryBase):
    scope: Literal[FleetScope.EXCLUDED]
    exclusion_reason: str = Field(min_length=1)
    excluded_at: AwareDatetime


type RepositoryCatalogEntry = Annotated[
    IncludedRepositoryCatalogEntry | ExcludedRepositoryCatalogEntry,
    Field(discriminator="scope"),
]

_REPOSITORY_CATALOG_ENTRY_ADAPTER = TypeAdapter(RepositoryCatalogEntry)


def parse_repository_catalog_entry(value: object) -> RepositoryCatalogEntry:
    """Parse persisted/API catalog data into its concrete scope variant."""
    return _REPOSITORY_CATALOG_ENTRY_ADAPTER.validate_python(value)
