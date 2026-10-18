from __future__ import annotations

from enum import StrEnum
from ipaddress import IPv4Address
from pathlib import Path
from typing import Annotated, Literal

from pydantic import AwareDatetime, Field, computed_field, field_validator, model_validator

from oh_goon.core.fleet_plan_evidence_identity import (
    egress_payload,
    execution_input_payload,
    repository_admission_payload,
    resource_policy_payload,
)
from oh_goon.core.product_identity import stable_id
from oh_goon.core.models.base import ImmutableModel
from oh_goon.core.models.execution_authority_diagnostics import (
    PlanExecutionAuthorityDiagnosticEvidence,
)
from oh_goon.core.models.fleet_observation import FleetObservationEvidence
from oh_goon.core.models.fleet_plan_requirements import RequirementCount
from oh_goon.core.models.fleet_plan_release_evidence import (
    KubernetesRolloutVerificationEvidence,
    KubernetesRolloutVerificationOutcome,
    PlanReleaseEffectEvidence,
)
from oh_goon.core.models.fleet_plan_retry import PlanRetryDecisionEvidence
from oh_goon.core.models.fleet_plan_tool_authority import PlanHostToolAuthorityEvidence
from oh_goon.core.models.shard_execution import CanonicalShardExecutionEvidence


class PlanOutputEvidence(ImmutableModel):
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    byte_count: int = Field(ge=0)
    line_count: int = Field(ge=0)


class PublishedPlanOutputEvidence(ImmutableModel):
    name: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_-]{0,63}$")
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    byte_count: int = Field(ge=0)
    producer_execution_identity: str | None = Field(default=None, pattern=r"^attempt-output-[0-9a-f]{64}$")


class PlanContentObjectEvidence(ImmutableModel):
    """Portable content identity derived from one canonical retained-content fact."""

    kind: Literal["tree", "file"]
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    byte_count: int = Field(ge=0)
    entry_count: int = Field(ge=0)

    @computed_field
    @property
    def object_id(self) -> str:
        return f"{self.kind}-sha256-{self.sha256}"


class PlanRetainedContentEvidence(PlanContentObjectEvidence):
    """Immutable content plus repository-scoped durable retention authority."""

    reference_id: str = Field(pattern=r"^ref-[0-9a-f]{32}$")
    owner_repository_id: str = Field(pattern=r"^repo-[0-9a-f]{20}$")


class PresentPlanArtifact(ImmutableModel):
    status: Literal["PRESENT"] = "PRESENT"
    path: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    byte_count: int = Field(ge=0)
    retained_object: PlanRetainedContentEvidence | None = None


class MissingPlanArtifact(ImmutableModel):
    status: Literal["MISSING"] = "MISSING"
    path: str


PlanArtifactEvidence = Annotated[
    PresentPlanArtifact | MissingPlanArtifact,
    Field(discriminator="status"),
]


class PlanToolEvidence(ImmutableModel):
    """Observed executable bytes for one physical attempt; paths are not persisted."""

    name: str = Field(min_length=1, max_length=240)
    origin: Literal["workspace", "host", "managed"]
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    byte_count: int = Field(ge=0)


class PlanIsolationBackendEvidence(ImmutableModel):
    """Physical isolation implementation selected after matching admitted guarantees."""

    backend_id: str = Field(min_length=1, max_length=80)
    configuration_id: str = Field(min_length=1)
    kernel_boundary: Literal["shared_host_kernel", "application_kernel", "virtual_machine"]
    remote_execution: bool
    experimental: bool


class PlanSnapshotEvidence(ImmutableModel):
    """Content identity for one execution input snapshot; no local path is persisted."""

    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    byte_count: int = Field(ge=0)
    entry_count: int = Field(ge=0)


class RequirementsRepositoryAdmissionEvidence(ImmutableModel):
    """Proof that the managed Team satisfied the exact resolved ``requirements`` list."""

    kind: Literal["requirements"] = "requirements"
    requirements_id: str = Field(min_length=1)
    requirement_count: RequirementCount

    @computed_field
    @property
    def validation_id(self) -> str:
        return stable_id("cicd-repository-admission", repository_admission_payload(self))


CicdRepositoryAdmissionEvidence = RequirementsRepositoryAdmissionEvidence


