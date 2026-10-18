from __future__ import annotations

import re
from typing import Literal

from pydantic import Field, field_validator, model_validator

from oh_goon.core.fleet_plan_output_refs import step_output_reference
from oh_goon.core.models.base import ImmutableModel
from oh_goon.core.models.fleet_plan_kubernetes import (
    AuthoringKubernetesApiCredentials,
    AuthoringKubernetesDeploymentTarget,
    KubernetesApiCredentials,
    KubernetesDeploymentTarget,
    KubernetesImagePrecondition,
    KubernetesRolloutVerifyPolicy,
    KubernetesSetImageOutput,
    KubernetesSetImagePolicy,
    ObserveCurrentKubernetesImage,
    is_digest_image_or_output_reference,
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


_INPUT_REFERENCE = re.compile(r"\$\{\{\s*inputs\.[A-Za-z_][A-Za-z0-9_-]*\s*\}\}")

def _authoring_output_reference(value: str) -> bool:
    return step_output_reference(value) is not None or _INPUT_REFERENCE.fullmatch(value) is not None


class KubernetesSetImagePlanStep(ImmutableModel):
    """Atomically change one Deployment container image to one immutable digest reference."""

    kind: Literal["kubernetes_set_image"] = "kubernetes_set_image"
    step_id: str = Field(min_length=1, max_length=80)
    lifecycle: StepLifecycle = StepLifecycle.NORMAL
    target: AuthoringKubernetesDeploymentTarget
    image: str = Field(min_length=1, max_length=512)
    precondition: KubernetesImagePrecondition = Field(default_factory=ObserveCurrentKubernetesImage)
    credentials: AuthoringKubernetesApiCredentials
    policy: KubernetesSetImagePolicy = Field(default_factory=KubernetesSetImagePolicy)
    output: KubernetesSetImageOutput = Field(default_factory=KubernetesSetImageOutput)
    timeout_seconds: float | None = Field(default=120, gt=0, le=1800)
    workspace: PlanStepWorkspace = Field(default_factory=PlanStepWorkspace)
    cache: PlanStepCache = Field(default_factory=PlanStepCache)
    on_failure: StepFailurePolicy = StepFailurePolicy.STOP
    when: StepCondition | None = None
    retry: StepRetryPolicy = Field(default_factory=StepRetryPolicy)

    @model_validator(mode="after")
    def validate_contract(self) -> KubernetesSetImagePlanStep:
        if self.workspace.source.kind != "repository" or self.workspace.publish:
            raise ValueError(
                "Fleet Kubernetes set-image uses one non-published repository workspace"
            )
        if self.cache.source.kind != "empty" or self.cache.publish or self.cache.retain:
            raise ValueError("Fleet Kubernetes set-image has no cache authority")
        if "${{" not in self.image and not is_digest_image_or_output_reference(self.image):
            raise ValueError(
                "Fleet Kubernetes desired image must be digest-qualified, one exact earlier step output, "
                "or one compile-time template value"
            )
        return self


class ResolvedKubernetesSetImageStep(ImmutableModel):
    kind: Literal["kubernetes_set_image"] = "kubernetes_set_image"
    step_id: str = Field(min_length=1, max_length=80)
    lifecycle: StepLifecycle = StepLifecycle.NORMAL
    target: KubernetesDeploymentTarget
    image: str = Field(min_length=1, max_length=512)
    precondition: KubernetesImagePrecondition
    credentials: KubernetesApiCredentials
    policy: KubernetesSetImagePolicy
    output: KubernetesSetImageOutput
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
    artifacts: tuple[PlanArtifactDeclaration, ...] = Field(min_length=4, max_length=4)
    outputs: tuple[PlanPublishedOutput, ...] = Field(min_length=3, max_length=3)

    _success_codes = field_validator("success_codes")(_validate_success_codes)

    @model_validator(mode="after")
    def validate_derived_contract(self) -> ResolvedKubernetesSetImageStep:
        if not is_digest_image_or_output_reference(self.image):
            raise ValueError(
                "Resolved Fleet Kubernetes image must be digest-qualified or one exact earlier step output"
            )
        if self.workspace.source.kind != "repository" or self.workspace.publish:
            raise ValueError("Resolved Fleet Kubernetes set-image uses one repository workspace")
        if self.cache.source.kind != "empty" or self.cache.publish or self.cache.retain:
            raise ValueError("Resolved Fleet Kubernetes set-image has no cache authority")
        expected_artifacts = {
            self.output.metadata,
            self.output.resource_version,
            self.output.generation,
            self.output.image,
        }
        if {item.path for item in self.artifacts} != expected_artifacts:
            raise ValueError(
                "Resolved Fleet Kubernetes set-image artifacts do not match output contract"
            )
        expected_outputs = {
            ("KUBERNETES_RESOURCE_VERSION", self.output.resource_version),
            ("KUBERNETES_GENERATION", self.output.generation),
            ("KUBERNETES_IMAGE", self.output.image),
        }
        if {(item.name, item.path) for item in self.outputs} != expected_outputs:
            raise ValueError(
                "Resolved Fleet Kubernetes set-image outputs do not match output contract"
            )
        return self


class KubernetesRolloutVerifyPlanStep(ImmutableModel):
    """Observe one exact Deployment generation until it is ready or a declared bound is reached."""

    kind: Literal["kubernetes_rollout_verify"] = "kubernetes_rollout_verify"
    step_id: str = Field(min_length=1, max_length=80)
    lifecycle: StepLifecycle = StepLifecycle.NORMAL
    target: AuthoringKubernetesDeploymentTarget
    expected_generation: str = Field(min_length=1, max_length=160)
    expected_image: str = Field(min_length=1, max_length=512)
    credentials: AuthoringKubernetesApiCredentials
    policy: KubernetesRolloutVerifyPolicy = Field(default_factory=KubernetesRolloutVerifyPolicy)
    timeout_seconds: float = Field(default=300, gt=0, le=3600)
    workspace: PlanStepWorkspace = Field(default_factory=PlanStepWorkspace)
    cache: PlanStepCache = Field(default_factory=PlanStepCache)
    on_failure: StepFailurePolicy = StepFailurePolicy.STOP
    when: StepCondition | None = None
    retry: StepRetryPolicy = Field(default_factory=StepRetryPolicy)

    @model_validator(mode="after")
    def validate_contract(self) -> KubernetesRolloutVerifyPlanStep:
        if self.workspace.source.kind != "repository" or self.workspace.publish:
            raise ValueError(
                "Fleet Kubernetes rollout verify uses one non-published repository workspace"
            )
        if self.cache.source.kind != "empty" or self.cache.publish or self.cache.retain:
            raise ValueError("Fleet Kubernetes rollout verify has no cache authority")
        if (
            self.lifecycle is not StepLifecycle.NORMAL
            or self.on_failure is not StepFailurePolicy.STOP
        ):
            raise ValueError("Fleet Kubernetes rollout verify is a normal hard gate")
        if self.retry.max_attempts != 1 or self.retry.known_transient_exit_codes:
            raise ValueError(
                "Fleet Kubernetes rollout verify owns one bounded observation window; outer retries are not supported"
            )
        if not _authoring_output_reference(self.expected_generation):
            raise ValueError(
                "Fleet Kubernetes rollout expected_generation must be one exact earlier step output"
            )
        if not _authoring_output_reference(self.expected_image):
            raise ValueError(
                "Fleet Kubernetes rollout expected_image must be one exact earlier step output"
            )
        return self


class ResolvedKubernetesRolloutVerifyStep(ImmutableModel):
    kind: Literal["kubernetes_rollout_verify"] = "kubernetes_rollout_verify"
    step_id: str = Field(min_length=1, max_length=80)
    lifecycle: StepLifecycle = StepLifecycle.NORMAL
    target: KubernetesDeploymentTarget
    expected_generation: str = Field(min_length=1, max_length=160)
    expected_image: str = Field(min_length=1, max_length=512)
    credentials: KubernetesApiCredentials
    policy: KubernetesRolloutVerifyPolicy
    timeout_seconds: float = Field(gt=0, le=3600)
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
    artifacts: tuple[PlanArtifactDeclaration, ...] = Field(default_factory=tuple, max_length=0)
    outputs: tuple[PlanPublishedOutput, ...] = Field(default_factory=tuple, max_length=0)

    _success_codes = field_validator("success_codes")(_validate_success_codes)

    @model_validator(mode="after")
    def validate_derived_contract(self) -> ResolvedKubernetesRolloutVerifyStep:
        if step_output_reference(self.expected_generation) is None:
            raise ValueError(
                "Resolved Fleet Kubernetes rollout generation must reference one earlier output"
            )
        if step_output_reference(self.expected_image) is None:
            raise ValueError(
                "Resolved Fleet Kubernetes rollout image must reference one earlier output"
            )
        if self.workspace.source.kind != "repository" or self.workspace.publish:
            raise ValueError(
                "Resolved Fleet Kubernetes rollout verify uses one repository workspace"
            )
        if self.cache.source.kind != "empty" or self.cache.publish or self.cache.retain:
            raise ValueError("Resolved Fleet Kubernetes rollout verify has no cache authority")
        if (
            self.lifecycle is not StepLifecycle.NORMAL
            or self.on_failure is not StepFailurePolicy.STOP
        ):
            raise ValueError("Resolved Fleet Kubernetes rollout verify is a normal hard gate")
        if self.retry.max_attempts != 1 or self.retry.known_transient_exit_codes:
            raise ValueError("Resolved Fleet Kubernetes rollout verify cannot add outer retries")
        return self
