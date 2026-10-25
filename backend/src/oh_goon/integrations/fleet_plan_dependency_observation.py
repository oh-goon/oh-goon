from __future__ import annotations

from pathlib import Path

from oh_goon.core.models.fleet_plan_observation_steps import ResolvedDependencyInventoryStep
from oh_goon.integrations._fleet_plan_step_outcome import StepOutcome
from oh_goon.integrations.hashmarks.dependencies import observe_locked_dependencies


def execute_dependency_inventory(
    repository: Path, step: ResolvedDependencyInventoryStep
) -> StepOutcome:
    snapshot = observe_locked_dependencies(repository)
    if (
        snapshot.evidence.availability != "available"
        or snapshot.evidence.freshness != "current"
        or snapshot.evidence.completeness != "complete"
    ):
        return StepOutcome(
            completion="in_process",
            argv=("dependency-inventory",),
            returncode=65,
            stderr=snapshot.evidence.reason or "Dependency lock evidence is incomplete",
            observations=(snapshot,),
        )
    return StepOutcome(
        completion="in_process",
        argv=("dependency-inventory",),
        returncode=0,
        stdout=(
            f"Observed {len(snapshot.dependencies)} dependency facts from "
            f"{len(snapshot.sources)} source files."
        ),
        observations=(snapshot,),
    )
