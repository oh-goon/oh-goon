from __future__ import annotations

from oh_goon.core.models.fleet import FleetRepository, FleetSnapshot, FleetSummary
from oh_goon.core.models.run import FleetRunDetail
from oh_goon.core.projections.fleet_context import FleetProjectionContext
from oh_goon.core.projections.fleet_snapshot_provenance import build_snapshot_provenance
from oh_goon.core.vocabulary import OperationAction, ReadinessState, Verdict


def build_fleet_summary(context: FleetProjectionContext) -> FleetSummary:
    latest_update_fleet_run = context.latest_run_family(OperationAction.UPDATE)
    latest_operations = latest_update_fleet_run.operations if latest_update_fleet_run else []
    return FleetSummary(
        discovered=len(context.repositories),
        included=len(context.included),
        excluded=len(context.repositories) - len(context.included),
        not_checked=sum(
            repo.readiness_state is ReadinessState.NOT_CHECKED for repo in context.included
        ),
        ready_for_update=sum(
            repo.readiness_state is ReadinessState.READY for repo in context.included
        ),
        stale=sum(repo.readiness_state is ReadinessState.STALE for repo in context.included),
        needs_attention=sum(repo.needs_attention for repo in context.included),
        updated=sum(report.outcome.verdict is Verdict.UPDATED for report in latest_operations),
        unchanged=sum(report.outcome.verdict is Verdict.UNCHANGED for report in latest_operations),
        failed=sum(report.outcome.verdict is Verdict.FAILED for report in latest_operations),
    )


def build_fleet_snapshot(
    repositories: list[FleetRepository],
    *,
    durable_revision: int,
    journal_revision: int,
    active_fleet_run: FleetRunDetail | None,
    latest_fleet_runs: list[FleetRunDetail],
) -> FleetSnapshot:
    context = FleetProjectionContext.build(repositories, active_fleet_run, latest_fleet_runs)
    provenance = build_snapshot_provenance(
        context,
        durable_revision=durable_revision,
        journal_revision=journal_revision,
    )
    return FleetSnapshot(
        provenance=provenance,
        repositories=context.repositories,
        active_fleet_run=context.active_fleet_run,
        latest_fleet_runs=context.latest_fleet_runs,
    )