class PlanHostnameResolutionEvidence(ImmutableModel):
    """Exact IPv4 answers frozen for one hostname before strict execution starts."""

    hostname: str = Field(min_length=3, max_length=253)
    addresses: tuple[str, ...] = Field(min_length=1)

    @field_validator("addresses")
    @classmethod
    def validate_addresses(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        canonical = tuple(str(IPv4Address(address)) for address in value)
        ordered = tuple(sorted(set(canonical), key=lambda item: int(IPv4Address(item))))
        if value != ordered:
            raise ValueError(
                "Fleet hostname resolution evidence must be unique canonical sorted IPv4"
            )
        return value


class PlanImportApplicabilityEvidence(ImmutableModel):
    """Repository-snapshot decision for one canonical resolved template import."""

    import_id: str = Field(min_length=1, max_length=160)
    status: Literal["APPLICABLE", "NOT_APPLICABLE"]
    reason: str = Field(default="", max_length=500)

    @model_validator(mode="after")
    def validate_reason(self) -> PlanImportApplicabilityEvidence:
        if self.status == "APPLICABLE" and self.reason:
            raise ValueError("Applicable template imports cannot carry a skip reason")
        if self.status == "NOT_APPLICABLE" and not self.reason:
            raise ValueError("Non-applicable template imports require an explicit reason")
        return self


class PlanExecutionInputEvidence(ImmutableModel):
    """Frozen non-secret inputs that gave one strict CI/CD run its physical meaning."""

    repository_id: str = Field(pattern=r"^repo-[0-9a-f]{20}$")
    source_authority_id: str = Field(pattern=r"^repository-source-authority-[0-9a-f]{64}$")
    base_workspace: PlanSnapshotEvidence
    repository_admission: CicdRepositoryAdmissionEvidence | None = None
    import_applicability: tuple[PlanImportApplicabilityEvidence, ...] = Field(default_factory=tuple)
    runtime_root: PlanSnapshotEvidence
    isolation_backend: PlanIsolationBackendEvidence
    host_environment_names: tuple[str, ...] = Field(default_factory=tuple)
    host_tools: tuple[PlanHostToolAuthorityEvidence, ...] = Field(default_factory=tuple)
    managed_inputs: tuple[PlanSnapshotEvidence, ...] = Field(default_factory=tuple)
    content_inputs: tuple[PlanContentObjectEvidence, ...] = Field(default_factory=tuple)
    hostname_resolutions: tuple[PlanHostnameResolutionEvidence, ...] = Field(default_factory=tuple)

    @field_validator("host_tools")
    @classmethod
    def validate_host_tools(
        cls, value: tuple[PlanHostToolAuthorityEvidence, ...]
    ) -> tuple[PlanHostToolAuthorityEvidence, ...]:
        names = tuple(item.name for item in value)
        if names != tuple(sorted(set(names))):
            raise ValueError("Fleet host-tool authority must be unique and sorted by logical name")
        return value

    @computed_field
    @property
    def host_toolchain_id(self) -> str:
        return stable_id(
            "plan-host-toolchain",
            [
                item.model_dump(mode="json", exclude_computed_fields=True)
                for item in self.host_tools
            ],
        )

    @computed_field
    @property
    def input_id(self) -> str:
        return stable_id("plan-execution-input", execution_input_payload(self))


class PlanEgressRuleEvidence(ImmutableModel):
    cidr: str
    protocol: Literal["tcp", "udp"]
    ports: tuple[int, ...]


class PlanEgressEvidence(ImmutableModel):
    """Durable proof of the network authority admitted for one physical attempt."""

    mode: Literal["none", "cidr_allowlist", "hostname_allowlist", "host_unrestricted"]
    rules: tuple[PlanEgressRuleEvidence, ...] = Field(default_factory=tuple)

    @computed_field
    @property
    def policy_id(self) -> str:
        return stable_id("plan-egress", egress_payload(self))

    @model_validator(mode="after")
    def validate_rules(self) -> PlanEgressEvidence:
        if self.mode not in {"cidr_allowlist", "hostname_allowlist"} and self.rules:
            raise ValueError(
                "Only destination-restricted egress evidence may contain destination rules"
            )
        return self


class PlanResourceEvidence(ImmutableModel):
    """Durable proof of the resource envelope enforced for one physical attempt."""

    cpu_millis: int | None = Field(default=None, ge=100)
    memory_bytes: int | None = Field(default=None, ge=64 * 1024 * 1024)
    max_processes: int | None = Field(default=None, ge=16)
    cpu_usage_usec: int | None = Field(default=None, ge=0)
    memory_peak_bytes: int | None = Field(default=None, ge=0)
    pids_peak: int | None = Field(default=None, ge=0)
    oom_kill_count: int | None = Field(default=None, ge=0)

    @computed_field
    @property
    def policy_id(self) -> str:
        return stable_id("plan-resource-policy", resource_policy_payload(self))


class CompletedPlanStepTeardownEvidence(ImmutableModel):
    """Verified removal of one step's attempt-private filesystem roots."""

    status: Literal["COMPLETED"] = "COMPLETED"
    attempt_roots_observed: int = Field(ge=0)
    step_attempts_root_absent: Literal[True] = True


class FailedPlanStepTeardownEvidence(ImmutableModel):
    """Known teardown failure; reason codes intentionally contain no host paths."""

    status: Literal["FAILED"] = "FAILED"
    attempt_roots_observed: int = Field(ge=0)
    step_attempts_root_absent: Literal[False] = False
    reason_code: Literal["INSPECTION_FAILED", "REMOVAL_FAILED", "ROOT_REMAINS"]


type PlanStepTeardownEvidence = Annotated[
    CompletedPlanStepTeardownEvidence | FailedPlanStepTeardownEvidence,
    Field(discriminator="status"),
]


class PlanExecutionRecoveryEventEvidence(ImmutableModel):
    """Normalized non-authoritative recovery provenance for one execution attempt."""

    kind: Literal["process_group_cleanup", "timeout_refinement", "environment_retry", "sqlite_reproof"]
    action: str = Field(min_length=1)
    reason_code: str = Field(min_length=1)
    outcome: Literal["requested", "succeeded", "failed", "blocked"]
    work_identity: str | None = None
    parent_identity: str | None = None
    child_identities: tuple[str, ...] = Field(default_factory=tuple)


class PlanExecutionSupervisorEvidence(ImmutableModel):
    """Bounded lifecycle facts from the Oh-Goon command supervisor."""

    leader_exited_before_group_quiescence: bool = False
    process_group_cleanup_requested: bool = False
    cleanup_reason: Literal["none", "timeout", "cancel", "output_cleanup", "output_limit"] = "none"
    recovery_events: tuple[PlanExecutionRecoveryEventEvidence, ...] = Field(default_factory=tuple)

    @model_validator(mode="after")
    def validate_cleanup_reason(self) -> PlanExecutionSupervisorEvidence:
        if self.process_group_cleanup_requested == (self.cleanup_reason == "none"):
            raise ValueError("Supervisor cleanup reason must match cleanup request state")
        return self


class PlanExecutionAuthorityEconomicsEvidence(ImmutableModel):
    """Diagnostic-only reuse/recompute evidence for one strict launch."""

    scope: Literal["runtime-launch", "materialization"]
    candidate_units: int = Field(ge=0)
    reused_units: int = Field(ge=0)
    recomputed_units: int = Field(ge=0)
    reused_bytes: int = Field(ge=0)
    recomputed_bytes: int = Field(ge=0)
    terminal_proof_bytes: int = Field(default=0, ge=0)
    reuse_provenance: tuple[str, ...] = Field(default_factory=tuple)
    reuse_identities: tuple[str, ...] = Field(default_factory=tuple)
    recompute_reasons: tuple[str, ...] = Field(default_factory=tuple)
    receipt_identity: str = Field(min_length=71, max_length=71)

    @model_validator(mode="after")
    def validate_unit_partition(self) -> PlanExecutionAuthorityEconomicsEvidence:
        if self.reused_units + self.recomputed_units != self.candidate_units:
            raise ValueError("Authority economics candidate units must partition reuse/recompute")
        return self


class PlanStepAttemptEvidence(ImmutableModel):
    attempt: int = Field(ge=1, le=5)
    argv: tuple[str, ...] = Field(default_factory=tuple)
    returncode: int
    stdout_evidence: PlanOutputEvidence
    stderr_evidence: PlanOutputEvidence
    duration_ms: int = Field(default=0, ge=0)
    repository_state_before: str = Field(min_length=1)
    repository_state_after: str = Field(min_length=1)
    worktree_files_after: tuple[str, ...] = Field(default_factory=tuple)
    tool: PlanToolEvidence | None = None
    cache_input: PlanSnapshotEvidence | None = None
    cache_output: PlanSnapshotEvidence | None = None
    retained_cache: PlanRetainedContentEvidence | None = None
    egress: PlanEgressEvidence
    resources: PlanResourceEvidence | None = None
    retry: PlanRetryDecisionEvidence | None = None
    release_effect: PlanReleaseEffectEvidence | None = None
    release_verification: KubernetesRolloutVerificationEvidence | None = None
    supervisor: PlanExecutionSupervisorEvidence | None = None
    execution_authority_economics: tuple[PlanExecutionAuthorityEconomicsEvidence, ...] = Field(
        default_factory=tuple
    )
    execution_authority: PlanExecutionAuthorityDiagnosticEvidence | None = None


class _PlanStepExecutionBase(ImmutableModel):
    step_id: str
    kind: str
    started_at: AwareDatetime | None = None
    finished_at: AwareDatetime | None = None
    argv: tuple[str, ...] = Field(default_factory=tuple)
    stdout_evidence: PlanOutputEvidence
    stderr_evidence: PlanOutputEvidence
    duration_ms: int = Field(default=0, ge=0)
    repository_state_before: str = Field(min_length=1)
    repository_state_after: str = Field(min_length=1)
    worktree_files_after: tuple[str, ...] = Field(default_factory=tuple)
    attempts: tuple[PlanStepAttemptEvidence, ...] = Field(default_factory=tuple)
    artifacts: tuple[PlanArtifactEvidence, ...] = Field(default_factory=tuple)
    outputs: tuple[PublishedPlanOutputEvidence, ...] = Field(default_factory=tuple)
    teardown: PlanStepTeardownEvidence | None = None
    observations: tuple[FleetObservationEvidence, ...] = Field(default_factory=tuple, max_length=8)

    @model_validator(mode="after")
    def validate_timing(self) -> _PlanStepExecutionBase:
        if (self.started_at is None) != (self.finished_at is None):
            raise ValueError("Fleet plan step timing requires both start and finish")
        if self.started_at is not None and self.finished_at < self.started_at:
            raise ValueError("Fleet plan step finished_at cannot precede started_at")
        return self


class CompletedPlanStepExecution(_PlanStepExecutionBase):
    status: Literal["COMPLETED"] = "COMPLETED"
    returncode: int = 0


class SkippedPlanStepExecution(_PlanStepExecutionBase):
    status: Literal["SKIPPED"] = "SKIPPED"
    reason: str = Field(min_length=1, max_length=500)


class FailedPlanStepExecution(_PlanStepExecutionBase):
    status: Literal["FAILED"] = "FAILED"
    returncode: int

    @field_validator("returncode")
    @classmethod
    def validate_returncode(cls, value: int) -> int:
        if value == 0:
            raise ValueError("Failed Fleet plan step evidence requires non-zero returncode")
        return value


type PlanStepExecution = Annotated[
    CompletedPlanStepExecution | SkippedPlanStepExecution | FailedPlanStepExecution,
    Field(discriminator="status"),
]


class FleetPlanResult(ImmutableModel):
    plan_id: str
    steps: tuple[PlanStepExecution, ...]
    execution_inputs: PlanExecutionInputEvidence | None = None
    shard_execution: CanonicalShardExecutionEvidence | None = None

    @property
    def successful(self) -> bool:
        return not any(
            isinstance(step, FailedPlanStepExecution)
            or isinstance(step.teardown, FailedPlanStepTeardownEvidence)
            for step in self.steps
        )

    @model_validator(mode="after")
    def validate_release_effect_evidence(self) -> FleetPlanResult:
        expected_kind = {
            "oci_publish": "OCI_PUBLISH",
            "kubernetes_set_image": "KUBERNETES_SET_IMAGE",
        }
        for step in self.steps:
            effects = tuple(
                attempt.release_effect
                for attempt in step.attempts
                if attempt.release_effect is not None
            )
            required = expected_kind.get(step.kind)
            if required is None and effects:
                raise ValueError(
                    f"Fleet plan step {step.step_id} cannot carry release-effect evidence"
                )
            if any(effect.kind != required for effect in effects):
                raise ValueError(
                    f"Fleet plan step {step.step_id} release-effect kind does not match {step.kind}"
                )
            if len(effects) > 1:
                raise ValueError(
                    f"Fleet plan step {step.step_id} cannot claim multiple release effects"
                )
            if (
                isinstance(step, CompletedPlanStepExecution)
                and required is not None
                and not effects
            ):
                raise ValueError(
                    f"Completed Fleet release step {step.step_id} requires canonical release evidence"
                )
            if isinstance(step, SkippedPlanStepExecution) and effects:
                raise ValueError(
                    f"Skipped Fleet release step {step.step_id} cannot claim an external release"
                )
        return self

    @model_validator(mode="after")
    def validate_release_verification_evidence(self) -> FleetPlanResult:
        for step in self.steps:
            verifications = tuple(
                attempt.release_verification
                for attempt in step.attempts
                if attempt.release_verification is not None
            )
            if step.kind != "kubernetes_rollout_verify" and verifications:
                raise ValueError(
                    f"Fleet plan step {step.step_id} cannot carry rollout-verification evidence"
                )
            if isinstance(step, SkippedPlanStepExecution) and verifications:
                raise ValueError(
                    f"Skipped Fleet rollout step {step.step_id} cannot claim rollout verification"
                )
            if step.kind != "kubernetes_rollout_verify":
                continue
            if isinstance(step, CompletedPlanStepExecution):
                if (
                    not verifications
                    or verifications[-1].outcome is not KubernetesRolloutVerificationOutcome.READY
                ):
                    raise ValueError(
                        f"Completed Fleet rollout step {step.step_id} requires final READY verification evidence"
                    )
            if isinstance(step, FailedPlanStepExecution) and any(
                item.outcome is KubernetesRolloutVerificationOutcome.READY for item in verifications
            ):
                raise ValueError(
                    f"Failed Fleet rollout step {step.step_id} cannot contain READY verification evidence"
                )
        return self

    @model_validator(mode="after")
    def validate_host_tool_authority(self) -> FleetPlanResult:
        if self.execution_inputs is None:
            return self
        authorities = {item.name: item for item in self.execution_inputs.host_tools}
        for step in self.steps:
            for attempt in step.attempts:
                tool = attempt.tool
                if tool is None or tool.origin != "host":
                    continue
                authority = authorities.get(tool.name)
                if authority is None:
                    raise ValueError(
                        f"Fleet plan step {step.step_id} used host tool {tool.name!r} "
                        "outside the frozen run tool authority"
                    )
                if (tool.sha256, tool.byte_count) != (authority.sha256, authority.byte_count):
                    raise ValueError(
                        f"Fleet plan step {step.step_id} host tool {tool.name!r} does not match "
                        "the frozen run tool authority"
                    )
        return self

    @model_validator(mode="after")
    def validate_retry_evidence(self) -> FleetPlanResult:
        from oh_goon.core.fleet_plan_retry import validate_retry_attempt_sequence

        validate_retry_attempt_sequence(self)
        return self

    @model_validator(mode="after")
    def validate_teardown_evidence(self) -> FleetPlanResult:
        strict = self.execution_inputs is not None
        missing = [step.step_id for step in self.steps if step.teardown is None]
        unexpected = [step.step_id for step in self.steps if step.teardown is not None]
        if strict and missing:
            raise ValueError(
                "Strict Fleet CI/CD result requires teardown evidence for every step: "
                + ", ".join(missing)
            )
        if not strict and unexpected:
            raise ValueError(
                "Non-strict Fleet plan result cannot claim strict attempt teardown evidence: "
                + ", ".join(unexpected)
            )
        return self


class PlanPreviewDisposition(StrEnum):
    READY = "READY"
    SKIPPED = "SKIPPED"
    RUNTIME_DEPENDENT = "RUNTIME_DEPENDENT"


class PlanStepPreview(ImmutableModel):
    step_id: str
    kind: str
    disposition: PlanPreviewDisposition
    reason: str = Field(default="", max_length=500)
    timeout_seconds: float | None = Field(default=None, ge=0)
    workspace_source: str = Field(default="repository", min_length=1, max_length=160)
    workspace_publish: bool = False
    cache_source: str = Field(default="empty", min_length=1, max_length=200)
    cache_publish: bool = False
    cache_retain: bool = False
    egress_mode: str = Field(default="none", min_length=1, max_length=80)


class PlanImportPreview(ImmutableModel):
    import_id: str
    parent_import_id: str | None = None
    kind: Literal["goon", "plan"]
    name: str
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    step_ids: tuple[str, ...] = Field(default_factory=tuple)
    requirement_count: RequirementCount = 0
    applicability: Literal["APPLICABLE", "NOT_APPLICABLE"]
    reason: str = Field(default="", max_length=500)


class FleetPlanPreview(ImmutableModel):
    plan_id: str
    repository: Path
    plan_name: str = ""
    execution_purpose: str = ""
    repository_execution_mode: str = ""
    repository_max_parallel: int | None = Field(default=None, ge=2, le=32)
    imports: tuple[PlanImportPreview, ...] = Field(default_factory=tuple)
    steps: tuple[PlanStepPreview, ...]
