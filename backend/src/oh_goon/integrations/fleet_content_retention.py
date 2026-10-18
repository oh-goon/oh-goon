from __future__ import annotations

import sys
import time
from dataclasses import dataclass
from pathlib import Path

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.path_layouts import ContentStorePathLayout
from oh_goon.integrations.fleet_content_object_lifecycle import (
    FleetContentObjectLifecycle,
    read_object_lifecycle,
)
from oh_goon.integrations.fleet_content_crash_reconciliation import complete_deletion_locked
from oh_goon.integrations.fleet_content_store_lifecycle import (
    is_object_id,
    reconcile_leases,
    referenced_objects,
    store_lock,
)


@dataclass(frozen=True, slots=True)
class FleetContentRetentionBudget:
    max_bytes: int
    max_objects: int
    min_idle_ns: int

    def __post_init__(self) -> None:
        if self.max_bytes < 0 or self.max_objects < 0 or self.min_idle_ns < 0:
            raise FleetPlanInvalid("Fleet content retention budgets must be non-negative")


@dataclass(frozen=True, slots=True)
class FleetContentRemovalLimits:
    max_removed_bytes: int
    max_removed_objects: int

    def __post_init__(self) -> None:
        if self.max_removed_bytes < 0 or self.max_removed_objects < 0:
            raise FleetPlanInvalid("Fleet content maintenance limits must be non-negative")


@dataclass(frozen=True, slots=True)
class FleetContentRetentionResult:
    removed: tuple[str, ...]
    protected: tuple[str, ...]
    idle_protected: tuple[str, ...]
    before_bytes: int
    after_bytes: int
    before_objects: int
    after_objects: int
    removed_bytes: int
    byte_budget_satisfied: bool
    object_budget_satisfied: bool
    removal_limit_reached: bool

    @property
    def budget_satisfied(self) -> bool:
        return self.byte_budget_satisfied and self.object_budget_satisfied


def prune_to_retention_budget(
    root: Path,
    layout: ContentStorePathLayout,
    lock_path: Path,
    budget: FleetContentRetentionBudget,
    *,
    now_ns: int | None = None,
    limits: FleetContentRemovalLimits | None = None,
) -> FleetContentRetentionResult:
    """Remove oldest idle unreachable objects only until explicit budgets are satisfied.

    Lifecycle timestamps are retention evidence only. Every extant object must have a
    valid lifecycle record before budget pruning can proceed. Unclassified bytes receive
    no retention or cleanup authority.
    """

    if sys.platform != "linux":
        raise FleetPlanInvalid(
            "Race-safe Fleet content retention pruning currently requires Linux process identity"
        )
    observed_now = time.time_ns() if now_ns is None else now_ns
    if observed_now < 0:
        raise FleetPlanInvalid("Fleet content retention clock must be non-negative")

    with store_lock(lock_path):
        objects = _inventory_objects(root, layout)
        protected = referenced_objects(root) | reconcile_leases(root)
        before_bytes = sum(item.lifecycle.byte_count for item in objects)
        before_objects = len(objects)
        remaining_bytes = before_bytes
        remaining_objects = before_objects
        candidates, idle_protected = _eligible_candidates(
            objects, protected=protected, observed_now=observed_now, min_idle_ns=budget.min_idle_ns
        )
        removal = _remove_candidates(
            root,
            candidates,
            remaining_bytes=remaining_bytes,
            remaining_objects=remaining_objects,
            budget=budget,
            limits=limits,
        )
        remaining_bytes = removal.remaining_bytes
        remaining_objects = removal.remaining_objects

        return FleetContentRetentionResult(
            removed=removal.removed,
            protected=tuple(sorted(protected)),
            idle_protected=tuple(sorted(idle_protected)),
            before_bytes=before_bytes,
            after_bytes=remaining_bytes,
            before_objects=before_objects,
            after_objects=remaining_objects,
            removed_bytes=removal.removed_bytes,
            byte_budget_satisfied=remaining_bytes <= budget.max_bytes,
            object_budget_satisfied=remaining_objects <= budget.max_objects,
            removal_limit_reached=removal.limit_reached,
        )


@dataclass(frozen=True, slots=True)
class _RetainedObject:
    object_id: str
    path: Path
    lifecycle: FleetContentObjectLifecycle


@dataclass(frozen=True, slots=True)
class _RemovalProgress:
    removed: tuple[str, ...]
    removed_bytes: int
    remaining_bytes: int
    remaining_objects: int
    limit_reached: bool


def _eligible_candidates(
    objects: tuple[_RetainedObject, ...],
    *,
    protected: set[str],
    observed_now: int,
    min_idle_ns: int,
) -> tuple[list[_RetainedObject], list[str]]:
    candidates: list[_RetainedObject] = []
    idle_protected: list[str] = []
    for item in objects:
        if item.object_id in protected:
            continue
        idle_ns = max(0, observed_now - item.lifecycle.last_used_at_ns)
        if idle_ns < min_idle_ns:
            idle_protected.append(item.object_id)
            continue
        candidates.append(item)
    candidates.sort(
        key=lambda item: (
            item.lifecycle.last_used_at_ns,
            item.lifecycle.created_at_ns,
            item.object_id,
        )
    )
    return candidates, idle_protected


def _remove_candidates(
    root: Path,
    candidates: list[_RetainedObject],
    *,
    remaining_bytes: int,
    remaining_objects: int,
    budget: FleetContentRetentionBudget,
    limits: FleetContentRemovalLimits | None,
) -> _RemovalProgress:
    removed: list[str] = []
    removed_bytes = 0
    for item in candidates:
        if remaining_bytes <= budget.max_bytes and remaining_objects <= budget.max_objects:
            break
        if _removal_limit_blocks(item, removed, removed_bytes, limits):
            return _RemovalProgress(
                tuple(removed), removed_bytes, remaining_bytes, remaining_objects, True
            )
        complete_deletion_locked(root, item.path, item.object_id)
        removed.append(item.object_id)
        removed_bytes += item.lifecycle.byte_count
        remaining_bytes -= item.lifecycle.byte_count
        remaining_objects -= 1
    return _RemovalProgress(
        tuple(removed), removed_bytes, remaining_bytes, remaining_objects, False
    )


def _removal_limit_blocks(
    item: _RetainedObject,
    removed: list[str],
    removed_bytes: int,
    limits: FleetContentRemovalLimits | None,
) -> bool:
    if limits is None:
        return False
    return (
        len(removed) >= limits.max_removed_objects
        or removed_bytes + item.lifecycle.byte_count > limits.max_removed_bytes
    )


def _inventory_objects(root: Path, layout: ContentStorePathLayout) -> tuple[_RetainedObject, ...]:
    inventory: list[_RetainedObject] = []
    for kind in ("tree", "file"):
        for path in sorted(layout.objects(kind).iterdir(), key=lambda item: item.name):
            object_id = f"{kind}-sha256-{path.name}"
            if not is_object_id(object_id):
                raise FleetPlanInvalid(
                    f"Fleet content retention refused unclassified object entry: {path.name}"
                )
            lifecycle = read_object_lifecycle(root, object_id)
            if lifecycle is None:
                raise FleetPlanInvalid(
                    f"Fleet content retention refused object without lifecycle authority: {object_id}"
                )
            if lifecycle.object_id != object_id:
                raise FleetPlanInvalid(
                    f"Fleet content retention lifecycle identity mismatch: {object_id}"
                )
            inventory.append(_RetainedObject(object_id, path, lifecycle))
    return tuple(inventory)
