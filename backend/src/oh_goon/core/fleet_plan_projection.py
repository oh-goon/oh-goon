from __future__ import annotations

from oh_goon.core.models.fleet_plan import ResolvedFleetPlan, ResolvedImport
from oh_goon.core.models.goon_plan_source import SharedPlanSource, TeamGoonSource
from oh_goon.core.models.target import FleetPlanTarget


def fleet_plan_logical_slot(target: FleetPlanTarget) -> tuple[str, str]:
    """Stable source slot used to decide which later execution supersedes an older result."""
    source = target.source
    if isinstance(source, SharedPlanSource):
        return (source.kind, source.plan_key)
    if isinstance(source, TeamGoonSource):
        return (source.kind, "goon.yml")
    return (source.kind, target.plan.plan_id)


def atomic_goon_by_step(plan: ResolvedFleetPlan) -> dict[str, ResolvedImport]:
    """Resolve each step to its leaf-most owning Goon once for read projections."""
    imports = plan.imports
    result: dict[str, ResolvedImport] = {}
    child_parent_ids = {
        item.parent_import_id for item in imports if item.parent_import_id is not None
    }
    leaves = tuple(item for item in imports if item.import_id not in child_parent_ids)
    for item in (*leaves, *imports):
        for step_id in item.step_ids:
            result.setdefault(step_id, item)
    return result
