"""M188: rerun parent identity and original replay gate precede new-Run work."""

from __future__ import annotations

from contextlib import nullcontext
from types import SimpleNamespace
from typing import cast
from unittest.mock import Mock

import pytest

import oh_goon.services.fleet_run_control_plane as rerun_module
from oh_goon.core.enforcer_lace_trust import TrustProtocolError
from oh_goon.core.errors import JournalConflict, StateConflict
from oh_goon.core.models.fleet_plan import ResolvedCommandStep, ResolvedFleetPlan
from oh_goon.core.models.fleet_plan_execution_policy import (
    CicdExecutionPolicy,
    MaintenanceExecutionPolicy,
)
from oh_goon.core.models.fleet_plan_isolation import CicdResourcePolicy
from oh_goon.core.models.fleet_plan_step_contract import PlanStepCapabilities
from oh_goon.core.models.run_admission import DirectFleetExecutionAdmission
from oh_goon.core.models.target import FleetPlanTarget
from oh_goon.core.vocabulary import FleetRunSource, FleetRunStatus, OperationAction
from oh_goon.integrations.run_resource_envelope import ResolvedRunResourceScope
from oh_goon.runtime.execution import ExecutionCoordinator, ExecutionSession, ExecutionTicket
from oh_goon.services.fleet_run_control_plane import FleetRunControlPlane
from oh_goon.services.fleet_run_lifecycle import FleetRunLifecycle
from tests.factories.capabilities import capability_snapshot
from tests.factories.fleet_plan_targets import structural_fleet_plan_target
from tests.factories.run_resources import run_resource_envelope


def _target(mode: str) -> FleetPlanTarget:
    if mode == "strict":
        execution = CicdExecutionPolicy(
            trust_repository_code=True,
            resources=CicdResourcePolicy(cpu_millis=1000, memory_mib=1024, max_processes=256),
        )
    else:
        execution = MaintenanceExecutionPolicy()
    plan = ResolvedFleetPlan.create(
        name="rerun-prework",
        description="",
        execution=execution,
        requirements=(),
        steps=(
            ResolvedCommandStep(
                step_id="noop",
                argv=("true",),
                timeout_seconds=15,
                capabilities=PlanStepCapabilities(),
            ),
        ),
    )
    return structural_fleet_plan_target(plan)


def _manager(
    *,
    requested: str = "parent",
    mode: str = "direct",
    authenticated: bool = True,
    budgeted: bool = False,
):
    manager = object.__new__(FleetRunControlPlane)
    target = _target(mode)
    parent = SimpleNamespace(
        fleet_run_id=requested,
        status=FleetRunStatus.COMPLETED,
        target=target,
        capability=capability_snapshot(OperationAction.EXECUTE_PLAN, target),
        repository_scope=object(),
        source=FleetRunSource.MANUAL,
    )
    reads: list[str] = []

    def read_view():
        reads.append("read")
        return SimpleNamespace(
            required_run_detail=lambda _key: SimpleNamespace(
                run=parent,
                execution=DirectFleetExecutionAdmission(
                    resource_envelope=run_resource_envelope(requested) if budgeted else None
                ),
            )
        )

    manager._journal = SimpleNamespace(read_view=read_view)  # type: ignore[attr-defined]
    lifecycle = SimpleNamespace(
        _require_authenticated_fleet=authenticated,
        _standard_local_fleet=False,
        serialize_admission=lambda _key: nullcontext(),
    )
    lifecycle.require_fleet_prework_available = (  # type: ignore[attr-defined]
        lambda *a, **kw: FleetRunLifecycle.require_fleet_prework_available(lifecycle, *a, **kw)
    )
    manager._lifecycle = lifecycle  # type: ignore[attr-defined]
    manager._admission = Mock()  # type: ignore[attr-defined]
    manager._start_intent = Mock(  # type: ignore[method-assign]
        side_effect=AssertionError("rerun must not start work")
    )
    return manager, parent, reads


@pytest.mark.parametrize("mode", ["direct", "strict"])
def test_authenticated_plan_rerun_denied_before_capability_resolution_or_scope(
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
) -> None:
    manager, _parent, reads = _manager(mode=mode)
    monkeypatch.setattr(
        rerun_module,
        "resolve_bound_capability_intent",
        lambda *a: pytest.fail("capability resolution must not preempt replay authority"),
    )
    monkeypatch.setattr(
        rerun_module,
        "rerun_repository_scope",
        lambda *a: pytest.fail("scope derivation must not precede replay authority"),
    )
    with pytest.raises(TrustProtocolError, match="replay Fleet ingress requires"):
        manager.rerun("parent", new_fleet_run_id="child")
    assert reads == ["read"]
    manager._admission.plan.assert_not_called()
    manager._start_intent.assert_not_called()


@pytest.mark.parametrize("mode", ["direct", "strict"])
def test_foreign_run_projection_rejected_before_any_prework_or_capability(
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
) -> None:
    manager, parent, reads = _manager(mode=mode)
    parent.fleet_run_id = "foreign-parent"
    gate = Mock(side_effect=AssertionError("foreign Run must not reach prework gate"))
    manager._lifecycle.require_fleet_prework_available = gate
    monkeypatch.setattr(
        rerun_module,
        "resolve_bound_capability_intent",
        lambda *a: pytest.fail("foreign Run must not resolve authority"),
    )
    with pytest.raises(JournalConflict, match="enrolled Run identity"):
        manager.rerun("parent", new_fleet_run_id="child")
    assert reads == ["read"]
    gate.assert_not_called()
    manager._start_intent.assert_not_called()


