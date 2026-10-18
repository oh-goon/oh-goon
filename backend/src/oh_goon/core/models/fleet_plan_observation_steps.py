from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from oh_goon.core.models.base import ImmutableModel
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
)
from oh_goon.core.models.fleet_plan_values import PlanStepImports, ResolvedEnvironmentBinding


class DependencyInventoryPlanStep(ImmutableModel):
    """Built-in, read-only dependency observation Step.

    It parses repository files as data and never executes repository code or network work.
    A successful Step publishes one complete typed dependency observation snapshot into
    canonical execution evidence.
    """

    kind: Literal["dependency_inventory"] = "dependency_inventory"
    step_id: str = Field(min_length=1, max_length=80)
    timeout_seconds: float = Field(default=300, gt=0, le=3600)
    lifecycle: StepLifecycle = StepLifecycle.NORMAL
    ecosystem: Literal["pypi"] = "pypi"
    imports: PlanStepImports = Field(default_factory=PlanStepImports)
    workspace: PlanStepWorkspace = Field(default_factory=PlanStepWorkspace)
    cache: PlanStepCache = Field(default_factory=PlanStepCache)
    capabilities: PlanStepCapabilities = Field(default_factory=PlanStepCapabilities)
    success_codes: tuple[int, ...] = (0,)
    on_failure: StepFailurePolicy = StepFailurePolicy.STOP
    when: StepCondition | None = None
    retry: StepRetryPolicy = Field(default_factory=StepRetryPolicy)
    artifacts: tuple[PlanArtifactDeclaration, ...] = Field(default_factory=tuple, max_length=0)
    outputs: tuple[PlanPublishedOutput, ...] = Field(default_factory=tuple, max_length=0)

    @model_validator(mode="after")
    def validate_read_only_contract(self) -> DependencyInventoryPlanStep:
        if self.success_codes != (0,):
            raise ValueError("dependency_inventory uses the fixed success code 0")
        if self.capabilities.egress.mode != "none":
            raise ValueError("dependency_inventory cannot request network egress")
        return self


class ResolvedDependencyInventoryStep(DependencyInventoryPlanStep):
    environment: tuple[ResolvedEnvironmentBinding, ...] = Field(
        default_factory=tuple, max_length=64
    )
