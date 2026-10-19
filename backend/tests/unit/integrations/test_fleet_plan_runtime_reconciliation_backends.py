from __future__ import annotations

import sys
from pathlib import Path

import pytest

from oh_goon.core.errors import ExecutionAuthorityDivergence
from oh_goon.core.models.fleet_plan import ResolvedCommandStep, ResolvedFleetPlan
from oh_goon.core.models.fleet_plan_execution_policy import CicdExecutionPolicy
from oh_goon.core.models.fleet_plan_isolation import CicdResourcePolicy
from oh_goon.core.models.repository import RepositoryIdentity
from oh_goon.engine.command import CommandResult
from oh_goon.integrations import fleet_plan_opensandbox_isolation
from oh_goon.integrations.fleet_plan_opensandbox_isolation import (
    OpenSandboxProcessIsolation,
    OpenSandboxRuntimeKind,
    OpenSandboxSettings,
)
from oh_goon.integrations.fleet_plan_process_isolation import (
    IsolatedProcessRequest,
    isolation_backend_evidence,
)
from tests.factories.cicd_repository import seed_cicd_repository
from tests.factories.fleet_plan_executor import prepare_physical_test_run
from tests.factories.path_authority import TEST_PATH_AUTHORITY

_FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "opensandbox"


class _Transport:
    def __init__(self, *, include_observation: bool = True) -> None:
        self.include_observation = include_observation
        self.requests: list[IsolatedProcessRequest] = []
        self.availability_checks = 0

    def require_available(self, settings: OpenSandboxSettings) -> None:
        del settings
        self.availability_checks += 1

    def execute(
        self,
        settings,
        request,
        *,
        expected_runtime_authority,
        timeout,
        cancel_requested,
    ):
        del settings, timeout, cancel_requested
        self.requests.append(request)
        authority = expected_runtime_authority if self.include_observation else None
        return CommandResult(
            argv=request.argv,
            returncode=0,
            stdout="",
            stderr="",
            runtime_authority=authority,
        )


def _backend(transport: _Transport) -> OpenSandboxProcessIsolation:
    return OpenSandboxProcessIsolation(
        OpenSandboxSettings(
            domain="sandbox.internal",
            api_key="test-key",
            image="registry.internal/oh-goon-runner@sha256:" + "1" * 64,
            cli_executable=sys.executable,
            runtime_attestation_file=str(_FIXTURES / "kata-runtime-attestation.json"),
            runtime_attestation_public_key_file=str(
                _FIXTURES / "runtime-attestation-ed25519.pub.pem"
            ),
            runtime=OpenSandboxRuntimeKind.KATA,
            protocol="https",
        ),
        transport=transport,
    )


def _plan() -> ResolvedFleetPlan:
    return ResolvedFleetPlan.create(
        name="runtime-reconciliation",
        description="",
        execution=CicdExecutionPolicy(
            trust_repository_code=False,
            resources=CicdResourcePolicy(cpu_millis=2000, memory_mib=4096, max_processes=256),
        ),
        requirements=(),
        steps=(ResolvedCommandStep(timeout_seconds=3600, step_id="check", argv=("true",)),),
    )


def _request(tmp_path: Path, backend: OpenSandboxProcessIsolation, argv: tuple[str, ...]):
    repository = tmp_path / "repo"
    seed_cicd_repository(repository)
    plan = _plan()
    step = plan.steps[0]
    return (
        prepare_physical_test_run(
            RepositoryIdentity(path=repository),
            plan,
            isolation_backend=isolation_backend_evidence(backend),
            path_authority=TEST_PATH_AUTHORITY,
        ),
        step,
        argv,
    )


def test_runtime_divergence_refuses_before_remote_workload_launch(tmp_path: Path) -> None:
    transport = _Transport()
    backend = _backend(transport)
    run_context, step, argv = _request(tmp_path, backend, ("python3", "-V"))
    with run_context as run:
        attempt = run.create_attempt(step, 1)
        request = IsolatedProcessRequest(
            attempt=attempt, argv=argv, cwd=attempt.paths.workspace, env={}
        )
        with pytest.raises(ExecutionAuthorityDivergence):
            backend.execute(request, timeout=1.0, cancel_requested=None)

    assert transport.availability_checks == 1
    assert transport.requests == []


def test_missing_remote_runtime_observation_fails_closed(tmp_path: Path) -> None:
    transport = _Transport(include_observation=False)
    backend = _backend(transport)
    run_context, step, argv = _request(tmp_path, backend, ("true",))
    with run_context as run:
        attempt = run.create_attempt(step, 1)
        request = IsolatedProcessRequest(
            attempt=attempt, argv=argv, cwd=attempt.paths.workspace, env={}
        )
        with pytest.raises(ExecutionAuthorityDivergence, match="did not match"):
            backend.execute(request, timeout=1.0, cancel_requested=None)

    assert len(transport.requests) == 1


def test_remote_runtime_expectation_is_resolved_once_and_reuse_is_receipted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    transport = _Transport()
    backend = _backend(transport)
    run_context, step, argv = _request(tmp_path, backend, ("true",))
    original = fleet_plan_opensandbox_isolation.expected_runtime_authority_sources
    calls = 0

    def counted(request):
        nonlocal calls
        calls += 1
        return original(request)

    monkeypatch.setattr(
        fleet_plan_opensandbox_isolation,
        "expected_runtime_authority_sources",
        counted,
    )
    with run_context as run:
        attempt = run.create_attempt(step, 1)
        request = IsolatedProcessRequest(
            attempt=attempt, argv=argv, cwd=attempt.paths.workspace, env={}
        )
        result = backend.execute(request, timeout=1.0, cancel_requested=None)

    assert calls == 1
    assert len(result.execution_authority_economics) == 1
    economics = result.execution_authority_economics[0]
    assert economics.candidate_units == 1
    assert economics.reused_units == 1
    assert economics.recomputed_units == 0
    assert economics.reused_bytes > 0
    assert economics.terminal_proof_bytes == economics.reused_bytes
