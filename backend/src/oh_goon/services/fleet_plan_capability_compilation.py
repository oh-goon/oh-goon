from __future__ import annotations

from oh_goon.core.fleet_plan_values import PipelineValueCompiler
from oh_goon.core.models.fleet_plan import (
    CommandPlanStep,
    DependencyInventoryPlanStep,
    ExtensionPlanStep,
    GitPlanStep,
    PreCommitPlanStep,
)
from oh_goon.core.models.fleet_plan_step_contract import PlanStepCapabilities


def compile_step_capabilities(
    step: CommandPlanStep
    | DependencyInventoryPlanStep
    | ExtensionPlanStep
    | GitPlanStep
    | PreCommitPlanStep,
    compiler: PipelineValueCompiler,
) -> PlanStepCapabilities:
    """Capabilities contain no pipeline-selected host filesystem paths."""

    del compiler
    return step.capabilities
