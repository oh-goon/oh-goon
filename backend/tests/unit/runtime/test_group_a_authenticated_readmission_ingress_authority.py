"""M189: strict placement-loss readmission must consult original replay gate first."""
from __future__ import annotations

from unittest.mock import Mock

import pytest

import oh_goon.services.fleet_run_control_plane as control_plane_module
from oh_goon.core.enforcer_lace_trust import TrustProtocolError
from oh_goon.core.errors import ExecutorReadmissionNotAllowed
from oh_goon.services.fleet_run_control_plane import FleetRunControlPlane
from oh_goon.services.fleet_run_lifecycle import FleetRunLifecycle
from tests.unit.runtime.test_executor_readmission import (
    _detail,
    _existing_child,
    _wire_readmission_dependencies,
)


def _manager(*, authenticated: bool, child: object | None = None):
    manager = object.__new__(FleetRunControlPlane)
    parent = _detail(failure_code="EXECUTOR_PLACEMENT_LOST")
    reads: list[str] = []
    _wire_readmission_dependencies(manager, parent, child, reads=reads)
    manager._lifecycle._require_authenticated_fleet = authenticated  # type: ignore[attr-defined]
    manager._lifecycle.require_fleet_prework_available = (  # type: ignore[attr-defined]
        lambda *args, **kwargs: FleetRunLifecycle.require_fleet_prework_available(
            manager._lifecycle, *args, **kwargs,
        )
    )
    manager._start_intent = Mock(side_effect=AssertionError("no fresh admission"))  # type: ignore[method-assign]
    return manager, parent, reads


def test_authenticated_strict_readmission_denied_before_capability_resolution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, _parent, reads = _manager(authenticated=True)
    monkeypatch.setattr(
        control_plane_module, "resolve_bound_capability_intent",
        lambda *args: pytest.fail("capability work preceded the original replay gate"),
    )
    with pytest.raises(TrustProtocolError, match="replay Fleet ingress requires"):
        manager.readmit("old-run", new_fleet_run_id="fresh-child")
    assert reads == ["read"]
    manager._start_intent.assert_not_called()


def test_authenticated_existing_child_stays_read_only_without_reauthorization() -> None:
    manager, parent, reads = _manager(authenticated=True)
    child = _existing_child(parent, fleet_run_id="original-child")
    _wire_readmission_dependencies(manager, parent, child, reads=reads)
    manager._lifecycle.require_fleet_prework_available = Mock(
        side_effect=AssertionError("existing child is already durably admitted")
    )
    manager._start_intent = Mock(side_effect=AssertionError("no competing child"))  # type: ignore[method-assign]
    assert manager.readmit("old-run", new_fleet_run_id="new-client-request") is child.run
    assert reads == ["read"]
    manager._lifecycle.require_fleet_prework_available.assert_not_called()
    manager._start_intent.assert_not_called()


def test_original_prework_owner_receives_exact_captured_lineage_before_resolution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, parent, reads = _manager(authenticated=True)
    calls: list[tuple[object, str, str]] = []

    def reject(target, run_id, *, readmitted_from_run_id):
        calls.append((target, run_id, readmitted_from_run_id))
        raise TrustProtocolError("original lifecycle replay denial")

    manager._lifecycle.require_fleet_prework_available = reject
    monkeypatch.setattr(
        control_plane_module, "resolve_bound_capability_intent",
        lambda *args: pytest.fail("capability work preceded lifecycle authorization"),
    )
    with pytest.raises(TrustProtocolError, match="original lifecycle"):
        manager.readmit("old-run", new_fleet_run_id="child")
    assert calls == [(parent.run.target, "child", "old-run")]
    assert reads == ["read"]


def test_invalid_placement_failure_does_not_invoke_replay_gate() -> None:
    manager, parent, reads = _manager(authenticated=True)
    parent.run.failure.code = "OTHER_FAILURE"
    gate = Mock(side_effect=AssertionError("invalid parent reached replay gate"))
    manager._lifecycle.require_fleet_prework_available = gate
    with pytest.raises(ExecutorReadmissionNotAllowed, match="EXECUTOR_PLACEMENT_LOST"):
        manager.readmit("old-run", new_fleet_run_id="child")
    assert reads == ["read"]
    gate.assert_not_called()


def test_legacy_readmission_preserves_original_capability_and_start_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, parent, reads = _manager(authenticated=False)
    resolved = object()
    monkeypatch.setattr(control_plane_module, "resolve_bound_capability_intent", lambda *args: resolved)
    manager._start_intent = Mock(return_value="legacy-child")  # type: ignore[method-assign]
    assert manager.readmit("old-run", new_fleet_run_id="child") == "legacy-child"
    manager._start_intent.assert_called_once_with(
        resolved,
        repository_scope=parent.run.repository_scope,
        fleet_run_id="child",
        source=parent.run.source,
        readmitted_from_run_id="old-run",
        rerun_from_run_id=None,
    )
    assert reads == ["read"]
