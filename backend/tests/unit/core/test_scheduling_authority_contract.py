from oh_goon.api.routes.fleet_executors import QueueReprioritizeRequest
from oh_goon.api.schemas.fleet import FleetRunChildRequest
from oh_goon.core.models.execution_lock_models import ExecutionLock
from oh_goon.core.models.scheduling import SchedulingPriority


def test_priority_is_not_part_of_goon_lock_identity() -> None:
    fields = set(ExecutionLock.model_fields)
    assert not fields & {"priority", "requested_priority", "admitted_priority", "queue_seq"}


def test_child_run_request_cannot_implicitly_inherit_priority() -> None:
    assert "requested_priority" not in FleetRunChildRequest.model_fields
    assert "admitted_priority" not in FleetRunChildRequest.model_fields


def test_reprioritization_request_requires_cas_and_reason() -> None:
    request = QueueReprioritizeRequest(
        admitted_priority=SchedulingPriority.HIGH,
        expected_scheduling_revision=7,
        reason="operator escalation",
    )
    assert request.expected_scheduling_revision == 7
    assert request.reason == "operator escalation"


def test_direct_execution_cannot_silently_ignore_non_normal_priority() -> None:
    import pytest

    from oh_goon.core.errors import FleetPlanInvalid
    from oh_goon.services.fleet_run_lifecycle import _require_direct_priority_supported

    _require_direct_priority_supported(SchedulingPriority.NORMAL)
    with pytest.raises(FleetPlanInvalid, match="cannot silently ignore requested priority"):
        _require_direct_priority_supported(SchedulingPriority.HIGH)
