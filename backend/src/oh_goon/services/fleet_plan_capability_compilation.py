from __future__ import annotations

from oh_goon.core.fleet_plan_values import PipelineValueCompiler
from oh_goon.core.models.fleet_plan import PlanStep


def compile_step_capabilities(step: PlanStep, compiler: PipelineValueCompiler):
    """Capabilities contain no pipeline-selected host filesystem paths."""

    del compiler
    return step.capabilities
