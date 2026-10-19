from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field

from oh_goon.core.models.base import ImmutableModel
from oh_goon.core.models.fleet_plan_release_evidence import KubernetesRolloutVerificationOutcome
from oh_goon.core.models.fleet_plan_requirements import RequirementCount


class FleetPlanAdmissionTrustReceipt(ImmutableModel):
    validation_id: str = Field(min_length=1)
    requirements_id: str = Field(min_length=1)
    requirement_count: RequirementCount


class FleetPlanRuntimeToolTrustReceipt(ImmutableModel):
    name: str = Field(min_length=1, max_length=240)
    origin: Literal["host"] = "host"
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    byte_count: int = Field(ge=0)
    authority_id: str = Field(min_length=1)


class FleetPlanRuntimeTrustReceipt(ImmutableModel):
    backend_id: str = Field(min_length=1, max_length=80)
    host_toolchain_id: str = Field(min_length=1)
    configuration_id: str = Field(min_length=1)
    kernel_boundary: Literal["shared_host_kernel", "application_kernel", "virtual_machine"]
    remote_execution: bool
    experimental: bool
    host_environment_names: tuple[str, ...] = Field(default_factory=tuple)
    host_tools: tuple[FleetPlanRuntimeToolTrustReceipt, ...] = Field(default_factory=tuple)
    hostname_resolution_count: int = Field(ge=0)


class FleetPlanCacheAttemptTrustReceipt(ImmutableModel):
    step_id: str = Field(min_length=1)
    attempt: int = Field(ge=1)
    cache_input_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    cache_output_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    retained_reference_id: str | None = None
    retained_owner_repository_id: str | None = None
    retained_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


class FleetPlanWorkspaceTrustReceipt(ImmutableModel):
    base_workspace_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    runtime_root_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    managed_input_count: int = Field(ge=0)
    content_input_count: int = Field(ge=0)
    attempts: tuple[FleetPlanCacheAttemptTrustReceipt, ...] = Field(default_factory=tuple)


class FleetPlanBoundaryTrustReceipt(ImmutableModel):
    step_id: str = Field(min_length=1)
    attempt: int = Field(ge=1)
    egress_mode: str = Field(min_length=1)
    egress_policy_id: str = Field(min_length=1)
    resource_policy_id: str | None = None
    repository_state_before: str = Field(min_length=1)
    repository_state_after: str = Field(min_length=1)


class FleetPlanTeardownStepTrustReceipt(ImmutableModel):
    step_id: str = Field(min_length=1)
    status: Literal["COMPLETED", "FAILED", "NOT_RECORDED"]
    attempt_roots_observed: int | None = Field(default=None, ge=0)
    reason_code: str | None = None


class FleetPlanTeardownTrustReceipt(ImmutableModel):
    steps: tuple[FleetPlanTeardownStepTrustReceipt, ...]
    completed: int = Field(ge=0)
    failed: int = Field(ge=0)
    missing: int = Field(ge=0)


class FleetPlanFinalTrustReceipt(ImmutableModel):
    state: Literal["cleanPass", "failed", "evidenceGap", "nonStrict"]
    completed_steps: int = Field(ge=0)
    failed_steps: int = Field(ge=0)
    skipped_steps: int = Field(ge=0)
    teardown_verified: int = Field(ge=0)
    teardown_failed: int = Field(ge=0)
    teardown_missing: int = Field(ge=0)


class FleetPlanOciReleaseTrustReceipt(ImmutableModel):
    kind: Literal["OCI_PUBLISH"] = "OCI_PUBLISH"
    release_id: str = Field(min_length=1)
    target: str = Field(min_length=1)
    manifest_digest: str = Field(min_length=1)


class FleetPlanKubernetesReleaseTrustReceipt(ImmutableModel):
    kind: Literal["KUBERNETES_SET_IMAGE"] = "KUBERNETES_SET_IMAGE"
    release_id: str = Field(min_length=1)
    outcome: Literal["COMMITTED", "ALREADY_DESIRED"]
    target: str = Field(min_length=1)
    desired_image: str = Field(min_length=1)
    before_resource_version: str = Field(min_length=1)
    before_generation: int = Field(ge=0)
    after_resource_version: str = Field(min_length=1)
    after_generation: int = Field(ge=0)


