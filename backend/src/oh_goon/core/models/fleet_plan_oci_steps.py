from __future__ import annotations

from typing import Literal

from pydantic import Field, field_validator, model_validator

from oh_goon.core.models.base import ImmutableModel
from oh_goon.core.models.fleet_plan_oci import (
    AuthoringOciBuildPolicy,
    AuthoringOciRegistryEndpoint,
    OciArchiveOutput,
    OciBuildPolicy,
    OciRegistryEndpoint,
)
from oh_goon.core.models.fleet_plan_oci_publish import (
    AuthoringOciPublishTarget,
    OciPublishOutput,
    OciPublishPolicy,
    OciPublishTarget,
)
from oh_goon.core.models.fleet_plan_oci_registry import (
    AuthoringOciRegistryCredentials,
    OciRegistryCredentials,
)
from oh_goon.core.models.fleet_plan_step_contract import (
    PlanStepCache,
    PlanStepCapabilities,
    PlanStepWorkspace,
)
from oh_goon.core.models.fleet_plan_step_primitives import (
    PlanArtifactDeclaration,
    PlanPublishedOutput,
    StepCondition,
    StepFailurePolicy,
    StepLifecycle,
    StepRetryPolicy,
    _validate_success_codes,
)
from oh_goon.core.models.fleet_plan_values import PlanStepImports, ResolvedEnvironmentBinding


class OciBuildPlanStep(ImmutableModel):
    """Typed OCI archive build. Builder argv, network split, and registry policy are not caller-defined."""

    kind: Literal["oci_build"] = "oci_build"
    step_id: str = Field(min_length=1, max_length=80)
    lifecycle: StepLifecycle = StepLifecycle.NORMAL
    containerfile: str = Field(default="Dockerfile", min_length=1, max_length=240)
    context: str = Field(default=".", min_length=1, max_length=240)
    registries: tuple[AuthoringOciRegistryEndpoint, ...] = Field(
        default_factory=tuple, max_length=16
    )
    policy: AuthoringOciBuildPolicy = Field(default_factory=AuthoringOciBuildPolicy)
    output: OciArchiveOutput = Field(default_factory=OciArchiveOutput)
    timeout_seconds: float | None = Field(default=1800, gt=0, le=21600)
    workspace: PlanStepWorkspace = Field(default_factory=PlanStepWorkspace)
    cache: PlanStepCache = Field(default_factory=PlanStepCache)
    on_failure: StepFailurePolicy = StepFailurePolicy.STOP
    when: StepCondition | None = None
    retry: StepRetryPolicy = Field(default_factory=StepRetryPolicy)

    @model_validator(mode="after")
    def validate_registries(self) -> OciBuildPlanStep:
        keys = [(item.hostname, item.port) for item in self.registries]
        if len(keys) != len(set(keys)):
            raise ValueError("Fleet OCI registry endpoints must be unique")
        return self


class OciPublishPlanStep(ImmutableModel):
    """Content-addressed OCI registry publication from one explicit earlier workspace."""

    kind: Literal["oci_publish"] = "oci_publish"
    step_id: str = Field(min_length=1, max_length=80)
    lifecycle: StepLifecycle = StepLifecycle.NORMAL
    archive: str = Field(default="oci/image.oci.tar", min_length=1, max_length=240)
    target: AuthoringOciPublishTarget
    credentials: AuthoringOciRegistryCredentials
    policy: OciPublishPolicy = Field(default_factory=OciPublishPolicy)
    output: OciPublishOutput = Field(default_factory=OciPublishOutput)
    timeout_seconds: float | None = Field(default=900, gt=0, le=21600)
    workspace: PlanStepWorkspace
    cache: PlanStepCache = Field(default_factory=PlanStepCache)
    on_failure: StepFailurePolicy = StepFailurePolicy.STOP
    when: StepCondition | None = None
    retry: StepRetryPolicy = Field(default_factory=StepRetryPolicy)

    @model_validator(mode="after")
    def validate_publish_contract(self) -> OciPublishPlanStep:
        if self.workspace.source.kind != "step":
            raise ValueError(
                "Fleet OCI publish must consume one explicitly published earlier workspace"
            )
        if self.workspace.publish:
            raise ValueError(
                "Fleet OCI publish does not republish its credential-bearing workspace"
            )
        if self.retry.max_attempts != 1 or self.retry.known_transient_exit_codes:
            raise ValueError(
                "Fleet OCI publish forbids automatic step retries; digest publication is retried by rerunning explicit intent"
            )
        return self


