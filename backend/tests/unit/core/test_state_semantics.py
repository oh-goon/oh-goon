from oh_goon.core.capability_registry import capability_for
from oh_goon.core.vocabulary import (
    FleetRunStatus,
    FleetVerdict,
    OperationAction,
    ReadinessState,
    Verdict,
)


def test_verdict_attention_semantics_are_canonical() -> None:
    assert Verdict.FAILED.needs_attention is True
    assert Verdict.MANUAL_ACTION_REQUIRED.needs_attention is True
    assert Verdict.UPDATED.needs_attention is False
    assert Verdict.READY.successful is True


def test_fleet_verdict_semantics_are_canonical() -> None:
    assert FleetVerdict.ATTENTION_REQUIRED.needs_attention is True
    assert FleetVerdict.READY.successful is True
    assert FleetVerdict.READY_WITH_EXCLUSIONS.successful is True
    assert FleetVerdict.UNKNOWN.successful is False


def test_readiness_update_gate_is_canonical() -> None:
    assert ReadinessState.READY.can_update is True
    assert ReadinessState.STALE.can_update is False
    assert ReadinessState.MANUAL_ACTION_REQUIRED.needs_attention is True


def test_operation_capabilities_are_registry_owned() -> None:
    init = capability_for(OperationAction.INIT)
    plan = capability_for(OperationAction.PLAN)
    update = capability_for(OperationAction.UPDATE)
    assert init.fleet_supported is True
    assert init.parallel_safe is True
    assert init.requirements == ()
    assert [item.capability_id for item in plan.requirements] == ["initialize"]
    assert update.parallel_safe is False


def test_run_terminality_is_canonical() -> None:
    assert FleetRunStatus.COMPLETED.terminal is True
    assert FleetRunStatus.FAILED.terminal is True
    assert FleetRunStatus.RUNNING.terminal is False
