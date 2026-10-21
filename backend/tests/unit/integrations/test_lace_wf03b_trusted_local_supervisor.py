"""WF-03B: host-process backend cannot become an unconsented strict downgrade."""

from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.execution_environment import configured_environment_backend
from oh_goon.core.models.fleet_plan_execution_policy import CicdExecutionPolicy
from oh_goon.core.models.fleet_plan_isolation import CicdResourcePolicy
from oh_goon.core.models.lace_trusted_local import TrustedLocalRunConsent
from oh_goon.engine.command import CommandResult
from oh_goon.integrations.fleet_plan_process_isolation import (
    IsolatedProcessRequest,
    TrustedLocalRunScope,
    isolation_backend_evidence,
)
from oh_goon.integrations.fleet_plan_trusted_local_isolation import TrustedLocalProcessIsolation

RUN = "fleet-test"
DIGEST = "a" * 64


def _setup(tmp_path: Path, *, consent=True, effect=True, scope=True, **changes):
    backend = TrustedLocalProcessIsolation()
    authority = Mock() if effect else None
    run_scope = (
        TrustedLocalRunScope(
            fleet_run_id=RUN,
            execution_digest=DIGEST,
            executor_id="owner-local",
            executor_generation=1,
            source="manual",
            all_repositories=False,
            repository_count=1,
            unattended=False,
            execution=CicdExecutionPolicy(),
        )
        if scope
        else None
    )
    grant = (
        TrustedLocalRunConsent(
            fleet_run_id=RUN,
            execution_digest=DIGEST,
            backend_id=backend.capabilities.backend_id,
            backend_configuration_id=backend.configuration_identity(),
            executor_id="owner-local",
            executor_generation=1,
            operator_interactive=True,
            explicitly_acknowledged_ambient_host_access=True,
        )
        if consent
        else None
    )
    attempt = SimpleNamespace(
        isolation_backend=isolation_backend_evidence(backend),
        nested_strict_execution=False,
        egress=SimpleNamespace(mode="host_unrestricted"),
        resources=CicdResourcePolicy(),
    )
    request = IsolatedProcessRequest(
        attempt=attempt,
        argv=("/usr/bin/printf", "hello"),
        cwd=tmp_path,
        env={},
        fleet_run_id=RUN,
        trusted_local_consent=grant,
        trusted_local_scope=run_scope,
        trusted_local_effect_authority=authority,
    )
    return backend, replace(request, **changes), authority


def test_capabilities_tell_truth_and_configuration_is_stable():
    first, second = TrustedLocalProcessIsolation(), TrustedLocalProcessIsolation()
    caps = first.capabilities
    assert caps.backend_id == "trusted-local"
    assert not caps.private_filesystem and not caps.private_process_tree
    assert not caps.enforced_network_policy and not caps.remote_execution
    assert not caps.enforced_cpu_limit and not caps.enforced_memory_limit
    assert not caps.enforced_process_limit and caps.exact_argv
    assert first.configuration_identity() == second.configuration_identity()


@pytest.mark.parametrize("missing", ["consent", "effect", "scope"])
def test_no_issuer_or_no_exact_consent_means_no_effect(tmp_path, monkeypatch, missing):
    import oh_goon.integrations.fleet_plan_trusted_local_isolation as module

    spawn = Mock()
    monkeypatch.setattr(module, "run_command", spawn)
    backend, request, _owner = _setup(tmp_path, **{missing: False})
    with pytest.raises(FleetPlanInvalid, match="requires enrolled Run consent"):
        backend.execute(request, timeout=5, cancel_requested=None)
    spawn.assert_not_called()


@pytest.mark.parametrize(
    "change",
    [
        {"fleet_run_id": "different"},
        {"read_only_workspace": object()},
    ],
)
def test_wrong_identity_or_private_workspace_denied_before_launch(tmp_path, monkeypatch, change):
    import oh_goon.integrations.fleet_plan_trusted_local_isolation as module

    spawn = Mock()
    monkeypatch.setattr(module, "run_command", spawn)
    backend, request, owner = _setup(tmp_path, **change)
    with pytest.raises((FleetPlanInvalid, ValueError, TypeError)):
        backend.execute(request, timeout=5, cancel_requested=None)
    owner.require_effect_authority.assert_not_called()
    spawn.assert_not_called()


@pytest.mark.parametrize(
    "attempt_change",
    [
        {"nested_strict_execution": True},
        {"egress": SimpleNamespace(mode="none")},
        {"egress": SimpleNamespace(mode="cidr_allowlist")},
        {"resources": CicdResourcePolicy(memory_mib=128)},
    ],
)
def test_no_false_kernel_guarantees(tmp_path, monkeypatch, attempt_change):
    import oh_goon.integrations.fleet_plan_trusted_local_isolation as module

    spawn = Mock()
    monkeypatch.setattr(module, "run_command", spawn)
    backend, request, owner = _setup(tmp_path)
    for name, value in attempt_change.items():
        setattr(request.attempt, name, value)
    with pytest.raises(FleetPlanInvalid, match="cannot"):
        backend.execute(request, timeout=5, cancel_requested=None)
    owner.require_effect_authority.assert_not_called()
    spawn.assert_not_called()


