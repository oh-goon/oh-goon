from __future__ import annotations

from typing import Literal

from pydantic import Field, field_validator, model_validator

from oh_goon.core.models.base import ImmutableModel
from oh_goon.core.models.fleet_plan_oci_registry import (
    AuthoringOciRegistryCredentials,
    OciRegistryCredentials,
)
from oh_goon.core.models.fleet_plan_oci_tag import (
    AuthoringOciTagTarget,
    OciTagAssertOutput,
    OciTagAssertPolicy,
    OciTagTarget,
    validate_oci_expected_digest,
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


class OciTagAssertPlanStep(ImmutableModel):
    """Read-only assertion that one mutable tag currently resolves to an expected digest."""

    kind: Literal["oci_tag_assert"] = "oci_tag_assert"
    step_id: str = Field(min_length=1, max_length=80)
    lifecycle: StepLifecycle = StepLifecycle.NORMAL
    target: AuthoringOciTagTarget
    expected_digest: str = Field(min_length=1, max_length=256)
    credentials: AuthoringOciRegistryCredentials
    policy: OciTagAssertPolicy = Field(default_factory=OciTagAssertPolicy)
    output: OciTagAssertOutput = Field(default_factory=OciTagAssertOutput)
    timeout_seconds: float | None = Field(default=120, gt=0, le=1800)
    workspace: PlanStepWorkspace = Field(default_factory=PlanStepWorkspace)
    cache: PlanStepCache = Field(default_factory=PlanStepCache)
    on_failure: StepFailurePolicy = StepFailurePolicy.STOP
    when: StepCondition | None = None
    retry: StepRetryPolicy = Field(default_factory=StepRetryPolicy)

    @model_validator(mode="after")
    def validate_assert_contract(self) -> OciTagAssertPlanStep:
        if self.workspace.source.kind != "repository" or self.workspace.publish:
            raise ValueError(
                "Fleet OCI tag assertion uses the repository workspace without publishing it"
            )
        if self.cache.source.kind != "empty" or self.cache.publish or self.cache.retain:
            raise ValueError(
                "Fleet OCI tag assertion does not consume, publish, or retain a step cache"
            )
        return self


class ResolvedOciTagAssertStep(ImmutableModel):
    """Canonical read-only registry observation with expected-digest precondition."""

    kind: Literal["oci_tag_assert"] = "oci_tag_assert"
    step_id: str = Field(min_length=1, max_length=80)
    lifecycle: StepLifecycle = StepLifecycle.NORMAL
    target: OciTagTarget
    expected_digest: str = Field(min_length=1, max_length=256)
    credentials: OciRegistryCredentials
    policy: OciTagAssertPolicy
    output: OciTagAssertOutput
    timeout_seconds: float | None = Field(gt=0, le=1800)
    working_directory: str = "."
    imports: PlanStepImports = Field(default_factory=PlanStepImports)
    workspace: PlanStepWorkspace = Field(default_factory=PlanStepWorkspace)
    cache: PlanStepCache = Field(default_factory=PlanStepCache)
    capabilities: PlanStepCapabilities
    environment: tuple[ResolvedEnvironmentBinding, ...] = Field(default_factory=tuple, max_length=0)
    success_codes: tuple[int, ...] = (0,)
    on_failure: StepFailurePolicy = StepFailurePolicy.STOP
    when: StepCondition | None = None
    retry: StepRetryPolicy = Field(default_factory=StepRetryPolicy)
    artifacts: tuple[PlanArtifactDeclaration, ...] = Field(min_length=3, max_length=3)
    outputs: tuple[PlanPublishedOutput, ...] = Field(min_length=2, max_length=2)

    _success_codes = field_validator("success_codes")(_validate_success_codes)

    @model_validator(mode="after")
    def validate_derived_contract(self) -> ResolvedOciTagAssertStep:
        validate_oci_expected_digest(self.expected_digest)
        if self.workspace.source.kind != "repository" or self.workspace.publish:
            raise ValueError(
                "Resolved OCI tag assertion uses one non-published repository workspace"
            )
        if self.cache.source.kind != "empty" or self.cache.publish or self.cache.retain:
            raise ValueError("Resolved OCI tag assertion has no cache authority")
        expected_artifacts = {
            self.output.metadata,
            self.output.digest,
            self.output.resolved_reference,
        }
        if {item.path for item in self.artifacts} != expected_artifacts:
            raise ValueError(
                "Resolved OCI tag assertion artifacts must be metadata + digest + resolved reference"
            )
        expected_outputs = {
            ("OCI_TAG_DIGEST", self.output.digest),
            ("OCI_TAG_RESOLVED_REFERENCE", self.output.resolved_reference),
        }
        if {(item.name, item.path) for item in self.outputs} != expected_outputs:
            raise ValueError(
                "Resolved OCI tag assertion outputs must expose OCI_TAG_DIGEST and OCI_TAG_RESOLVED_REFERENCE"
            )
        return self
