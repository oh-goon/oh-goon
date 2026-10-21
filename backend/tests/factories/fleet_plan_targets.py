from __future__ import annotations

from oh_goon.core.execution_lock_identity import build_execution_lock
from oh_goon.core.models.execution_identity import ExecutionIdentity
from oh_goon.core.models.fleet_plan import ResolvedFleetPlan
from oh_goon.core.models.goon_plan_source import FleetPlanSource, PlanFileSource
from oh_goon.core.models.target import FleetPlanTarget


def structural_execution_identity(plan: ResolvedFleetPlan) -> ExecutionIdentity:
    """Build consistent identity for model-only tests that do not claim lock verification."""
    return ExecutionIdentity(digest=build_execution_lock(plan).digest)


def structural_fleet_plan_target(
    plan: ResolvedFleetPlan,
    *,
    source: FleetPlanSource | None = None,
) -> FleetPlanTarget:
    """Construct a consistent target only for model/projection tests below execution."""
    return FleetPlanTarget(
        kind="fleet_plan",
        plan=plan,
        source=source
        or PlanFileSource(
            sha256=(plan.configuration.root_source_sha256 if plan.configuration else None)
            or "0" * 64
        ),
        identity=structural_execution_identity(plan),
    )