def test_authenticated_deployment_keeps_nonplan_rerun_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, parent, reads = _manager()
    parent.target = None
    parent.capability = capability_snapshot(OperationAction.INIT)
    resolved = SimpleNamespace(definition=SimpleNamespace(rerun_supported=True))
    monkeypatch.setattr(rerun_module, "resolve_bound_capability_intent", lambda *a: resolved)
    monkeypatch.setattr(rerun_module, "rerun_repository_scope", lambda *a: "preserved-scope")
    manager._start_intent = Mock(return_value="existing-native-path")  # type: ignore[method-assign]
    assert manager.rerun("parent", new_fleet_run_id="child") == "existing-native-path"
    manager._start_intent.assert_called_once_with(
        resolved,
        repository_scope="preserved-scope",
        fleet_run_id="child",
        source=FleetRunSource.MANUAL,
        readmitted_from_run_id=None,
        rerun_from_run_id="parent",
    )
    assert reads == ["read"]


def test_legacy_plan_rerun_keeps_original_path(monkeypatch: pytest.MonkeyPatch) -> None:
    manager, _, reads = _manager(authenticated=False)
    resolved = SimpleNamespace(definition=SimpleNamespace(rerun_supported=True))
    monkeypatch.setattr(rerun_module, "resolve_bound_capability_intent", lambda *a: resolved)
    monkeypatch.setattr(rerun_module, "rerun_repository_scope", lambda *a: "preserved-scope")
    manager._start_intent = Mock(return_value="existing-native-path")  # type: ignore[method-assign]
    assert manager.rerun("parent", new_fleet_run_id="child") == "existing-native-path"
    assert manager._start_intent.call_count == 1
    assert reads == ["read"]


def test_budgeted_history_rerun_requires_fresh_native_envelope_before_new_work(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolved = SimpleNamespace(definition=SimpleNamespace(rerun_supported=True))
    monkeypatch.setattr(rerun_module, "resolve_bound_capability_intent", lambda *a: resolved)
    monkeypatch.setattr(rerun_module, "rerun_repository_scope", lambda *a: "preserved-scope")
    ordinary, _, _ = _manager(authenticated=False)
    ordinary._start_intent = Mock(return_value="existing-native-path")
    assert ordinary.rerun("parent", new_fleet_run_id="child") == "existing-native-path"

    budgeted, _, reads = _manager(authenticated=False, budgeted=True)
    budgeted._lifecycle.require_fleet_prework_available = Mock(
        side_effect=AssertionError("budget replay cannot reach new admission")
    )
    monkeypatch.setattr(
        rerun_module,
        "resolve_bound_capability_intent",
        lambda *a: pytest.fail("budget replay resolved new executable intent"),
    )
    with pytest.raises(StateConflict, match="fresh native envelope"):
        budgeted.rerun("parent", new_fleet_run_id="child")
    assert reads == ["read"]
    budgeted._lifecycle.require_fleet_prework_available.assert_not_called()
    budgeted._admission.plan.assert_not_called()
    budgeted._start_intent.assert_not_called()


@pytest.mark.parametrize("foreign", (False, True), ids=("original-run", "foreign-run"))
def test_native_resource_scope_activation_keeps_the_original_run_identity(
    monkeypatch: pytest.MonkeyPatch, foreign: bool
) -> None:
    import oh_goon.integrations.run_resource_envelope as native_resources

    evidence = run_resource_envelope()
    lifecycle = object.__new__(FleetRunLifecycle)
    lifecycle._run_resource_scope = cast(
        ResolvedRunResourceScope, SimpleNamespace(fleet_run_id=evidence.fleet_run_id)
    )
    lifecycle._run_resource_envelope = evidence
    # Native scope conformance is tested by its owner. This seam exercises the
    # actual activation consumer's comparison with the opaque owner's Run ID.
    monkeypatch.setattr(
        native_resources, "verify_current_run_resource_scope", lambda *a, **kw: evidence
    )
    session = cast(
        ExecutionSession,
        SimpleNamespace(
            require_fleet_run_id=lambda: "another-run" if foreign else evidence.fleet_run_id
        ),
    )
    activate = Mock(return_value=session)
    lifecycle._execution = cast(ExecutionCoordinator, SimpleNamespace(activate=activate))
    ticket = cast(ExecutionTicket, object())
    if foreign:
        with pytest.raises(StateConflict, match="cannot activate another Run"):
            lifecycle.activate(ticket)
    else:
        assert lifecycle.activate(ticket) is session
    activate.assert_called_once_with(ticket)


def test_self_rerun_does_not_read_journal_or_invoke_gate() -> None:
    manager, _, reads = _manager()
    with pytest.raises(Exception, match="own identity"):
        manager.rerun("parent", new_fleet_run_id="parent")
    assert reads == []
