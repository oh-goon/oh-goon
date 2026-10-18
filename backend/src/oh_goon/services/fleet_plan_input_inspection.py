"""Read-only provenance for inputs already projected by the canonical source graph.

This module does not resolve values, rewrite bindings, or own a second schema.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.fleet_plan_composition import input_reference
from oh_goon.core.models.fleet_plan import CompiledExecutionTemplate, CompiledGoonImportStep
from oh_goon.core.models.fleet_plan_composition import PlanInputDefinition
from oh_goon.services.fleet_plan_input_resolution import materialize_projected_inputs
from oh_goon.services.fleet_plan_source_graph import FleetPlanSourceGraph


@dataclass(frozen=True, slots=True)
class InputInspection:
    definition: PlanInputDefinition
    # Full reference paths, starting with the direct Goon and ending at the
    # authored input owner. A composite may intentionally forward to >1 owner.
    owner_paths: tuple[tuple[str, ...], ...]


@dataclass(frozen=True, slots=True)
class InputInterfaceInspection:
    inputs: tuple[InputInspection, ...]
    unrouted_inputs: tuple[str, ...]


def inspect_projected_inputs(graph: FleetPlanSourceGraph) -> tuple[InputInspection, ...]:
    """Inspect one analyzed source cut; never re-read or re-resolve a Goon."""
    return inspect_input_interface(graph).inputs


def inspect_input_interface(graph: FleetPlanSourceGraph) -> InputInterfaceInspection:
    """Report accepted contracts and shared names from canonical materialization."""
    root = graph.root
    cache: dict[Path, CompiledExecutionTemplate] = {}
    compiled = materialize_projected_inputs(graph, root.path, _cache=cache)
    results = []
    for definition in compiled.inputs:
        if any(item.name == definition.name for item in root.compiled.inputs):
            results.append(InputInspection(definition, ()))
            continue
        paths = _owners(graph, root.path, definition.name, cache=cache)
        if not paths:
            raise FleetPlanInvalid(f"Projected input {definition.name!r} has no ultimate owner")
        results.append(InputInspection(definition, tuple(paths)))
    return InputInterfaceInspection(tuple(results), compiled.ambiguous_inputs)


def _owners(
    graph: FleetPlanSourceGraph,
    path: Path,
    name: str,
    *,
    cache: dict[Path, CompiledExecutionTemplate],
) -> list[tuple[str, ...]]:
    node = graph.node(path)
    if any(item.name == name for item in node.compiled.inputs):
        return [()]
    compiled = materialize_projected_inputs(graph, path, _cache=cache)
    found: list[tuple[str, ...]] = []
    for step in compiled.steps:
        if not isinstance(step, CompiledGoonImportStep):
            continue
        child_node = graph.goon(path, step)
        for binding in step.inputs:
            if input_reference(binding.value) != name or binding.sources:
                continue
            for suffix in _owners(graph, child_node.path, binding.name, cache=cache):
                found.append((step.goon, *suffix))
    return found
