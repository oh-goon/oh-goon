"""Read-only semantic comparison using the existing Fleet resolver, not a new evaluator."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any

from oh_goon.core.models.fleet_plan_composition import PlanInputValue
from oh_goon.services.fleet_plan_resolver import FleetPlanResolver
from oh_goon.services.fleet_plan_source_graph import FleetPlanSourceGraph


def _steps(
    graph: FleetPlanSourceGraph, resolver: FleetPlanResolver, values: Mapping[str, PlanInputValue]
) -> list[dict[str, Any]]:
    resolved = resolver.resolve_source_graph(graph, inputs=dict(values), environment={})
    return [
        {
            "id": step.step_id,
            "kind": step.kind,
            "capabilities_sha256": hashlib.sha256(
                json.dumps(step.capabilities.model_dump(mode="json"), sort_keys=True).encode(
                    "utf-8"
                )
            ).hexdigest()
            if hasattr(step, "capabilities")
            else None,
        }
        for step in resolved.steps
    ]


def compare_resolved_drafts(
    *,
    original: FleetPlanSourceGraph,
    proposed: FleetPlanSourceGraph,
    resolver: FleetPlanResolver,
    values: Mapping[str, PlanInputValue],
    original_names: set[str],
    proposed_names: set[str],
    original_missing: bool,
    proposed_missing: bool,
) -> dict[str, Any]:
    """Only resolve when required inputs are present; results are never admission."""
    if original_missing or proposed_missing:
        return {
            "status": "UNRESOLVED_REQUIRED_INPUTS",
            "before": None,
            "after": None,
            "step_order_changed": None,
            "capabilities_changed": None,
        }
    before = _steps(original, resolver, {k: v for k, v in values.items() if k in original_names})
    after = _steps(proposed, resolver, {k: v for k, v in values.items() if k in proposed_names})
    order_before = [(s["id"], s["kind"]) for s in before]
    order_after = [(s["id"], s["kind"]) for s in after]
    return {
        "status": "RESOLVED_NOT_EXECUTED",
        "before": before,
        "after": after,
        "step_order_changed": order_before != order_after,
        "capabilities_changed": before != after
        and (
            [(s["id"], s["capabilities_sha256"]) for s in before]
            != [(s["id"], s["capabilities_sha256"]) for s in after]
        ),
        "step_count_before": len(before),
        "step_count_after": len(after),
    }
