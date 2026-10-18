from __future__ import annotations

from datetime import datetime

from oh_goon.core.models.fleet_plan_evidence import FleetPlanResult
from oh_goon.core.models.operation import OperationReport


def repository_time_bounds(
    report: OperationReport,
    result: FleetPlanResult | None,
) -> tuple[datetime, datetime]:
    """Use canonical step wall-clock evidence when available, otherwise operation bounds."""

    if result is None:
        return report.provenance.started_at, report.provenance.finished_at
    starts = tuple(step.started_at for step in result.steps if step.started_at is not None)
    finishes = tuple(step.finished_at for step in result.steps if step.finished_at is not None)
    started_at = min(starts) if starts else report.provenance.started_at
    finished_at = max(finishes) if finishes else report.provenance.finished_at
    return started_at, finished_at
