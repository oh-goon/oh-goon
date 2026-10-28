"""Read-only configuration reports from the original source graph and input binder."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.fleet_plan_composition import bind_inputs, coerce_input, input_reference
from oh_goon.core.models.fleet_plan import (
    AgentPlanStep,
    CommandPlanStep,
    CompiledGoonImportStep,
    ResolvedCommandStep,
)
from oh_goon.core.models.fleet_plan_composition import PlanInputType
from oh_goon.core.models.run_configuration import CatalogPlanSelection
from oh_goon.core.plan_validation_diagnostics import safe_plan_diagnostic
from oh_goon.services.execution_lock_persistence import (
    MAX_EXECUTION_LOCK_BYTES,
    parse_execution_lock,
)
from oh_goon.services.fleet_caller_file_freshness import (
    locate_caller_file,
    read_caller_file_bytes,
    stat_caller_file,
)
from oh_goon.services.fleet_plan_input_inspection import inspect_input_interface
from oh_goon.services.fleet_plan_input_resolution import materialize_projected_inputs
from oh_goon.services.fleet_plan_input_routing import _binding_origin
from oh_goon.services.fleet_plan_resolver import FleetPlanResolver
from oh_goon.services.fleet_plan_source_graph import FleetPlanSourceGraph, FleetPlanSourceNode


def _lock_records(root: Path) -> dict[str, Any]:
    """Read bounded lock records through existing file/lock owners; never verify closure."""
    path = root / "goon.lock"
    if not os.path.lexists(path):
        return {"status": "absent", "skill_pins": []}
    try:
        selected, _ = locate_caller_file(path, root=root)
        captured = read_caller_file_bytes(
            selected, expected=stat_caller_file(selected), max_bytes=MAX_EXECUTION_LOCK_BYTES
        )
        # Parse the captured cut with the original lock owner; reopening the path
        # would discard the no-follow identity proof and its bounded read.
        lock = parse_execution_lock(captured.decode("utf-8", errors="strict"))
    except (FleetPlanInvalid, OSError, UnicodeError, ValueError, RecursionError):
        return {"status": "unavailable", "skill_pins": []}
    return {
        "status": "lock-record-not-verified",
        "skill_pins": [
            {"source_alias": pin.name, "commit": pin.commit}
            for pin in sorted(lock.skill_sources, key=lambda item: item.name)
        ],
    }


def inspect_run_configuration(
    normalized: Any,
    resolver: FleetPlanResolver,
    *,
    source_graph: FleetPlanSourceGraph | None = None,
) -> dict[str, Any]:
    """Project one source cut; never fetch Skills, inspect private agent state or admit work."""
    snapshot = normalized.snapshot
    graph = source_graph or resolver.analyze_source(normalized.plan_path)
    interface = inspect_input_interface(graph)
    agent_source = graph.has_agent_source_contract()
    if (snapshot.selection.agent is not None) != agent_source:
        raise FleetPlanInvalid("agent selection must match the selected source's Agent contract")
    if agent_source:
        graph.validate_agent_selection(
            native_selected=snapshot.selection.agent.implementation is not None,
            require_review_phases=snapshot.selection.repair is not None,
            model_selected=snapshot.selection.agent.model_profile is not None,
        )
    resolved = None
    effective_inputs = {}
    diagnostic = None
    try:
        if agent_source:
            # Capturing Git/Skill bytes is an explicit lock operation. The original
            # binder validates the public contract without acquiring those bytes.
            compiled = materialize_projected_inputs(graph, graph.root.path)
            effective_inputs = bind_inputs(compiled, normalized.inputs, context="run.yml")
        else:
            resolved = resolver.resolve_source_graph(
                graph,
                inputs=normalized.inputs,
                repository=normalized.resolution_repository,
                observe_environment=False,
                resolved_inputs=effective_inputs,
            )
    except (FleetPlanInvalid, ValueError) as exc:
        diagnostic = safe_plan_diagnostic(exc)
    occurrences: list[dict[str, Any]] = []
    declared_steps: list[dict[str, Any]] = []
    resolved_commands = (
        {step.step_id: step for step in resolved.steps if isinstance(step, ResolvedCommandStep)}
        if resolved is not None
        else {}
    )

    def dependency_selections(
        node: FleetPlanSourceNode, lineage: tuple[str, ...]
    ) -> list[dict[str, Any]]:
        selections = []
        definitions = {definition.name: definition for definition in node.compiled.inputs}
        for step in node.compiled.steps:
            if not isinstance(step, CommandPlanStep) or step.uv is None:
                continue
            effective = resolved_commands.get(".".join((*lineage, step.step_id)))
            reference = input_reference(step.uv.group)
            definition = definitions.get(reference) if reference is not None else None
            group = None
            if resolved is None:
                state = "unresolved"
            elif effective is None or effective.uv is None:
                state = "omitted"
            elif reference is not None and (
                definition is None or definition.input_type is PlanInputType.STRING
            ):
                state = "resolved-redacted"
            else:
                group = effective.uv.group
                state = "resolved"
            selections.append(
                {
                    "step": step.step_id,
                    "lock_mode": step.uv.lock_mode,
                    "freshness": "not_checked"
                    if step.uv.lock_mode == "frozen"
                    else "not_established",
                    "declared_group": step.uv.group,
                    "group": group,
                    "group_state": state,
                }
            )
        return selections

    def source_report(
        node: FleetPlanSourceNode,
        *,
        root: bool = False,
        lineage: tuple[str, ...] = (),
    ) -> dict[str, Any]:
        git = None
        if node.git_source is not None:
            pin = node.git_source
            if node.git_path is None or node.local_source is None:
                raise FleetPlanInvalid("Git source owner provenance is unavailable")
            git = {"git": pin.git, "ref": pin.ref, "commit": pin.commit, "path": node.git_path}
            location = node.local_source
        elif root and isinstance(snapshot.selection.source, CatalogPlanSelection):
            location = f"catalog:{snapshot.selection.source.name}/plan.yml"
        elif root:
            location = str(normalized.plan_path)
        else:
            location = node.local_source or f"catalog:{node.package_name}/goon.yml"
        return {
            "path": location,
            "git": git,
            "yaml_sha256": node.source_sha256,
            "project": node.project.identity.model_dump(mode="json")
            if node.project is not None
            else None,
            "runtime": node.compiled.runtime,
            "dependency_selections": dependency_selections(node, lineage),
            "installation": "not-established",
            "execution": "not-started",
        }

    def walk(
        node: Any,
        lineage: tuple[str, ...],
    ) -> None:
        for step in node.compiled.steps:
            if isinstance(step, CompiledGoonImportStep):
                child = graph.goon(node.path, step)
                path = (*lineage, step.step_id)
                definitions = {
                    item.name: item
                    for item in materialize_projected_inputs(graph, child.path).inputs
                }
                bindings = []
                for binding in step.inputs:
                    projected = {
                        "name": binding.name,
                        "origin": _binding_origin(
                            binding.value, binding.sources, ".".join(lineage)
                        ),
                    }
                    definition = definitions.get(binding.name)
                    if (
                        definition is not None
                        and definition.input_type is PlanInputType.SKILL_REFERENCE
                        and not binding.sources
                        and isinstance(binding.value, str)
                    ):
                        try:
                            reference = coerce_input(
                                definition, binding.value, context="authored Skill reference"
                            )
                        except FleetPlanInvalid:
                            pass
                        else:
                            projected["skill"] = {
                                "reference": reference,
                                "proof": "authored-contract-validated",
                            }
                    bindings.append(projected)
                occurrences.append(
                    {
                        "occurrence": list(path),
                        "goon": step.goon,
                        "source_sha256": child.source_sha256,
                        "source": source_report(child, lineage=path),
                        "phase": getattr(step, "phase", None),
                        "requirements": [
                            item.model_dump(mode="json") for item in child.compiled.requirements
                        ],
                        "bindings": bindings,
                    }
                )
                walk(child, path)
            else:
                capabilities = (
                    step.requested_effects.capabilities
                    if isinstance(step, AgentPlanStep)
                    else getattr(step, "capabilities", None)
                )
                workspace = getattr(step, "workspace", None)
                declared_steps.append(
                    {
                        "id": ".".join((*lineage, step.step_id)),
                        "kind": step.kind,
                        "proof": "authored-not-resolved",
                        "capabilities": capabilities.model_dump(mode="json")
                        if capabilities is not None
                        else None,
                        "workspace": workspace.model_dump(mode="json") if workspace else None,
                        "artifacts": [
                            item.model_dump(mode="json") for item in getattr(step, "artifacts", ())
                        ],
                        "outputs": [
                            item.model_dump(mode="json") for item in getattr(step, "outputs", ())
                        ],
                        "requested_effects": step.requested_effects.model_dump(mode="json")
                        if isinstance(step, AgentPlanStep)
                        else None,
                    }
                )

    walk(graph.root, ())
    inputs = []
    for item in interface.inputs:
        definition = item.definition
        contract = definition.model_dump(mode="json")
        redacted = definition.input_type is PlanInputType.STRING
        if redacted:
            contract.pop("default", None)
            contract["default_declared"] = definition.default is not None
        inputs.append(
            {
                "name": definition.name,
                "contract": contract,
                "owners": [list(path) for path in item.owner_paths],
                "origin": "run.yml" if definition.name in normalized.inputs else "goon",
                "value": effective_inputs.get(definition.name)
                if diagnostic is None and not redacted
                else None,
                "value_state": "unverified"
                if diagnostic is not None
                else "redacted"
                if redacted
                else "defined",
            }
        )
    steps = declared_steps
    if resolved is not None:
        # Resolution decides exact membership/order. Details remain declarations:
        # resolved paths and capabilities may incorporate private string inputs.
        declarations = {step["id"]: step for step in declared_steps}
        steps = []
        for effective in resolved.steps:
            declaration = declarations.get(effective.step_id)
            if declaration is None:
                steps.append(
                    {
                        "id": effective.step_id,
                        "kind": effective.kind,
                        "proof": "resolved-membership;declarations-unavailable",
                        "capabilities": None,
                        "workspace": None,
                        "artifacts": [],
                        "outputs": [],
                        "requested_effects": None,
                    }
                )
            else:
                steps.append({**declaration, "proof": "resolved-membership;authored-declarations"})
    lock_records = _lock_records(snapshot.root)
    return {
        "schema": "oh-goon.run-configuration/v1",
        "status": "inputs-valid" if diagnostic is None else "contract-inspected",
        "diagnostic": diagnostic,
        "configuration": {"path": snapshot.relative_path, "sha256": snapshot.sha256},
        "source": {
            **source_report(graph.root, root=True),
            "selection": snapshot.selection.source.model_dump(mode="json"),
            "sha256": graph.root.source_sha256,
            "git_sources": [pin.model_dump(mode="json") for pin in graph.execution_sources],
        },
        "target": normalized.repository.model_dump(mode="json")
        if len(normalized.repositories) == 1
        else {
            "repositories": [
                repository.model_dump(mode="json") for repository in normalized.repositories
            ]
        },
        "inputs": inputs,
        "unrouted_inputs": list(interface.unrouted_inputs),
        "occurrences": occurrences,
        "steps": steps,
        "configuration_evidence": resolved.configuration.model_dump(mode="json")
        if resolved is not None and resolved.configuration is not None
        else None,
        "agent": snapshot.selection.agent.model_dump(mode="json")
        if snapshot.selection.agent is not None
        else None,
        "repair": snapshot.selection.repair.model_dump(mode="json")
        if snapshot.selection.repair is not None
        else None,
        "skills": {
            "pins": lock_records["skill_pins"] if agent_source else [],
            "pin_status": lock_records["status"] if agent_source else "not-applicable",
            "current_closure": "unknown" if agent_source else "not-applicable",
        },
        "proof": {
            "acquisition": "captured" if graph.execution_sources else "not-required",
            "source": "contract-inspected",
            "lock": lock_records["status"],
            "skill_closure": "not-captured" if agent_source else "not-applicable",
            "native_availability": "unknown" if agent_source else "not-applicable",
            "provider": "configuration-not-inspected;not-connected"
            if snapshot.selection.agent is not None
            and (
                snapshot.selection.agent.model_profile is not None
                or snapshot.selection.agent.implementation is not None
            )
            else "not-applicable",
            "admission": "not-started",
            "execution": "not-started",
        },
    }
