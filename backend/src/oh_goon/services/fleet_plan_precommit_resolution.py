from __future__ import annotations

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.fleet_plan_values import PipelineValueCompiler
from oh_goon.core.models.fleet_plan import (
    DEFAULT_PRE_COMMIT_REQUIREMENT,
    PreCommitPlanStep,
    ResolvedPreCommitStep,
)
from oh_goon.engine.fleet_tools import FleetToolStore
from oh_goon.services.fleet_plan_capability_compilation import compile_step_capabilities


def resolve_pre_commit(
    tools: FleetToolStore | None,
    step: PreCommitPlanStep,
    *,
    step_id: str,
    value_compiler: PipelineValueCompiler,
) -> ResolvedPreCommitStep:
    environment = value_compiler.compile_imports(env_names=step.imports.env)
    if tools is None:
        raise FleetPlanInvalid(
            "Fleet plan tool runtime is not configured; run 'oh-goon plan prepare <plan.yml>'"
        )
    runtime = tools.resolve_prepared_pre_commit(DEFAULT_PRE_COMMIT_REQUIREMENT)
    return ResolvedPreCommitStep(
        step_id=step_id,
        lifecycle=step.lifecycle,
        if_missing=step.if_missing,
        timeout_seconds=step.timeout_seconds,
        imports=step.imports,
        workspace=step.workspace,
        capabilities=compile_step_capabilities(step, value_compiler),
        environment=environment,
        on_failure=step.on_failure,
        when=step.when,
        retry=step.retry,
        artifacts=step.artifacts,
        outputs=step.outputs,
        runner_requirement=runtime.requirement,
        runtime_id=runtime.runtime_id,
        runtime_pyproject_sha256=runtime.pyproject_sha256,
        runtime_uv_lock_sha256=runtime.uv_lock_sha256,
    )
