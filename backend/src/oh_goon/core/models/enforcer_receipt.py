from __future__ import annotations

from typing import Literal

from pydantic import AwareDatetime, Field, model_validator

from oh_goon.core.models.base import ImmutableModel
from oh_goon.core.models.execution_identity import ResolvedExecutionReference
from oh_goon.core.models.fleet_executor import FleetExecutorPlacement
from oh_goon.core.models.fleet_plan_evidence import (
    PlanContentObjectEvidence,
    PlanImportApplicabilityEvidence,
    PlanSnapshotEvidence,
    PlanStepTeardownEvidence,
    PublishedPlanOutputEvidence,
)
from oh_goon.core.models.fleet_plan_execution_policy import CicdExecutionPolicy
from oh_goon.core.models.fleet_plan_requirements import RequirementCount
from oh_goon.core.models.fleet_plan_retry import PlanRetryDecisionEvidence
from oh_goon.core.models.fleet_plan_step_contract import PlanStepCache, PlanStepWorkspace
from oh_goon.core.models.fleet_plan_step_primitives import StepRetryPolicy
from oh_goon.core.models.goon_plan_source import validate_plan_source_repository_count
from oh_goon.core.models.operation import OperationOutcome
from oh_goon.core.vocabulary import FleetRunOutcome, FleetRunStatus


class EnforcerRunLifecycleFact(ImmutableModel):
    status: FleetRunStatus
    accepted_at: AwareDatetime
    started_at: AwareDatetime | None = None
    cancel_requested_at: AwareDatetime | None = None
    finished_at: AwareDatetime | None = None

    @model_validator(mode="after")
    def validate_lifecycle(self) -> EnforcerRunLifecycleFact:
        required, allowed = self.status.receipt_timestamp_contract
        present = tuple(
            name
            for name in ("started_at", "cancel_requested_at", "finished_at")
            if getattr(self, name) is not None
        )
        if any(name not in present for name in required) or any(
            name not in allowed for name in present
        ):
            raise ValueError(f"Invalid lifecycle timestamps for {self.status}")
        ordered = tuple(
            value
            for value in (
                self.accepted_at,
                self.started_at,
                self.cancel_requested_at,
                self.finished_at,
            )
            if value is not None
        )
        if ordered != tuple(sorted(ordered)):
            raise ValueError("Enforcer lifecycle timestamps must be monotonic")
        return self


class EnforcerToolFact(ImmutableModel):
    name: str = Field(min_length=1, max_length=240)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    byte_count: int = Field(ge=0)
    authority_id: str = Field(min_length=1)


class EnforcerPlacementIds(ImmutableModel):
    """Stable derived placement identities materialized for human receipt comparison."""

    inventory_id: str = Field(min_length=1)
    health_attestation_id: str = Field(min_length=1)
    placement_id: str = Field(min_length=1)
    reservation_id: str = Field(min_length=1)


class EnforcerFleetExecutionFact(ImmutableModel):
    """Round-trippable strict admission projection using the canonical placement fact."""

    kind: Literal["strict"] = "strict"
    placement: FleetExecutorPlacement
    placement_ids: EnforcerPlacementIds
    readmitted_from_run_id: str | None = Field(default=None, min_length=1)
    rerun_from_run_id: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def validate_placement_ids(self) -> EnforcerFleetExecutionFact:
        capacity = self.placement.executor_capacity
        expected = EnforcerPlacementIds(
            inventory_id=capacity.inventory_id,
            health_attestation_id=capacity.health_attestation_id,
            placement_id=capacity.placement_id,
            reservation_id=capacity.reservation_id,
        )
        if self.placement_ids != expected:
            raise ValueError("Enforcer placement IDs must match the admitted placement")
        return self


class EnforcerResolvedStepFact(ImmutableModel):
    step_id: str = Field(min_length=1)
    kind: str = Field(min_length=1)
    required_tool: str | None = None
    timeout_seconds: float | None = Field(default=None, gt=0)
    workspace: PlanStepWorkspace
    cache: PlanStepCache
    retry: StepRetryPolicy
    egress_mode: str = Field(min_length=1)
    repository_policy_id: str | None = None


class EnforcerEgressFact(ImmutableModel):
    mode: Literal["none", "cidr_allowlist", "hostname_allowlist", "host_unrestricted"]
    policy_id: str = Field(min_length=1)


class EnforcerAttemptFact(ImmutableModel):
    attempt: int = Field(ge=1, le=5)
    returncode: int
    duration_ms: int = Field(ge=0)
    tool: EnforcerToolFact | None = None
    cache_input: PlanSnapshotEvidence | None = None
    cache_output: PlanSnapshotEvidence | None = None
    retained_cache_reference_id: str | None = None
    egress: EnforcerEgressFact
    resource_policy_id: str | None = None
    retry: PlanRetryDecisionEvidence | None = None


