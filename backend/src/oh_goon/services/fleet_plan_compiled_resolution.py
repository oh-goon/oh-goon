from __future__ import annotations

from pathlib import Path

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.fleet_plan_composition import (
    bind_inputs,
    input_reference,
    qualify_step_dependencies,
    render_step,
    resolve_output_references,
)
from oh_goon.core.fleet_plan_output_refs import (
    goon_output_reference,
    goon_workspace_reference,
    step_output_reference,
)
from oh_goon.core.models.fleet_plan import (
    AgentPlanStep,
    CompiledExecutionTemplate,
    CompiledGoonImportStep,
    ResolvedImport,
    ResolvedPlanStep,
    ResolvedStepSource,
)
from oh_goon.core.models.fleet_plan_composition import PlanInputType, PlanInputValue
from oh_goon.core.models.fleet_plan_diagnostics import (
    PlanConfigurationBinding,
    PlanInputSourceObservation,
)
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
from oh_goon.services.fleet_plan_step_resolution import bind_native_agent_effects, resolve_plan_step


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
    input_bindings: dict[str, PlanConfigurationBinding] | None = None,
) -> ResolvedCompositionScope:
    value_compiler = prepare_composition_scope(
        context.diagnostics,
        plan_path=plan_path,
        compiled=compiled,
        prefix=prefix,
        input_values=input_values,
        input_origins=input_origins,
        external_step_ids=external_step_ids,
        input_bindings=input_bindings,
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
            assert isinstance(raw_step, CompiledGoonImportStep)
            child_steps, child_imports, child_sources = resolve_goon_edge(
                context,
                plan_path,
                step,
                prefix=prefix,
                stack=stack,
                goon_aliases=goon_aliases,
                goon_workspaces=goon_workspaces,
                external_step_ids=external_step_ids,
                authored_step=raw_step,
            )
            resolved.extend(child_steps)
            step_sources.extend(child_sources)
            resolved_imports.extend(child_imports)
            continue
        step = qualify_step_dependencies(
            step, prefix, goon_aliases, preserved_step_ids=external_step_ids
        )
        if isinstance(step, AgentPlanStep):
            step = bind_native_agent_effects(step, context.native_agent)
        agent_handoff = None
        if isinstance(step, AgentPlanStep) and context.agent_capture is not None:
            assert isinstance(raw_step, AgentPlanStep)
            agent_handoff = context.agent_capture(
                plan_path,
                # Rendering revalidates numeric defaults. Preserve the authored
                # timeout scalar so existing immutable deliveries keep their bytes.
                step.model_copy(update={"timeout_seconds": raw_step.timeout_seconds}),
                qualified_step_id(prefix, step.step_id),
                compiled.inputs,
                input_values,
            )
        resolved_step = resolve_plan_step(
            context,
            step,
            plan_base=plan_path.parent,
            step_id=qualified_step_id(prefix, step.step_id),
            value_compiler=value_compiler,
            agent_handoff=agent_handoff,
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
    authored_step: CompiledGoonImportStep,
) -> tuple[
    tuple[ResolvedPlanStep, ...],
    tuple[ResolvedImport, ...],
    tuple[ResolvedStepSource, ...],
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
    child_inputs, child_origins, child_external_step_ids, child_bindings = child_goon_bindings(
        context,
        child,
        child_path,
        step,
        prefix=prefix,
        goon_aliases=goon_aliases,
        goon_workspaces=goon_workspaces,
        external_step_ids=external_step_ids,
        authored_step=authored_step,
        parent_path=plan_path,
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
        input_bindings=child_bindings,
    )
    bind_goon_alias(step, scope, goon_aliases, goon_workspaces)
    parent = ResolvedImport(
        import_id=scope.prefix,
        phase=step.phase,
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
    authored_step: CompiledGoonImportStep,
    parent_path: Path,
) -> tuple[
    dict[str, PlanInputValue], dict[str, str], frozenset[str], dict[str, PlanConfigurationBinding]
]:
    explicit: dict[str, PlanInputValue] = {}
    origins: dict[str, str] = {}
    referenced_workspaces: set[str] = set()
    definitions = {item.name: item for item in child.inputs}
    authored = {item.name: item for item in authored_step.inputs}
    bindings: dict[str, PlanConfigurationBinding] = {}
    for item in step.inputs:
        definition = definitions.get(item.name)
        if definition is None:
            raise FleetPlanInvalid(
                f"Goon import {step.goon!r} received unknown input {item.name!r}"
            )
        if item.sources:
            observations: list[PlanInputSourceObservation] = []
            found, value, origin = resolve_input_sources(
                item.sources,
                context=context.input_resolution,
                input_name=item.name,
                observations=observations,
            )
            bindings[item.name] = PlanConfigurationBinding(
                kind="external_source" if found else "default", candidates=tuple(observations)
            )
            if found:
                explicit[item.name] = value
                origins[item.name] = origin
            continue
        bindings[item.name] = _configuration_binding(authored[item.name].value, prefix)
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
    node = context.source_graph.node(parent_path)
    for name, binding in bindings.items():
        position = node.value_positions.get(f"binding/{authored_step.step_id}/{name}")
        if position is None:
            position = node.value_positions.get(f"import/{authored_step.step_id}")
        bindings[name] = binding.model_copy(
            update={
                "source_sha256": node.source_sha256,
                "parent_scope": prefix or None,
                "line": position.line if position else None,
                "column": position.column if position else None,
            }
        )
    external = set(external_step_ids)
    external.update(referenced_step_ids(explicit))
    external.update(referenced_workspaces)
    return inputs, origins, frozenset(external), bindings


def _configuration_binding(value: PlanInputValue, prefix: str) -> PlanConfigurationBinding:
    """Describe the already compiled edge without retaining its literal value."""
    if isinstance(value, str):
        parent = input_reference(value)
        if parent is not None:
            return PlanConfigurationBinding(
                kind="parent_input", parent_scope=prefix or None, parent_input=parent
            )
        workspace = goon_workspace_reference(value)
        if workspace:
            return PlanConfigurationBinding(
                kind="producer_workspace", producer=qualified_step_id(prefix, workspace)
            )
        goon_output = goon_output_reference(value)
        if goon_output:
            return PlanConfigurationBinding(
                kind="deferred_goon_output",
                producer=qualified_step_id(prefix, goon_output[0]),
                output=goon_output[1],
            )
        step_output = step_output_reference(value)
        if step_output:
            return PlanConfigurationBinding(
                kind="deferred_step_output",
                producer=qualified_step_id(prefix, step_output[0]),
                output=step_output[1],
            )
        if "${{" in value:
            return PlanConfigurationBinding(kind="expression")
    return PlanConfigurationBinding(kind="literal")


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
