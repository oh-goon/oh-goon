from __future__ import annotations

from collections.abc import Sequence

from oh_goon.core.models.capability import CapabilitySnapshot
from oh_goon.core.models.operation import OperationReport
from oh_goon.core.vocabulary import ResultFreshness


def result_freshness(
    report: OperationReport,
    later_invalidators: Sequence[CapabilitySnapshot],
) -> ResultFreshness:
    """Derive freshness from every later durable mutation-capability fact.

    Normal OperationReports and recovery reconciliation evidence both feed this function.
    A recovered effect that may have changed the repository must invalidate earlier results
    exactly like a normally completed operation with the same admitted capability policy.
    """
    capability_id = report.capability.capability_id
    for capability in later_invalidators:
        if capability.invalidates_all or capability_id in capability.invalidates_capabilities:
            return ResultFreshness.STALE
    return ResultFreshness.CURRENT
