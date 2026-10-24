from __future__ import annotations

from typing import Literal

from oh_goon.core.models.storage_maintenance import FleetContentMaintenanceFact, GameTapeStorage


def project_storage_maintenance(facts: tuple[FleetContentMaintenanceFact, ...]) -> GameTapeStorage:
    if not facts:
        return GameTapeStorage(pressure="NO_EVIDENCE", maintenance_cuts=0)
    receipts = tuple(item.receipt for item in facts)
    latest = receipts[-1]
    pressure = _storage_pressure(latest.outcome, latest.budget_satisfied)
    census_known = latest.outcome != "REFUSED"
    observed = [r.before_bytes for r in receipts if r.outcome != "REFUSED"]
    observed += [r.after_bytes for r in receipts if r.outcome != "REFUSED"]
    return GameTapeStorage(
        pressure=pressure,
        maintenance_cuts=len(receipts),
        peak_observed_bytes=max(observed) if observed else None,
        latest_retained_bytes=latest.after_bytes if census_known else None,
        latest_retained_objects=latest.after_objects if census_known else None,
        removed_bytes=sum(r.removed_bytes for r in receipts),
        removed_objects=sum(r.removed_objects for r in receipts),
        recovered_deletions=sum(r.recovered_deletions for r in receipts),
        removed_orphan_lifecycle=sum(r.removed_orphan_lifecycle for r in receipts),
        final_budget_satisfied=latest.budget_satisfied if census_known else None,
        removal_limit_reached=latest.removal_limit_reached,
        refusal_reason=latest.reason if latest.outcome == "REFUSED" else None,
    )


def _storage_pressure(
    outcome: str, budget_satisfied: bool
) -> Literal["REFUSED", "UNDER_BUDGET", "OVER_BUDGET"]:
    if outcome == "REFUSED":
        return "REFUSED"
    return "UNDER_BUDGET" if budget_satisfied else "OVER_BUDGET"
