"""WF-03C2C: original admitted TL-1 facts reach Step requests, never mint authority."""
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.engine.command import CommandResult
from oh_goon.core.models.fleet_plan_execution_policy import CicdExecutionPolicy, ExecutionPurpose
from oh_goon.core.models.lace_trusted_local import TrustedLocalRunConsent, TrustedLocalRunScope
from oh_goon.integrations.fleet_plan_process_isolation import isolation_backend_evidence
from oh_goon.integrations.fleet_plan_run import PreparedPlanRun
from oh_goon.integrations.fleet_plan_run_execution import FleetPlanRunExecutor
from oh_goon.integrations.fleet_plan_step_execution import _isolated_command_runner
from oh_goon.integrations.fleet_plan_trusted_local_isolation import TrustedLocalProcessIsolation


def _authority():
    backend = TrustedLocalProcessIsolation()
    scope = TrustedLocalRunScope(
        fleet_run_id="run-one", execution_digest="sha256:" + "a" * 64,
        executor_id="exec-one", executor_generation=2, source="manual",
        all_repositories=False, repository_count=1, unattended=False,
        execution=CicdExecutionPolicy(),
    )
    consent = TrustedLocalRunConsent(
        fleet_run_id=scope.fleet_run_id, execution_digest=scope.execution_digest,
        backend_id="trusted-local", backend_configuration_id=backend.configuration_identity(),
        executor_id=scope.executor_id, executor_generation=scope.executor_generation,
        operator_interactive=True, explicitly_acknowledged_ambient_host_access=True,
    )
    return backend, scope, consent


@pytest.mark.parametrize("missing", ["consent", "scope"])
def test_preparation_refuses_incomplete_admission_before_workspace(tmp_path: Path, missing: str):
    backend, scope, consent = _authority()
    args = dict(
        repository=None, plan=None, source_authority=None,
        process_isolation=backend, isolation_backend=isolation_backend_evidence(backend),
        path_authority=None, run_parent=tmp_path,
        trusted_local_consent=None if missing == "consent" else consent,
        trusted_local_scope=None if missing == "scope" else scope,
    )
    with pytest.raises(FleetPlanInvalid, match="exact consent and scope"):
        PreparedPlanRun(**args)
    assert list(tmp_path.iterdir()) == []


def test_preparation_refuses_tl1_backend_without_journal_consent(tmp_path: Path):
    backend, _, _ = _authority()
    with pytest.raises(FleetPlanInvalid, match="original journal consent"):
        PreparedPlanRun(
            None, None, source_authority=None, process_isolation=backend,
            isolation_backend=isolation_backend_evidence(backend),
            path_authority=None, run_parent=tmp_path,
        )
    assert list(tmp_path.iterdir()) == []


def test_worker_runtime_transports_original_scope_not_effect_authority():
    _, scope, consent = _authority()
    executor = FleetPlanRunExecutor(git=Mock(), process_isolation_backends=())
    runtime = executor._step_runtime(
        None, scope.fleet_run_id, "repository-one", None,
        trusted_local_scope=scope, trusted_local_consent=consent,
    )
    assert runtime.trusted_local_scope is scope
    assert runtime.trusted_local_consent is consent
    assert not hasattr(runtime, "trusted_local_effect_authority")


def test_every_command_in_step_receives_exact_original_consent(tmp_path: Path, monkeypatch):
    import oh_goon.integrations.fleet_plan_step_execution as owner
    backend, scope, consent = _authority()
    backend.execute = Mock(return_value=CommandResult(returncode=0, stdout="", stderr=""))
    monkeypatch.setattr(owner, "verify_step_executable_materialization", lambda *args: "original")
    step = SimpleNamespace(step_id="step-one")
    attempt = SimpleNamespace(paths=SimpleNamespace(workspace=tmp_path),
                              executable_materialization=object())
    command = _isolated_command_runner(
        attempt, step, backend, termination_requested=None,
        fleet_run_id=scope.fleet_run_id,
        trusted_local_scope=scope, trusted_local_consent=consent,
    )
    # The existing command entrypoint, no direct backend bypass.
    for ordinal in (1, 2):
        command("/usr/bin/true", cwd=tmp_path, env={})
        request = backend.execute.call_args.args[0]
        assert request.command_ordinal == ordinal
        assert request.trusted_local_scope is scope
        assert request.trusted_local_consent is consent
        assert request.trusted_local_effect_authority is None
        assert request.fleet_run_id == scope.fleet_run_id
        assert request.local_resource_intent is None


def test_prepared_run_cannot_be_reused_for_other_run_before_effect():
    _, scope, consent = _authority()
    executor = FleetPlanRunExecutor(git=Mock(), process_isolation_backends=())
    prepared = SimpleNamespace(plan_id="plan-one", trusted_local_scope=scope,
                               trusted_local_consent=consent)
    plan = SimpleNamespace(plan_id="plan-one", execution=SimpleNamespace(purpose=ExecutionPurpose.CICD))
    with pytest.raises(FleetPlanInvalid, match="identity differs"):
        executor.execute_strict_run(Path("."), plan, prepared, cancel_requested=None,
                                    fleet_run_id="another-run")