class EnforcerStepResultFact(ImmutableModel):
    step_id: str = Field(min_length=1)
    kind: str = Field(min_length=1)
    status: str = Field(min_length=1)
    reason: str | None = Field(default=None, max_length=500)
    returncode: int | None = None
    duration_ms: int = Field(ge=0)
    repository_state_before: str = Field(min_length=1)
    repository_state_after: str = Field(min_length=1)
    attempts: tuple[EnforcerAttemptFact, ...] = Field(default_factory=tuple)
    teardown: PlanStepTeardownEvidence | None = None
    artifact_digests: tuple[str, ...] = Field(default_factory=tuple)
    outputs: tuple[PublishedPlanOutputEvidence, ...] = Field(default_factory=tuple)


class EnforcerRequirementsAdmissionFact(ImmutableModel):
    validation_id: str = Field(min_length=1)
    kind: Literal["requirements"] = "requirements"
    requirements_id: str = Field(min_length=1)
    requirement_count: RequirementCount


EnforcerRepositoryAdmissionFact = EnforcerRequirementsAdmissionFact


class EnforcerRequirementFact(ImmutableModel):
    kind: str = Field(min_length=1)
    detail: str = Field(min_length=1)


class EnforcerImportFact(ImmutableModel):
    import_id: str = Field(min_length=1)
    parent_import_id: str | None = None
    kind: Literal["goon", "plan"]
    name: str = Field(min_length=1)
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    step_ids: tuple[str, ...] = Field(min_length=1)
    requirements: tuple[EnforcerRequirementFact, ...] = Field(default_factory=tuple)


class EnforcerResolvedPlanFact(ImmutableModel):
    plan_id: str = Field(min_length=1)
    contract_version: int = Field(ge=1)
    name: str = Field(min_length=1)
    execution: CicdExecutionPolicy
    requirements: tuple[EnforcerRequirementFact, ...] = Field(default_factory=tuple)
    imports: tuple[EnforcerImportFact, ...] = Field(default_factory=tuple)
    steps: tuple[EnforcerResolvedStepFact, ...]


class EnforcerHostnameResolutionFact(ImmutableModel):
    """Privacy-safe DNS evidence: hostname plus digest of the frozen address set."""

    hostname: str = Field(min_length=3, max_length=253)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class EnforcerExecutionInputFact(ImmutableModel):
    """Privacy-safe structural projection of one strict repository execution input."""

    input_id: str = Field(min_length=1)
    admission: EnforcerRepositoryAdmissionFact | None = None
    base_workspace: PlanSnapshotEvidence
    runtime_root: PlanSnapshotEvidence
    host_environment_names: tuple[str, ...] = Field(default_factory=tuple)
    hostname_resolutions: tuple[EnforcerHostnameResolutionFact, ...] = Field(default_factory=tuple)
    host_toolchain_id: str = Field(min_length=1)
    host_tools: tuple[EnforcerToolFact, ...] = Field(default_factory=tuple)
    managed_inputs: tuple[PlanSnapshotEvidence, ...] = Field(default_factory=tuple)
    content_inputs: tuple[PlanContentObjectEvidence, ...] = Field(default_factory=tuple)
    import_applicability: tuple[PlanImportApplicabilityEvidence, ...] = Field(default_factory=tuple)


class EnforcerOperationFact(ImmutableModel):
    operation_id: str = Field(min_length=1)
    outcome: OperationOutcome


class EnforcerRepositoryFact(ImmutableModel):
    repository_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    operation: EnforcerOperationFact | None = None
    execution_input: EnforcerExecutionInputFact | None = None
    steps: tuple[EnforcerStepResultFact, ...] = Field(default_factory=tuple)


class EnforcerRunReceipt(ImmutableModel):
    receipt_format: Literal["oh-goon/enforcer-run/v19"] = "oh-goon/enforcer-run/v19"
    fleet_run_id: str = Field(min_length=1)
    lifecycle: EnforcerRunLifecycleFact
    outcome: FleetRunOutcome
    plan: EnforcerResolvedPlanFact
    execution_reference: ResolvedExecutionReference
    fleet_execution: EnforcerFleetExecutionFact
    repositories: tuple[EnforcerRepositoryFact, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_receipt_bindings(self) -> EnforcerRunReceipt:
        validate_plan_source_repository_count(
            self.execution_reference.source, len(self.repositories)
        )
        if (
            self.execution_reference.plan_id != self.plan.plan_id
            or self.execution_reference.plan_name != self.plan.name
        ):
            raise ValueError("Enforcer receipt execution reference must match the resolved Plan")
        if self.fleet_execution.placement.executor_capacity.fleet_run_id != self.fleet_run_id:
            raise ValueError("Enforcer receipt placement must belong to the same Fleet run")
        return self
