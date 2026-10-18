from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.models.storage_maintenance import FleetContentMaintenanceReceipt

if TYPE_CHECKING:
    from oh_goon.integrations.fleet_content_store import FleetContentStore

from oh_goon.integrations.fleet_content_retention import (
    FleetContentRemovalLimits,
    FleetContentRetentionBudget,
    FleetContentRetentionResult,
)

DEFAULT_MAX_RETAINED_BYTES = 8 * 1024 * 1024 * 1024
DEFAULT_MAX_RETAINED_OBJECTS = 512
DEFAULT_MIN_IDLE_NS = 24 * 60 * 60 * 1_000_000_000
DEFAULT_MAX_REMOVED_BYTES_PER_RUN = 1024 * 1024 * 1024
DEFAULT_MAX_REMOVED_OBJECTS_PER_RUN = 8


@dataclass(frozen=True, slots=True)
class FleetContentMaintenancePolicy:
    retention: FleetContentRetentionBudget
    limits: FleetContentRemovalLimits


DEFAULT_FLEET_CONTENT_MAINTENANCE_POLICY = FleetContentMaintenancePolicy(
    retention=FleetContentRetentionBudget(
        max_bytes=DEFAULT_MAX_RETAINED_BYTES,
        max_objects=DEFAULT_MAX_RETAINED_OBJECTS,
        min_idle_ns=DEFAULT_MIN_IDLE_NS,
    ),
    limits=FleetContentRemovalLimits(
        max_removed_bytes=DEFAULT_MAX_REMOVED_BYTES_PER_RUN,
        max_removed_objects=DEFAULT_MAX_REMOVED_OBJECTS_PER_RUN,
    ),
)


def maintenance_receipt(
    result: FleetContentRetentionResult,
    *,
    policy: FleetContentMaintenancePolicy = DEFAULT_FLEET_CONTENT_MAINTENANCE_POLICY,
    recovered_deletions: int = 0,
    removed_orphan_lifecycle: int = 0,
) -> FleetContentMaintenanceReceipt:
    return FleetContentMaintenanceReceipt(
        outcome="MAINTAINED" if result.removed else "NOOP",
        reason=None,
        before_bytes=result.before_bytes,
        after_bytes=result.after_bytes,
        before_objects=result.before_objects,
        after_objects=result.after_objects,
        removed_bytes=result.removed_bytes,
        removed_objects=len(result.removed),
        protected_objects=len(result.protected),
        idle_protected_objects=len(result.idle_protected),
        budget_satisfied=result.budget_satisfied,
        removal_limit_reached=result.removal_limit_reached,
        recovered_deletions=recovered_deletions,
        removed_orphan_lifecycle=removed_orphan_lifecycle,
        max_retained_bytes=policy.retention.max_bytes,
        max_retained_objects=policy.retention.max_objects,
        min_idle_ns=policy.retention.min_idle_ns,
        max_removed_bytes=policy.limits.max_removed_bytes,
        max_removed_objects=policy.limits.max_removed_objects,
    )


def refused_maintenance_receipt(
    reason: str, policy: FleetContentMaintenancePolicy = DEFAULT_FLEET_CONTENT_MAINTENANCE_POLICY
) -> FleetContentMaintenanceReceipt:
    if not reason.strip():
        raise FleetPlanInvalid("Fleet content maintenance refusal reason must be non-empty")
    return FleetContentMaintenanceReceipt(
        outcome="REFUSED",
        reason=reason,
        before_bytes=0,
        after_bytes=0,
        before_objects=0,
        after_objects=0,
        removed_bytes=0,
        removed_objects=0,
        protected_objects=0,
        idle_protected_objects=0,
        budget_satisfied=False,
        removal_limit_reached=False,
        max_retained_bytes=policy.retention.max_bytes,
        max_retained_objects=policy.retention.max_objects,
        min_idle_ns=policy.retention.min_idle_ns,
        max_removed_bytes=policy.limits.max_removed_bytes,
        max_removed_objects=policy.limits.max_removed_objects,
    )


def run_content_maintenance(
    store: "FleetContentStore",
    policy: FleetContentMaintenancePolicy | None = None,
    *,
    now_ns: int | None = None,
) -> FleetContentMaintenanceReceipt:
    selected = DEFAULT_FLEET_CONTENT_MAINTENANCE_POLICY if policy is None else policy
    try:
        crash_recovery = store.reconcile_interrupted_maintenance()
        result = store.prune_to_retention_budget(
            selected.retention, now_ns=now_ns, limits=selected.limits
        )
    except FleetPlanInvalid as exc:
        return refused_maintenance_receipt(str(exc), selected)
    return maintenance_receipt(
        result,
        recovered_deletions=len(crash_recovery.completed_deletions),
        removed_orphan_lifecycle=len(crash_recovery.removed_orphan_lifecycle),
        policy=selected,
    )