FleetPlanReleaseEffectTrustReceipt = Annotated[
    FleetPlanOciReleaseTrustReceipt | FleetPlanKubernetesReleaseTrustReceipt,
    Field(discriminator="kind"),
]


class FleetPlanReleaseStepTrustReceipt(ImmutableModel):
    step_id: str = Field(min_length=1)
    step_kind: str = Field(min_length=1)
    execution_status: str | None = None
    gate_state: Literal["RELEASED", "BLOCKED", "EVIDENCE_GAP"]
    release: FleetPlanReleaseEffectTrustReceipt | None = None


class FleetPlanRolloutVerificationTrustReceipt(ImmutableModel):
    verification_id: str = Field(min_length=1)
    outcome: KubernetesRolloutVerificationOutcome
    target: str = Field(min_length=1)
    expected_generation: int = Field(ge=1)
    expected_image: str = Field(min_length=1)
    observations: int = Field(ge=1)
    final_resource_version: str = Field(min_length=1)
    final_generation: int = Field(ge=1)
    final_image: str = Field(min_length=1)
    observed_generation: int | None = Field(default=None, ge=0)
    desired_replicas: int = Field(ge=0)
    replicas: int = Field(ge=0)
    updated_replicas: int = Field(ge=0)
    available_replicas: int = Field(ge=0)
    unavailable_replicas: int = Field(ge=0)
    progressing_status: Literal["True", "False", "Unknown"] | None = None
    progressing_reason: str | None = None
    timeout_seconds: float = Field(gt=0)
    poll_interval_seconds: float = Field(gt=0)
    request_timeout_seconds: int = Field(ge=1)


class FleetPlanReadinessStepTrustReceipt(ImmutableModel):
    step_id: str = Field(min_length=1)
    execution_status: str | None = None
    state: Literal["ready", "failed", "blocked", "evidenceGap"]
    verification: FleetPlanRolloutVerificationTrustReceipt | None = None


class FleetPlanReadinessTrustReceipt(ImmutableModel):
    state: Literal["notRequested", "ready", "failed", "blocked", "partial", "evidenceGap"]
    requested: int = Field(ge=0)
    ready: int = Field(ge=0)
    failed: int = Field(ge=0)
    blocked: int = Field(ge=0)
    evidence_gaps: int = Field(ge=0)
    steps: tuple[FleetPlanReadinessStepTrustReceipt, ...] = Field(default_factory=tuple)


class FleetPlanReleaseTrustReceipt(ImmutableModel):
    state: Literal["noRelease", "released", "blocked", "partialRelease", "evidenceGap"]
    released: int = Field(ge=0)
    blocked: int = Field(ge=0)
    evidence_gaps: int = Field(ge=0)
    external_effects: int = Field(ge=0)
    readiness: FleetPlanReadinessTrustReceipt
    steps: tuple[FleetPlanReleaseStepTrustReceipt, ...] = Field(default_factory=tuple)


class FleetPlanArtifactTrustReceipt(ImmutableModel):
    step_id: str = Field(min_length=1)
    path: str
    present: bool
    byte_count: int | None = Field(default=None, ge=0)
    sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


class FleetPlanOutputTrustReceipt(ImmutableModel):
    step_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    byte_count: int = Field(ge=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class FleetPlanEgressTrustReceipt(ImmutableModel):
    artifacts: tuple[FleetPlanArtifactTrustReceipt, ...] = Field(default_factory=tuple)
    outputs: tuple[FleetPlanOutputTrustReceipt, ...] = Field(default_factory=tuple)


class FleetPlanTrustReceipt(ImmutableModel):
    plan_id: str = Field(min_length=1)
    input_id: str | None = None
    repository_id: str | None = None
    admission: FleetPlanAdmissionTrustReceipt | None = None
    runtime: FleetPlanRuntimeTrustReceipt | None = None
    workspace: FleetPlanWorkspaceTrustReceipt | None = None
    boundaries: tuple[FleetPlanBoundaryTrustReceipt, ...] = Field(default_factory=tuple)
    release: FleetPlanReleaseTrustReceipt
    egress: FleetPlanEgressTrustReceipt
    teardown: FleetPlanTeardownTrustReceipt
    final: FleetPlanFinalTrustReceipt


class RepositoryFleetPlanTrustReceipt(ImmutableModel):
    repository_id: str = Field(min_length=1)
    receipt: FleetPlanTrustReceipt
