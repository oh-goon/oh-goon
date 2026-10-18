from oh_goon.core.game_tape_storage import project_storage_maintenance
from oh_goon.core.models.storage_maintenance import FleetContentMaintenanceFact, FleetContentMaintenanceReceipt


def _receipt(**overrides):
    values = dict(outcome="NOOP", reason=None, before_bytes=100, after_bytes=100,
        before_objects=2, after_objects=2, removed_bytes=0, removed_objects=0,
        protected_objects=0, idle_protected_objects=0, budget_satisfied=True,
        removal_limit_reached=False,
        recovered_deletions=0, removed_orphan_lifecycle=0,
        max_retained_bytes=1000, max_retained_objects=10, min_idle_ns=100,
        max_removed_bytes=500, max_removed_objects=3)
    values.update(overrides)
    return FleetContentMaintenanceReceipt(**values)


def _fact(operation_id, receipt):
    return FleetContentMaintenanceFact(repository_id="repository-1", operation_id=operation_id, receipt=receipt)


def test_game_tape_storage_projects_bounded_durable_economics():
    first = _receipt(outcome="MAINTAINED", before_bytes=900, after_bytes=600,
        before_objects=9, after_objects=6, removed_bytes=300, removed_objects=3,
        budget_satisfied=False, removal_limit_reached=True)
    second = _receipt(before_bytes=600, after_bytes=600, before_objects=6, after_objects=6)
    storage = project_storage_maintenance((_fact("op-1", first), _fact("op-2", second)))
    assert storage.pressure == "UNDER_BUDGET"
    assert storage.maintenance_cuts == 2
    assert storage.peak_observed_bytes == 900
    assert storage.latest_retained_bytes == 600
    assert storage.removed_bytes == 300
    assert storage.removed_objects == 3
    assert storage.final_budget_satisfied is True


def test_game_tape_storage_projects_refusal_reason():
    receipt = _receipt(outcome="REFUSED", reason="invalid lifecycle metadata",
        before_bytes=0, after_bytes=0, before_objects=0, after_objects=0,
        budget_satisfied=False)
    storage = project_storage_maintenance((_fact("op-1", receipt),))
    assert storage.pressure == "REFUSED"
    assert storage.refusal_reason == "invalid lifecycle metadata"
