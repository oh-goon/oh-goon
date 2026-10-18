from __future__ import annotations

from pathlib import Path

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.fleet_plan_composition import (
    bind_inputs,
    qualify_step_dependencies,
    render_step,
    resolve_output_references,
)
from oh_goon.core.fleet_plan_output_refs import goon_workspace_reference
from oh_goon.core.models.fleet_plan import (
    CompiledExecutionTemplate,
    CompiledGoonImportStep,
    ResolvedImport,
    ResolvedPlanStep,
    ResolvedStepSource,
)
from oh_goon.core.models.fleet_plan_composition import PlanInputType, PlanInputValue
from oh_goon.integrations.fleet_plan_compilation import qualified_step_id
from oh_goon.services.fleet_plan_composition_scope import (
    ResolvedCompositionScope,
    bind_goon_alias,
    finalize_composition_scope,
    prepare_composition_scope,
    referenced_step_ids,
    render_pipeline_step,
)
from oh_goon.services.fleet_plan_input_resolution import (
    materialize_projected_inputs,
    resolve_input_sources,
)
from oh_goon.services.fleet_plan_resolution_context import FleetPlanResolutionContext
from oh_goon.services.fleet_plan_step_resolution import resolve_plan_step


def resolve_compiled_steps(
    context: FleetPlanResolutionContext,
    plan_path: Path,
    compiled: CompiledExecutionTemplate,
    *,
    prefix: str,
    stack: tuple[Path, ...],
    input_values: dict[str, PlanInputValue],
    input_origins: dict[str, str],
    external_step_ids: frozenset[str] = frozenset(),
) -> ResolvedCompositionScope:
    value_compiler = prepare_composition_scope(
        context.diagnostics,
        plan_path=plan_path,
        compiled=compiled,
        prefix=prefix,
        input_values=input_values,
        input_origins=input_origins,
        external_step_ids=external_step_ids,
    )
    resolved: list[ResolvedPlanStep] = []
    step_sources: list[ResolvedStepSource] = []
    resolved_imports: list[ResolvedImport] = []
    goon_aliases: dict[str, dict[str, str]] = {}
    goon_workspaces: dict[str, str] = {}
    inclusions = {item.step_id: item for item in compiled.inclusions}
    for raw_step in compiled.steps:
        inclusion = inclusions.get(raw_step.step_id)
        if inclusion is not None and context.diagnostics.should_omit_step(
            plan_path=plan_path,
            compiled=compiled,
            step=raw_step,
            inclusion=inclusion,
            prefix=prefix,
            input_values=input_values,
        ):
            continue
        step = render_pipeline_step(render_step(raw_step, input_values), value_compiler)
        if isinstance(step, CompiledGoonImportStep):
            child_steps, child_imports, child_sources = resolve_goon_edge(
                context,
                plan_path,
                step,
                prefix=prefix,
                stack=stack,
                goon_aliases=goon_aliases,
                goon_workspaces=goon_workspaces,
                external_step_ids=external_step_ids,
            )
            resolved.extend(child_steps)
            step_sources.extend(child_sources)
            resolved_imports.extend(child_imports)
            continue
        step = qualify_step_dependencies(
            step, prefix, goon_aliases, preserved_step_ids=external_step_ids
        )
        resolved_step = resolve_plan_step(
            context,
            step,
            plan_base=plan_path.parent,
            step_id=qualified_step_id(prefix, step.step_id),
            value_compiler=value_compiler,
        )
        resolved.append(resolved_step)
        source = _resolved_step_source(context, plan_path, raw_step.step_id, resolved_step.step_id)
        if source is not None:
            step_sources.append(source)
        context.diagnostics.trace_included(plan_path=plan_path, step=resolved_step)
    return finalize_composition_scope(
        compiled,
        prefix=prefix,
        goon_aliases=goon_aliases,
        resolved=resolved,
        step_sources=step_sources,
        imports=resolved_imports,
        allow_multiple_published_workspaces=plan_path.name == "plan.yml",
    )


