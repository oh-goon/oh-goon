"""Read-only provenance for inputs already projected by the canonical source graph.

This module does not resolve values, rewrite bindings, or own a second schema.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.models.fleet_plan import CompiledGoonImportStep
from oh_goon.core.models.fleet_plan_composition import PlanInputDefinition
from oh_goon.services.fleet_plan_input_resolution import materialize_projected_inputs
from oh_goon.services.fleet_plan_source_graph import FleetPlanSourceGraph


@dataclass(frozen=True, slots=True)
class InputInspection:
    definition: PlanInputDefinition
    # Full reference paths, starting with the direct Goon and ending at the
    # authored input owner. A composite may intentionally forward to >1 owner.
    owner_paths: tuple[tuple[str, ...], ...]


def inspect_projected_inputs(graph: FleetPlanSourceGraph) -> tuple[InputInspection, ...]:
    """Inspect one analyzed source cut; never re-read or re-resolve a Goon."""
    root = graph.root
    is_plan = root.path.name != "goon.yml"
    compiled = materialize_projected_inputs(graph, root.path, plan_root=is_plan)
    results = []
    for definition in compiled.inputs:
        if definition.name not in root.compiled.projected_inputs:
            results.append(InputInspection(definition, ()))
            continue
        paths = _owners(graph, root.path, definition.name, plan_root=is_plan)
        if not paths:
            raise FleetPlanInvalid(f"Projected input {definition.name!r} has no ultimate owner")
        results.append(InputInspection(definition, tuple(paths)))
    return tuple(results)


def _owners(
    graph: FleetPlanSourceGraph, path: Path, name: str, *, plan_root: bool
) -> list[tuple[str, ...]]:
    node = graph.node(path)
    if name not in node.compiled.projected_inputs:
        return [()] if any(item.name == name for item in node.compiled.inputs) else []
    found: list[tuple[str, ...]] = []
    expected_reference = f"${{{{ inputs.{name} }}}}"
    for step in node.compiled.steps:
        if not isinstance(step, CompiledGoonImportStep):
            continue
        child_node = graph.goon(path, step.goon)
        child = materialize_projected_inputs(graph, child_node.path)
        if plan_root:
            projected_child_name = name
            if not any(item.name == name for item in child.inputs):
                continue
            if any(binding.name == name for binding in step.inputs):
                continue  # canonical materialization rejects this ambiguity
        else:
            forwarded = [
                binding
                for binding in step.inputs
                if binding.value == expected_reference and not binding.sources
            ]
            if not forwarded:
                continue
            for binding in forwarded:
                for suffix in _owners(graph, child_node.path, binding.name, plan_root=False):
                    found.append((step.goon, *suffix))
            continue
        for suffix in _owners(graph, child_node.path, projected_child_name, plan_root=False):
            found.append((step.goon, *suffix))
    return found
