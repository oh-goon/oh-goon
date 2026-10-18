"""Validate phase membership on the existing resolved Goon graph."""

from __future__ import annotations

from typing import TYPE_CHECKING

from oh_goon.core.errors import FleetPlanInvalid

if TYPE_CHECKING:
    from oh_goon.core.models.fleet_plan import ResolvedFleetPlan

PHASES = ("review", "adjudicate", "repair", "regression", "verification")


def agent_review_phases(plan: ResolvedFleetPlan) -> dict[str, tuple[str, ...]]:
    labeled = [edge for edge in plan.imports if edge.phase is not None]
    if not labeled:
        return {}
    selected: dict[str, list[str]] = {phase: [] for phase in PHASES}
    owners = {}
    for edge in labeled:
        assert edge.phase is not None
        for step_id in edge.step_ids:
            if step_id in owners:
                raise FleetPlanInvalid(
                    "Agent phases overlap; label an import or its children, not both"
                )
            owners[step_id] = edge.phase
            selected[edge.phase].append(step_id)
    if set(owners) != {step.step_id for step in plan.steps} or any(
        not ids for ids in selected.values()
    ):
        raise FleetPlanInvalid(
            "Agent loop requires complete review, adjudicate, repair, regression and verification phases"
        )
    order = [PHASES.index(owners[step.step_id]) for step in plan.steps]
    if order != sorted(order):
        raise FleetPlanInvalid(
            "Agent phases must follow review, adjudicate, repair, regression, verification order"
        )
    steps = {step.step_id: step for step in plan.steps}
    for phase in ("review", "adjudicate"):
        expected = "provider-review" if phase == "review" else "provider-adjudicate"
        if any(getattr(steps[item], "harness", None) != expected for item in selected[phase]):
            raise FleetPlanInvalid(f"{phase} phase requires read-only {expected} Agent Steps")
    if len(selected["adjudicate"]) != 1:
        raise FleetPlanInvalid("adjudicate phase requires exactly one Agent Step")
    repair = [steps[item] for item in selected["repair"]]
    if (
        len(repair) != 2
        or getattr(repair[0], "harness", None) != "provider-proposal"
        or repair[1].kind != "command"
    ):
        raise FleetPlanInvalid(
            "repair phase requires one read-only proposal Agent followed by native batch apply"
        )
    for phase in ("regression", "verification"):
        if any(steps[item].kind == "agent" for item in selected[phase]):
            raise FleetPlanInvalid(f"{phase} requires native verification Steps")
    for step in plan.steps:
        if (
            step.when is not None
            or step.retry.max_attempts != 1
            or step.lifecycle.value != "normal"
            or step.on_failure.value != "stop"
            or step.workspace.publish
            or step.cache.publish
        ):
            raise FleetPlanInvalid(
                "Agent loop phases require unconditional single-attempt Steps with native stop-on-failure"
            )
    return {phase: tuple(ids) for phase, ids in selected.items()}
