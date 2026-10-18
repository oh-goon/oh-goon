from __future__ import annotations

from datetime import datetime

from oh_goon.core.models.operation import OperationExecution, OperationReport
from oh_goon.core.models.policy import PolicySnapshot
from oh_goon.core.models.provenance import OperationProvenance, RepositoryStateFingerprint
from oh_goon.core.time import utc_now
from oh_goon.engine.operation_execution_context import OperationContext


def provenance(
    context: OperationContext,
    started_at: datetime,
    before: RepositoryStateFingerprint | None,
    after: RepositoryStateFingerprint | None,
    policy: PolicySnapshot | None,
) -> OperationProvenance:
    return OperationProvenance(
        operation_id=context.attempt.operation_id,
        fleet_run_id=context.attempt.fleet_run_id,
        started_at=started_at,
        finished_at=utc_now(),
        before=before,
        after=after,
    )


def execution(stdout: str, stderr: str, report: OperationReport) -> OperationExecution:
    return OperationExecution(stdout=stdout, stderr=stderr, report=report)
