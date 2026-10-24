"""Read-only *presentation* of bindings from one already-analyzed Plan/Goon graph.

The canonical resolver has already materialized and resolved this source cut. This
visitor never reads sources, evaluates references, chooses a value or admits work.
It traverses import occurrences, not graph definitions (which may be shared).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.fleet_plan_composition import input_reference
from oh_goon.core.fleet_plan_output_refs import (
    goon_output_reference,
    goon_workspace_reference,
    step_output_reference,
)
from oh_goon.core.models.fleet_plan import CompiledGoonImportStep, ResolvedFleetPlan
from oh_goon.core.models.fleet_plan_composition import PlanInputSource
from oh_goon.core.models.fleet_plan_diagnostics import PlanValueResolutionEntry
from oh_goon.integrations.fleet_plan_compilation import qualified_step_id
from oh_goon.services.fleet_plan_input_inspection import inspect_projected_inputs
from oh_goon.services.fleet_plan_input_resolution import materialize_projected_inputs
from oh_goon.services.fleet_plan_source_graph import FleetPlanSourceGraph


@dataclass(frozen=True, slots=True)
class CallerFileRoutingOrigin:
    """Presentation provenance only; parsed values never leave the CLI collector."""

    relative_path: str
    sha256: str
    names: frozenset[str]


def _binding_origin(
    value: object, sources: tuple[PlanInputSource, ...], prefix: str
) -> dict[str, object]:
    """Only structural descriptors; never publish literals or resolved secret values."""
    if sources:
        return {"kind": "external_source", "source_kinds": [source.kind for source in sources]}
    if isinstance(value, str):
        parent = input_reference(value)
        if parent is not None:
            return {"kind": "parent_input", "parent_input": parent}
        workspace = goon_workspace_reference(value)
        if workspace:
            return {
                "kind": "producer_workspace",
                "producer_import_instance_id": qualified_step_id(prefix, workspace),
            }
        goon_output = goon_output_reference(value)
        if goon_output:
            return {
                "kind": "deferred_goon_output",
                "producer_import_instance_id": qualified_step_id(prefix, goon_output[0]),
                "output": goon_output[1],
            }
        step_output = step_output_reference(value)
        if step_output:
            return {
                "kind": "deferred_step_output",
                "producer_step_id": qualified_step_id(prefix, step_output[0]),
                "output": step_output[1],
            }
        if "${{" in value:
            # Mixed/unknown expressions are intentionally not echoed or resolved.
            return {"kind": "expression_redacted"}
    return {"kind": "literal_redacted"}


def project_input_routing(
    graph: FleetPlanSourceGraph,
    resolved: ResolvedFleetPlan,
    values: tuple[PlanValueResolutionEntry, ...],
    *,
    caller_file: CallerFileRoutingOrigin | None = None,
) -> dict[str, object]:
    """Project the resolved graph without another source read or input resolution."""
    root_digest = graph.root.source_sha256
    if root_digest is None:
        raise FleetPlanInvalid("Plan routing cannot report an unidentifiable source cut")
    active = {item.import_id: item for item in resolved.imports}
    resolved_steps = {step.step_id for step in resolved.steps}
    root = graph.root.path
    root_compiled = materialize_projected_inputs(graph, root)
    root_names = {item.name for item in root_compiled.inputs}
    root_origins = {
        item.name: item.origin
        for item in values
        if item.scope == "root" and item.namespace.value == "input"
    }
    bindings: list[dict[str, object]] = []
    occurrences: list[dict[str, object]] = []

    def walk(path: Path, prefix: str, parent_id: str | None, lineage: dict[str, str]) -> None:
        compiled = materialize_projected_inputs(graph, path)
        for step in compiled.steps:
            if not isinstance(step, CompiledGoonImportStep):
                continue
            instance_id = qualified_step_id(prefix, step.step_id)
            if instance_id not in active:
                continue  # A condition omitted this import in the canonical resolution.
            actual = active[instance_id]
            if actual.name != step.goon or actual.parent_import_id != parent_id:
                raise FleetPlanInvalid("Plan routing disagrees with resolved import identity")
            child = graph.goon(path, step)
            occurrences.append(
                {
                    "import_instance_id": instance_id,
                    "parent_import_instance_id": parent_id,
                    "goon": step.goon,
                    "source_sha256": actual.source_sha256,
                    "source": child.local_source or f"goon:{step.goon}/goon.yml",
                }
            )
            child_lineage: dict[str, str] = {}
            for binding in step.inputs:
                descriptor = _binding_origin(binding.value, binding.sources, prefix)
                if descriptor["kind"] in {"producer_workspace", "deferred_goon_output"}:
                    if descriptor["producer_import_instance_id"] not in active:
                        raise FleetPlanInvalid(
                            "Plan routing producer import is not in the resolved cut"
                        )
                if descriptor["kind"] == "deferred_step_output":
                    if descriptor["producer_step_id"] not in resolved_steps:
                        raise FleetPlanInvalid(
                            "Plan routing producer Step is not in the resolved cut"
                        )
                root_input = None
                if descriptor["kind"] == "parent_input":
                    root_input = lineage.get(str(descriptor["parent_input"]))
                    if root_input is not None:
                        child_lineage[binding.name] = root_input
                bindings.append(
                    {
                        "import_instance_id": instance_id,
                        "parent_import_instance_id": parent_id,
                        "goon": step.goon,
                        "input": binding.name,
                        "root_input": root_input,
                        "origin": descriptor,
                    }
                )
            walk(child.path, instance_id, instance_id, child_lineage)

    walk(root, "", None, {name: name for name in root_names})
    # Source identity in this report is the root *file*, never the whole closure.
    inputs = []
    for inspection in inspect_projected_inputs(graph):
        name = inspection.definition.name
        origin_kind = root_origins.get(name, "unobserved")
        selected_source: dict[str, str] = {"kind": origin_kind}
        if origin_kind == "supplied" and caller_file is not None and name in caller_file.names:
            selected_source = {
                "kind": "inputs_file",
                "path": caller_file.relative_path,
                "sha256": caller_file.sha256,
            }
        inputs.append(
            {
                "name": name,
                "origin": selected_source,
                "owner_paths": [list(item) for item in inspection.owner_paths],
                "recipients": [
                    {
                        "import_instance_id": item["import_instance_id"],
                        "goon": item["goon"],
                        "input": item["input"],
                    }
                    for item in bindings
                    if item["root_input"] == name
                ],
            }
        )
    return {
        "schema": "oh-goon.plan-input-routing/v1",
        "status": "resolved_not_executed",
        "root": root.as_posix(),
        "root_source_sha256": root_digest,
        "plan_id": resolved.plan_id,
        "inputs": inputs,
        "occurrences": occurrences,
        "bindings": bindings,
    }
