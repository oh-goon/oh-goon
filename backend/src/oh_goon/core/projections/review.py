from __future__ import annotations

from oh_goon.core.models.explanation import ReasonCode
from oh_goon.core.models.review import ReviewPreparation
from oh_goon.core.models.run import CancelledFleetRun, CompletedFleetRun, FailedFleetRun
from oh_goon.core.projections.fleet_context import FleetProjectionContext
from oh_goon.core.vocabulary import OperationAction, Verdict


def build_review_preparation(context: FleetProjectionContext) -> ReviewPreparation | None:
    update = context.latest_run_family(OperationAction.UPDATE)
    if update is None:
        return None
    prepared = context.latest_run_family(OperationAction.PREPARE_REVIEW)
    prepared_ids: set[str] = set()
    update_finished_at = (
        update.run.finished_at
        if isinstance(update.run, (CompletedFleetRun, CancelledFleetRun, FailedFleetRun))
        else None
    )
    if (
        update_finished_at is not None
        and prepared is not None
        and prepared.run.target == update.run.target
        and prepared.run.accepted_at >= update_finished_at
    ):
        prepared_ids = {
            report.repository.repository_id
            for report in prepared.operations
            if report.outcome.reason_code is ReasonCode.REVIEW_PREPARED
        }
    reviewable = []
    for report in update.operations:
        current = context.current_updates_by_repository.get(report.repository.repository_id)
        if (
            report.outcome.verdict is Verdict.UPDATED
            and report.repository.repository_id not in prepared_ids
            and current is not None
            and current.is_current_operation(report.provenance.operation_id)
        ):
            reviewable.append(report.repository)
    if not reviewable:
        return None
    return ReviewPreparation(
        update_fleet_run_id=update.run.fleet_run_id,
        target=update.run.target,
        repositories=tuple(reviewable),
    )
