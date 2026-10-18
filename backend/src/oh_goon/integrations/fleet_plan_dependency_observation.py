from __future__ import annotations

from pathlib import Path

from oh_goon.core.models.fleet_plan_observation_steps import ResolvedDependencyInventoryStep
from oh_goon.engine.dependency_observation import observe_python_dependencies
from oh_goon.integrations._fleet_plan_step_outcome import StepOutcome


def execute_dependency_inventory(
    repository: Path, step: ResolvedDependencyInventoryStep
) -> StepOutcome:
    try:
        snapshot = observe_python_dependencies(repository)
    except ValueError as exc:
        return StepOutcome(
            argv=("dependency-inventory",),
            returncode=65,
            stderr=str(exc),
        )
    return StepOutcome(
        argv=("dependency-inventory",),
        returncode=0,
        stdout=(
            f"Observed {len(snapshot.dependencies)} dependency facts from "
            f"{len(snapshot.sources)} source files."
        ),
        observations=(snapshot,),
    )
