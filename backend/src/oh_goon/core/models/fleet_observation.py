from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator

from oh_goon.core.dependency_identity import normalize_package_name
from oh_goon.core.models.base import ImmutableModel


class ObservationSourceEvidence(ImmutableModel):
    """Exact repository source bytes covered by one complete observation snapshot."""

    path: str = Field(min_length=1, max_length=240)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class DependencyObservation(ImmutableModel):
    """One typed Python-package fact inside a complete dependency snapshot."""

    ecosystem: Literal["pypi"] = "pypi"
    package_name: str = Field(min_length=1, max_length=200)
    relationship: Literal["direct", "transitive"]
    declared_range: str | None = Field(default=None, max_length=500)
    resolved_version: str | None = Field(default=None, min_length=1, max_length=200)
    declared_source: str | None = Field(default=None, min_length=1, max_length=240)
    resolved_source: str | None = Field(default=None, min_length=1, max_length=240)

    @field_validator("package_name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        normalized = normalize_package_name(value)
        if not normalized:
            raise ValueError("Dependency observation package name must be non-empty")
        return normalized

    @model_validator(mode="after")
    def validate_evidence(self) -> DependencyObservation:
        if self.declared_range is None and self.resolved_version is None:
            raise ValueError("Dependency observation requires declared or resolved evidence")
        if (self.declared_range is None) != (self.declared_source is None):
            raise ValueError("Declared dependency value/source must be present together")
        if (self.resolved_version is None) != (self.resolved_source is None):
            raise ValueError("Resolved dependency value/source must be present together")
        if self.relationship == "transitive" and self.declared_range is not None:
            raise ValueError(
                "Transitive dependency observations cannot claim direct declaration evidence"
            )
        return self


class DependencyObservationSnapshot(ImmutableModel):
    """Complete replacement snapshot for one repository's PyPI dependency scope.

    This object is Step evidence. Repository/Run/Goon identity is supplied by the
    canonical OperationReport and resolved Plan provenance that contains it.
    """

    kind: Literal["dependency"] = "dependency"
    schema_version: Literal[1] = 1
    ecosystem: Literal["pypi"] = "pypi"
    coverage: Literal["complete"] = "complete"
    sources: tuple[ObservationSourceEvidence, ...] = Field(min_length=1, max_length=512)
    dependencies: tuple[DependencyObservation, ...] = Field(
        default_factory=tuple, max_length=100_000
    )

    @model_validator(mode="after")
    def validate_snapshot(self) -> DependencyObservationSnapshot:
        paths = [item.path for item in self.sources]
        if len(paths) != len(set(paths)):
            raise ValueError("Observation snapshot source paths must be unique")
        identities = [
            (
                item.package_name,
                item.relationship,
                item.declared_range,
                item.resolved_version,
                item.declared_source,
                item.resolved_source,
            )
            for item in self.dependencies
        ]
        if len(identities) != len(set(identities)):
            raise ValueError("Dependency observation snapshot entries must be unique")
        return self


FleetObservationEvidence = Annotated[DependencyObservationSnapshot, Field(discriminator="kind")]