def test_stale_executor_consent_does_not_reach_effect_owner(tmp_path, monkeypatch):
    import oh_goon.integrations.fleet_plan_trusted_local_isolation as module

    spawn = Mock()
    monkeypatch.setattr(module, "run_command", spawn)
    backend, request, owner = _setup(tmp_path)
    assert request.trusted_local_scope is not None
    request = replace(
        request, trusted_local_scope=replace(request.trusted_local_scope, executor_generation=2)
    )
    with pytest.raises(FleetPlanInvalid, match="stale"):
        backend.execute(request, timeout=5, cancel_requested=None)
    owner.require_effect_authority.assert_not_called()
    spawn.assert_not_called()


def test_effect_owner_must_revalidate_before_supervision(tmp_path, monkeypatch):
    import oh_goon.integrations.fleet_plan_trusted_local_isolation as module

    spawn = Mock()
    monkeypatch.setattr(module, "run_command", spawn)
    backend, request, owner = _setup(tmp_path)
    owner.require_effect_authority.side_effect = FleetPlanInvalid("Run revoked")
    with pytest.raises(FleetPlanInvalid, match="revoked"):
        backend.execute(request, timeout=5, cancel_requested=None)
    spawn.assert_not_called()


def test_same_supervisor_receives_exact_argv_and_cancellation(tmp_path, monkeypatch):
    import oh_goon.integrations.fleet_plan_trusted_local_isolation as module

    observed = []
    expected = CommandResult(returncode=0, stdout="hello", stderr="")

    def original_supervisor(*argv, **kwargs):
        observed.append((argv, kwargs))
        return expected

    monkeypatch.setattr(module, "run_command", original_supervisor)
    backend, request, owner = _setup(tmp_path)
    cancel = Mock(return_value=False)
    result = backend.execute(request, timeout=6.0, cancel_requested=cancel)
    assert result is expected
    owner.require_effect_authority.assert_called_once_with(request)
    assert len(observed) == 1
    assert observed[0][0] == request.argv
    kwargs = observed[0][1]
    assert kwargs["cwd"] == tmp_path and kwargs["env"] == {}
    assert kwargs["timeout"] == 6.0 and kwargs["cancel_requested"] is cancel
    assert kwargs["require_process_group_quiescence"] is True
    assert kwargs["parent_death_signal"] is True
    assert kwargs["max_capture_bytes_per_stream"] == 1024 * 1024


def test_deployment_inventory_still_rejects_undeployed_backend():
    with pytest.raises(FleetPlanInvalid, match="Unsupported"):
        configured_environment_backend({"environment": {"backend": "trusted-local"}})


def test_backend_refuses_untrusted_plan_and_denied_egress():
    backend = TrustedLocalProcessIsolation()
    trusted = CicdExecutionPolicy()

    def plan(execution, mode):
        return SimpleNamespace(
            execution=execution,
            steps=[
                SimpleNamespace(capabilities=SimpleNamespace(egress=SimpleNamespace(mode=mode)))
            ],
        )

    with pytest.raises(FleetPlanInvalid, match="trusted CI/CD"):
        backend.require_available(
            plan(CicdExecutionPolicy(trust_repository_code=False), "host_unrestricted")
        )
    with pytest.raises(FleetPlanInvalid, match="host_unrestricted"):
        backend.require_available(plan(trusted, "none"))
    with pytest.raises(FleetPlanInvalid, match="limits"):
        backend.require_available(
            plan(
                CicdExecutionPolicy(resources=CicdResourcePolicy(cpu_millis=400)),
                "host_unrestricted",
            )
        )
    backend.require_available(plan(trusted, "host_unrestricted"))


def test_admitted_host_command_runs_through_real_supervisor(tmp_path):
    """Hosted adapter smoke test, NOT authenticated Fleet admission or native certification."""
    backend, request, owner = _setup(tmp_path)
    request = replace(
        request,
        argv=(sys.executable, "-S", "-c", "print('tl-1-smoke', end='')"),
    )
    result = backend.execute(request, timeout=5.0, cancel_requested=lambda: False)
    assert result.returncode == 0 and result.stdout == "tl-1-smoke"
    owner.require_effect_authority.assert_called_once_with(request)
    assert result.supervisor.final_process_group_quiescent


def test_real_host_command_failure_is_not_reclassified(tmp_path):
    import shutil

    executable = shutil.which("false")
    if executable is None:
        pytest.skip("false executable not available")
    backend, request, owner = _setup(tmp_path)
    request = replace(request, argv=(executable,))
    result = backend.execute(request, timeout=5.0, cancel_requested=lambda: False)
    assert result.returncode != 0
    owner.require_effect_authority.assert_called_once_with(request)


def test_real_host_command_cancellation_uses_original_supervisor(tmp_path):
    import shutil

    from oh_goon.engine.command import CommandCancelledError

    executable = shutil.which("sleep")
    if executable is None:
        pytest.skip("sleep executable not available")
    backend, request, owner = _setup(tmp_path)
    request = replace(request, argv=(executable, "10"))
    with pytest.raises(CommandCancelledError) as error:
        backend.execute(request, timeout=5.0, cancel_requested=lambda: True)
    assert error.value.supervisor.process_group_cleanup_requested
    assert error.value.supervisor.final_process_group_quiescent
    owner.require_effect_authority.assert_called_once_with(request)