def resolve_goon_edge(
    context: FleetPlanResolutionContext,
    plan_path: Path,
    step: CompiledGoonImportStep,
    *,
    prefix: str,
    stack: tuple[Path, ...],
    goon_aliases: dict[str, dict[str, str]],
    goon_workspaces: dict[str, str],
    external_step_ids: frozenset[str],
) -> tuple[
    tuple[ResolvedPlanStep, ...],
    tuple[ResolvedImport, ...],
    tuple[ResolvedStepSource, ...],
    tuple,
]:
    source = context.source_graph.goon(plan_path, step)
    child_path = source.path
    child = materialize_projected_inputs(context.source_graph, child_path)
    if source.package_name != step.goon or source.source_sha256 is None:
        raise FleetPlanInvalid(
            f"Resolved Goon import {step.goon!r} is missing its exact source identity"
        )
    if child_path in stack:
        chain = " -> ".join(str(path) for path in (*stack, child_path))
        raise FleetPlanInvalid(f"Invalid Goon composition cycle: {chain}")
    child_prefix = qualified_step_id(prefix, step.step_id)
    child_inputs, child_origins, child_external_step_ids = child_goon_bindings(
        context,
        child,
        child_path,
        step,
        prefix=prefix,
        goon_aliases=goon_aliases,
        goon_workspaces=goon_workspaces,
        external_step_ids=external_step_ids,
    )
    scope = resolve_compiled_steps(
        context,
        child_path,
        child,
        prefix=child_prefix,
        stack=(*stack, child_path),
        input_values=child_inputs,
        input_origins=child_origins,
        external_step_ids=child_external_step_ids,
    )
    bind_goon_alias(step, scope, goon_aliases, goon_workspaces)
    parent = ResolvedImport(
        import_id=scope.prefix,
        name=step.goon,
        source_sha256=source.source_sha256,
        step_ids=tuple(item.step_id for item in scope.steps),
        requirements=scope.declared_requirements,
        failure_impact=step.failure_impact,
    )
    imports = (
        parent,
        *(
            imported.model_copy(update={"parent_import_id": parent.import_id})
            if imported.parent_import_id is None
            else imported
            for imported in scope.imports
        ),
    )
    context.diagnostics.trace_expansion(
        plan_path=plan_path,
        step_id=step.step_id,
        prefix=prefix,
        compiled_step_ids=tuple(item.step_id for item in scope.steps),
    )
    return scope.steps, imports, scope.step_sources


def child_goon_bindings(
    context: FleetPlanResolutionContext,
    child: CompiledExecutionTemplate,
    child_path: Path,
    step: CompiledGoonImportStep,
    *,
    prefix: str,
    goon_aliases: dict[str, dict[str, str]],
    goon_workspaces: dict[str, str],
    external_step_ids: frozenset[str],
):
    explicit: dict[str, PlanInputValue] = {}
    origins: dict[str, str] = {}
    referenced_workspaces: set[str] = set()
    definitions = {item.name: item for item in child.inputs}
    for item in step.inputs:
        definition = definitions.get(item.name)
        if definition is None:
            raise FleetPlanInvalid(
                f"Goon import {step.goon!r} received unknown input {item.name!r}"
            )
        if item.sources:
            found, value, origin = resolve_input_sources(
                item.sources, context=context.input_resolution, input_name=item.name
            )
            if found:
                explicit[item.name] = value
                origins[item.name] = origin
            continue
        workspace_goon = (
            goon_workspace_reference(item.value) if isinstance(item.value, str) else None
        )
        if definition.input_type is PlanInputType.WORKSPACE_REFERENCE and workspace_goon is None:
            raise FleetPlanInvalid(
                f"Goon input {item.name!r} is a structural workspace reference and must bind "
                "one ${{ goons.<goon>.workspace }} value"
            )
        if workspace_goon is not None:
            producer = goon_workspaces.get(workspace_goon)
            if producer is None:
                if workspace_goon not in goon_aliases:
                    raise FleetPlanInvalid(
                        f"Goon workspace {workspace_goon} is unavailable from earlier imports; "
                        "place the producer before this consumer and check the import ID and workspace publication"
                    )
                raise FleetPlanInvalid(f"Goon import {workspace_goon} does not publish a workspace")
            explicit[item.name] = producer
            origins[item.name] = f"workspace:{workspace_goon}"
            referenced_workspaces.add(producer)
            continue
        explicit[item.name] = resolve_output_references(
            item.value,
            prefix=prefix,
            goon_aliases=goon_aliases,
            preserved_step_ids=external_step_ids,
        )
        origins[item.name] = "bound"
    inputs = bind_inputs(child, explicit, context=str(child_path))
    origins.update({name: "default" for name in inputs if name not in origins})
    external = set(external_step_ids)
    external.update(referenced_step_ids(explicit))
    external.update(referenced_workspaces)
    return inputs, origins, frozenset(external)


def _resolved_step_source(
    context: FleetPlanResolutionContext,
    plan_path: Path,
    authored_step_id: str,
    resolved_step_id: str,
) -> ResolvedStepSource | None:
    node = context.source_graph.node(plan_path)
    position = node.step_positions.get(authored_step_id)
    if position is None:
        return None
    return ResolvedStepSource(
        step_id=resolved_step_id,
        source=node.local_source
        or _source_label(context, node.path, node.package_kind, node.package_name),
        line=position.line,
        column=position.column,
    )


def _source_label(
    context: FleetPlanResolutionContext,
    path: Path,
    package_kind: str | None,
    package_name: str | None,
) -> str:
    if package_kind is not None and package_name is not None:
        return f"{package_kind}:{package_name}/{path.name}"
    if path.is_relative_to(context.plan_root):
        return path.relative_to(context.plan_root).as_posix()
    return path.name