class ResolvedOciBuildStep(ImmutableModel):
    """Canonical OCI build intent with derived process and artifact authority."""

    kind: Literal["oci_build"] = "oci_build"
    step_id: str = Field(min_length=1, max_length=80)
    lifecycle: StepLifecycle = StepLifecycle.NORMAL
    containerfile: str = Field(min_length=1, max_length=240)
    context: str = Field(min_length=1, max_length=240)
    registries: tuple[OciRegistryEndpoint, ...] = Field(default_factory=tuple, max_length=16)
    policy: OciBuildPolicy
    output: OciArchiveOutput
    timeout_seconds: float | None = Field(gt=0, le=21600)
    working_directory: str = "."
    imports: PlanStepImports = Field(default_factory=PlanStepImports)
    workspace: PlanStepWorkspace = Field(default_factory=PlanStepWorkspace)
    cache: PlanStepCache = Field(default_factory=PlanStepCache)
    capabilities: PlanStepCapabilities
    environment: tuple[ResolvedEnvironmentBinding, ...] = Field(
        default_factory=tuple, max_length=64
    )
    success_codes: tuple[int, ...] = (0,)
    on_failure: StepFailurePolicy = StepFailurePolicy.STOP
    when: StepCondition | None = None
    retry: StepRetryPolicy = Field(default_factory=StepRetryPolicy)
    artifacts: tuple[PlanArtifactDeclaration, ...] = Field(min_length=2, max_length=2)
    outputs: tuple[PlanPublishedOutput, ...] = Field(default_factory=tuple, max_length=0)

    _success_codes = field_validator("success_codes")(_validate_success_codes)

    @model_validator(mode="after")
    def validate_derived_contract(self) -> ResolvedOciBuildStep:
        expected = {self.output.archive, self.output.metadata}
        actual = {item.path for item in self.artifacts}
        if actual != expected:
            raise ValueError("Resolved OCI build artifacts must be exactly archive + metadata")
        return self


class ResolvedOciPublishStep(ImmutableModel):
    """Canonical content-addressed registry effect with secret names, never secret bytes."""

    kind: Literal["oci_publish"] = "oci_publish"
    step_id: str = Field(min_length=1, max_length=80)
    lifecycle: StepLifecycle = StepLifecycle.NORMAL
    archive: str = Field(min_length=1, max_length=240)
    target: OciPublishTarget
    credentials: OciRegistryCredentials
    policy: OciPublishPolicy
    output: OciPublishOutput
    timeout_seconds: float | None = Field(gt=0, le=21600)
    working_directory: str = "."
    imports: PlanStepImports = Field(default_factory=PlanStepImports)
    workspace: PlanStepWorkspace
    cache: PlanStepCache = Field(default_factory=PlanStepCache)
    capabilities: PlanStepCapabilities
    environment: tuple[ResolvedEnvironmentBinding, ...] = Field(default_factory=tuple, max_length=0)
    success_codes: tuple[int, ...] = (0,)
    on_failure: StepFailurePolicy = StepFailurePolicy.STOP
    when: StepCondition | None = None
    retry: StepRetryPolicy = Field(default_factory=StepRetryPolicy)
    artifacts: tuple[PlanArtifactDeclaration, ...] = Field(min_length=3, max_length=3)
    outputs: tuple[PlanPublishedOutput, ...] = Field(min_length=2, max_length=2)

    @model_validator(mode="after")
    def validate_derived_contract(self) -> ResolvedOciPublishStep:
        if self.workspace.source.kind != "step" or self.workspace.publish:
            raise ValueError("Resolved OCI publish requires one non-republished earlier workspace")
        if self.retry.max_attempts != 1 or self.retry.known_transient_exit_codes:
            raise ValueError("Resolved OCI publish forbids automatic retries")
        expected_artifacts = {self.output.metadata, self.output.digest, self.output.reference}
        if {item.path for item in self.artifacts} != expected_artifacts:
            raise ValueError("Resolved OCI publish artifacts must be metadata + digest + reference")
        expected_outputs = {
            ("OCI_DIGEST", self.output.digest),
            ("OCI_REFERENCE", self.output.reference),
        }
        if {(item.name, item.path) for item in self.outputs} != expected_outputs:
            raise ValueError(
                "Resolved OCI publish outputs must expose OCI_DIGEST and OCI_REFERENCE"
            )
        return self
