from __future__ import annotations

from oh_goon.core.errors import JournalConflict
from oh_goon.core.models.capability import CapabilitySnapshot
from oh_goon.core.models.run import FleetRunDetail, FleetRunState
from oh_goon.core.models.run_admission import (
    FleetRepositoryScope,
    fleet_readmission_parent,
    fleet_rerun_parent,
)
from oh_goon.core.models.target import OperationTarget
from oh_goon.core.models.scheduling import SchedulingPriority
from oh_goon.core.vocabulary import FleetRunSource


def require_fleet_run_binding(
    run: FleetRunState,
    *,
    source: FleetRunSource,
    capability: CapabilitySnapshot,
    target: OperationTarget | None,
    repository_scope: FleetRepositoryScope,
) -> FleetRunState:
    """Require one durable Fleet-run identity to match the expected structural binding."""
    if run.source is not source:
        raise JournalConflict(
            f"Fleet run identity {run.fleet_run_id} is already bound to another execution source"
        )
    if run.action is not capability.action or run.target != target:
        raise JournalConflict(
            f"Fleet run identity {run.fleet_run_id} is already bound to another operation intent"
        )
    if run.repository_scope != repository_scope:
        raise JournalConflict(
            f"Fleet run identity {run.fleet_run_id} is already bound to another repository scope"
        )
    if run.capability != capability:
        raise JournalConflict(
            f"Fleet run identity {run.fleet_run_id} is already bound to another capability policy"
        )
    return run


def require_fleet_run_detail_binding(
    detail: FleetRunDetail,
    *,
    source: FleetRunSource,
    capability: CapabilitySnapshot,
    target: OperationTarget | None,
    repository_scope: FleetRepositoryScope,
    readmitted_from_run_id: str | None,
    rerun_from_run_id: str | None,
) -> FleetRunDetail:
    """Require exact durable run identity and execution-lineage binding."""
    require_fleet_run_binding(
        detail.run,
        source=source,
        capability=capability,
        target=target,
        repository_scope=repository_scope,
    )
    if fleet_readmission_parent(detail.execution) != readmitted_from_run_id:
        raise JournalConflict(
            f"Fleet run identity {detail.run.fleet_run_id} is already bound to different "
            "re-admission provenance"
        )
    if fleet_rerun_parent(detail.execution) != rerun_from_run_id:
        raise JournalConflict(
            f"Fleet run identity {detail.run.fleet_run_id} is already bound to different "
            "rerun provenance"
        )
    return detail


def require_fleet_run_scheduling_binding(
    detail: FleetRunDetail, requested_priority: SchedulingPriority
) -> None:
    """Reject idempotent replay that attempts to change the original scheduling request."""
    scheduling = detail.scheduling
    original = scheduling.requested_priority if scheduling is not None else SchedulingPriority.NORMAL
    if original is not requested_priority:
        raise JournalConflict(
            f"Fleet run identity {detail.run.fleet_run_id} is already bound to requested priority "
            f"{original.value}, not {requested_priority.value}"
        )
