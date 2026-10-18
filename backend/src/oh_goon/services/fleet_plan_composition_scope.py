from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.fleet_plan_composition import resolve_goon_outputs
from oh_goon.core.fleet_plan_output_refs import step_output_reference
from oh_goon.core.fleet_plan_values import PipelineValueCompiler
from oh_goon.core.models.fleet_plan import (
    CompiledExecutionTemplate,
    CompiledGoonImportStep,
    PlanStep,
    ResolvedImport,
    ResolvedPlanStep,
    ResolvedStepSource,
)
from oh_goon.core.models.fleet_plan_composition import PlanInputValue
from oh_goon.core.models.fleet_plan_requirements import RepositoryRequirement
from oh_goon.integrations.fleet_plan_compilation import qualified_step_id
from oh_goon.services.fleet_plan_resolution_diagnostics import FleetPlanResolutionDiagnostics


@dataclass(frozen=True)
class ResolvedCompositionScope:
    prefix: str
    steps: tuple[ResolvedPlanStep, ...]
    step_sources: tuple[ResolvedStepSource, ...]
    outputs: dict[str, str]
    workspace: str | None = None
    requirements: tuple[RepositoryRequirement, ...] = ()
    declared_requirements: tuple[RepositoryRequirement, ...] = ()
    imports: tuple[ResolvedImport, ...] = ()


def validated_value_compiler(
    *,
    compiled: CompiledExecutionTemplate,
    input_values: dict[str, PlanInputValue],
    prefix: str,
) -> PipelineValueCompiler:
    compiler = PipelineValueCompiler(
        inputs=input_values,
        env=compiled.env,
        qualify_step=lambda step_id: qualified_step_id(prefix, step_id),
    )
    compiler.validate_all()
    return compiler


def render_pipeline_step(step: PlanStep, compiler: PipelineValueCompiler) -> PlanStep:
    def render(value):
        if isinstance(value, str):
            return compiler.render_authoring_text(value)
        if isinstance(value, tuple):
            return tuple(render(item) for item in value)
        if isinstance(value, list):
            return [render(item) for item in value]
        if isinstance(value, dict):
            return {key: render(item) for key, item in value.items()}
        return value

    return type(step).model_validate(render(step.model_dump(mode="python")))


def prepare_composition_scope(
    diagnostics: FleetPlanResolutionDiagnostics,
    *,
    plan_path: Path,
    compiled: CompiledExecutionTemplate,
    prefix: str,
    input_values: dict[str, PlanInputValue],
    input_origins: dict[str, str],
    external_step_ids: frozenset[str],
) -> PipelineValueCompiler:
    reject_ambiguous_external_step_ids(compiled, external_step_ids)
    compiler = validated_value_compiler(compiled=compiled, input_values=input_values, prefix=prefix)
    diagnostics.trace_pipeline_values(
        plan_path=plan_path,
        compiled=compiled,
        prefix=prefix,
        input_values=input_values,
        input_origins=input_origins,
        compiler=compiler,
    )
    return compiler


def referenced_step_ids(values: dict[str, PlanInputValue]) -> frozenset[str]:
    step_ids: set[str] = set()
    for value in values.values():
        if not isinstance(value, str):
            continue
        reference = step_output_reference(value)
        if reference is not None:
            step_ids.add(reference[0])
    return frozenset(step_ids)


def reject_ambiguous_external_step_ids(
    compiled: CompiledExecutionTemplate, external_step_ids: frozenset[str]
) -> None:
    if not external_step_ids:
        return
    local_ids = {step.step_id for step in compiled.steps}
    ambiguous = sorted(local_ids & external_step_ids)
    if ambiguous:
        raise FleetPlanInvalid(
            "Compiled execution caller-owned output references collide with local step ids: "
            + ", ".join(ambiguous)
        )


def bind_goon_alias(
    step: CompiledGoonImportStep,
    scope: ResolvedCompositionScope,
    aliases: dict[str, dict[str, str]],
    workspace_aliases: dict[str, str],
) -> None:
    aliases[step.step_id] = scope.outputs
    if scope.workspace is not None:
        workspace_aliases[step.step_id] = scope.workspace


def finalize_composition_scope(
    compiled: CompiledExecutionTemplate,
    *,
    prefix: str,
    goon_aliases: dict[str, dict[str, str]],
    resolved: list[ResolvedPlanStep],
    step_sources: list[ResolvedStepSource] | None = None,
    imports: list[ResolvedImport] | None = None,
    allow_multiple_published_workspaces: bool = False,
) -> ResolvedCompositionScope:
    step_ids = tuple(step.step_id for step in resolved)
    if len(step_ids) != len(set(step_ids)):
        raise FleetPlanInvalid("Resolved Fleet plan step_id values must be unique")
    steps = tuple(resolved)
    outputs = resolve_goon_outputs(compiled, prefix=prefix, goon_aliases=goon_aliases, steps=steps)
    published_workspaces = tuple(step.step_id for step in steps if step.workspace.publish)
    if len(published_workspaces) > 1 and not allow_multiple_published_workspaces:
        raise FleetPlanInvalid("One Goon may publish at most one workspace")
    workspace = published_workspaces[0] if len(published_workspaces) == 1 else None
    declared = tuple(compiled.requirements)
    requirements = _unique_requirements(declared)
    return ResolvedCompositionScope(
        prefix=prefix,
        steps=steps,
        step_sources=tuple(step_sources or ()),
        outputs=outputs,
        workspace=workspace,
        requirements=requirements,
        declared_requirements=declared,
        imports=tuple(imports or ()),
    )


def _unique_requirements(
    requirements: tuple[RepositoryRequirement, ...] | list[RepositoryRequirement],
) -> tuple[RepositoryRequirement, ...]:
    result: list[RepositoryRequirement] = []
    seen: set[str] = set()
    for requirement in requirements:
        identity = requirement.model_dump_json()
        if identity in seen:
            continue
        seen.add(identity)
        result.append(requirement)
    return tuple(result)
