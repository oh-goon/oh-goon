from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import Field, field_validator, model_validator

from oh_goon.core.dependency_identity import normalize_package_name
from oh_goon.core.models.agent_review import AgentAdjudicationRecord, AgentReviewRecord
from oh_goon.core.models.base import ImmutableModel


class ObservationSourceEvidence(ImmutableModel):
    """Producer member revisions retained with one observation."""

    path: str = Field(min_length=1, max_length=240)
    member_revision: str = Field(pattern=r"^[0-9a-f]{64}$")


class DependencyEvidenceAssessment(ImmutableModel):
    """Repository facts about the supplied lock, independent of observation age."""

    scope: Literal["locked-inventory"] = "locked-inventory"
    freshness: Literal["current", "stale", "unknown"] = "unknown"
    completeness: Literal["complete", "incomplete", "unknown"] = "unknown"
    availability: Literal["available", "unsupported", "unavailable", "missing"] = "unavailable"
    absence_admissible: bool = False
    reason: str | None = None

    @model_validator(mode="after")
    def validate_absence(self) -> DependencyEvidenceAssessment:
        if self.absence_admissible and (
            self.freshness != "current"
            or self.completeness != "complete"
            or self.availability != "available"
        ):
            raise ValueError("Lock absence requires current complete available evidence")
        return self


class DependencyObservation(ImmutableModel):
    """One producer-selected package in a resolved lock inventory."""

    ecosystem: Literal["pypi"] = "pypi"
    package_name: str = Field(min_length=1, max_length=200)
    node_id: str = Field(min_length=1)
    resolved_version: str = Field(min_length=1, max_length=200)
    resolved_source: Literal["uv.lock"] = "uv.lock"
    package_source: str = Field(min_length=1)
    contexts: tuple[str, ...] = Field(min_length=1)
    relationships: tuple[dict[str, Any], ...] = ()

    @field_validator("package_name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        normalized = normalize_package_name(value)
        if not normalized:
            raise ValueError("Dependency observation package name must be non-empty")
        return normalized


class DependencyObservationSnapshot(ImmutableModel):
    """Retained producer observation plus its searchable locked-package projection.

    Declarations and installed environments are outside this scope. Raw adapter
    evidence is retained so the producer can requalify its original source claims.
    """

    kind: Literal["dependency"] = "dependency"
    schema_version: Literal[2] = 2
    ecosystem: Literal["pypi"] = "pypi"
    sources: tuple[ObservationSourceEvidence, ...] = Field(default=(), max_length=512)
    evidence: DependencyEvidenceAssessment
    producer_implementation_identity: str | None = None
    adapter_observation: dict[str, Any] = Field(default_factory=dict)
    producer_observation: dict[str, Any] = Field(default_factory=dict)
    dependencies: tuple[DependencyObservation, ...] = Field(default=(), max_length=100_000)

    @model_validator(mode="after")
    def validate_snapshot(self) -> DependencyObservationSnapshot:
        paths = [item.path for item in self.sources]
        if len(paths) != len(set(paths)):
            raise ValueError("Observation source paths must be unique")
        identifiers = [item.node_id for item in self.dependencies]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("Dependency observation selections must be unique")
        if self.evidence.availability == "available" and (
            not self.sources
            or not self.adapter_observation
            or not self.producer_observation
            or not self.producer_implementation_identity
        ):
            raise ValueError("Available dependency observations require producer evidence")
        if self.evidence.availability != "available" and self.dependencies:
            raise ValueError("Unavailable dependency evidence cannot publish packages")
        return self


class AgentReviewObservation(ImmutableModel):
    kind: Literal["agent_review"] = "agent_review"
    record: AgentReviewRecord | AgentAdjudicationRecord


class AgentHarnessObservation(ImmutableModel):
    """Bounded native-agent claim, distinct from supervisor and producer facts."""

    kind: Literal["agent", "tool", "context"]
    summary: str = Field(min_length=1, max_length=4000)
    native_event_id: str | None = Field(default=None, min_length=1, max_length=160)

    @field_validator("summary", "native_event_id")
    @classmethod
    def nonblank_text(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("Agent harness observation text must be nonblank")
        return value


class NativeServiceExecutionEvidence(ImmutableModel):
    """Established native service facts reported by the original Step owner.

    These facts describe observed preflight and owned process closure. They are
    separate from model/native conversation claims and do not certify a solution.
    """

    authority: Literal["ORIGINAL_STEP_EXECUTION"] = "ORIGINAL_STEP_EXECUTION"
    backend_id: Literal["codex-native-tools"] = "codex-native-tools"
    configuration_id: str = Field(min_length=1, max_length=160)
    native_version: str = Field(min_length=1, max_length=128)
    permission_profile_id: str = Field(min_length=1, max_length=256)
    repository_access: Literal["read_only", "write"]
    disabled_features: tuple[Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{0,127}$")], ...] = (
        Field(max_length=64)
    )
    mcp_catalog: Literal["empty"] = "empty"
    native_state_separation: Literal["verified"] = "verified"
    preflight: Literal["verified"] = "verified"
    process_cleanup: Literal["verified"] = "verified"


class NativeAgentObservation(ImmutableModel):
    """Native protocol claims bound to the original source and Step occurrence.

    Conversation events and settled/failed flags remain untrusted harness claims.
    The optional execution child carries original Step facts under its own
    authority. Actual process receipts, cancellation and verification remain in
    the original Step evidence. A settled native conversation is not APP PASS.
    """

    kind: Literal["native_agent"] = "native_agent"
    agent: Literal["opencode", "pi", "codex"]
    implementation_identity: str = Field(min_length=1, max_length=160)
    profile: str = Field(min_length=1, max_length=64)
    input_identity: str = Field(min_length=1, max_length=160)
    occurrence: str = Field(min_length=1, max_length=240)
    source_identity: str = Field(min_length=1, max_length=160)
    role: Literal["report", "review", "adjudication", "proposal"]
    events: tuple[AgentHarnessObservation, ...] = Field(default=(), max_length=128)
    native_event_count: int = Field(ge=0)
    omitted_observations: int = Field(default=0, ge=0)
    settled: bool
    failed: bool
    authority: Literal["UNTRUSTED_HARNESS_CLAIM"] = "UNTRUSTED_HARNESS_CLAIM"
    execution: NativeServiceExecutionEvidence | None = Field(
        default=None, exclude_if=lambda value: value is None
    )

    @model_validator(mode="after")
    def native_service_execution_owner(self) -> NativeAgentObservation:
        if self.execution is not None and self.agent != "codex":
            raise ValueError("Codex service execution evidence requires the Codex harness")
        return self


FleetObservationEvidence = Annotated[
    DependencyObservationSnapshot | AgentReviewObservation | NativeAgentObservation,
    Field(discriminator="kind"),
]
