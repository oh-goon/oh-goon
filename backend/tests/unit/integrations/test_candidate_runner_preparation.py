"""Candidate preparation retains original admission, port and resource authority."""

from __future__ import annotations

import hashlib
import os
import subprocess
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from oh_goon.core.errors import StateConflict
from oh_goon.core.models.agent_providers import (
    AgentConnection,
    AgentModel,
    AgentProvider,
    AgentProvidersConfig,
    NativeAgentDefinition,
    NativeAgentProfile,
    ResolvedNativeAgent,
)
from oh_goon.core.models.fleet_plan import (
    ResolvedAgentStep,
    ResolvedFleetPlan,
    ResolvedNativeAgentWorkload,
)
from oh_goon.core.models.fleet_plan_execution_policy import MaintenanceExecutionPolicy
from oh_goon.core.models.goon_plan_source import PlanFileSource
from oh_goon.core.models.repository import RepositoryIdentity
from oh_goon.core.models.target import AgentStepDeliveryReference, FleetPlanTarget
from oh_goon.core.ports.candidate_execution import (
    CandidateExecutionContext,
    CandidateExecutionFactory,
)
from oh_goon.core.ports.capability_runner import CapabilityExecutionRequest
from oh_goon.core.vocabulary import OperationAction
from oh_goon.engine.git import GitService
from oh_goon.integrations.fleet_content_store import FleetContentStore
from oh_goon.integrations.fleet_plan_preview import repository_plan_admission_evidence
from oh_goon.integrations.fleet_plan_run_execution import FleetPlanRunExecutor
from oh_goon.integrations.fleet_plans import FleetPlanRunner
from oh_goon.runtime.database import LocalDatabase
from oh_goon.services.fleet_plan_candidate import (
    prepare_candidate_execution,
    prepare_candidate_workspace,
)
from oh_goon.services.repositories import RepositoryService
from oh_goon.services.repository_discovery import RepositoryDiscoveryService
from tests.factories.capabilities import capability_request
from tests.factories.fleet_plan_targets import structural_execution_identity
from tests.factories.local_state import local_state


@pytest.fixture
def candidate_request(tmp_path: Path):
    repository = tmp_path / "registered"
    repository.mkdir()
    (repository / "app.py").write_text("VALUE = 1\n")
    for args in (
        ("init", "-q"),
        ("add", "app.py"),
        ("-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "base"),
    ):
        subprocess.run(("git", *args), cwd=repository, check=True, capture_output=True, timeout=10)
    database = LocalDatabase(tmp_path / "state/control.sqlite")
    state = local_state(database)
    state.add_location(repository)
    identity = RepositoryIdentity(path=repository)
    admission = prepare_candidate_workspace(
        database=database,
        repositories=RepositoryService(state, RepositoryDiscoveryService()),
        repository_id=identity.repository_id,
        fleet_run_id="candidate-preparation-run",
    )
    native = ResolvedNativeAgent(
        agent="pi",
        profile_name="candidate",
        definition=NativeAgentDefinition(executable="/bin/true"),
        profile=NativeAgentProfile(backend="bubblewrap", inference_profile="fixture"),
        executable_sha256="a" * 64,
        backend_configuration_id="native-backend-fixture",
        connection=AgentConnection.select(
            AgentProvidersConfig(
                providers={
                    "fixture": AgentProvider(
                        api="openai-chat-completions", base_url="http://127.0.0.1:1"
                    )
                },
                models={"fixture": AgentModel(provider="fixture", model="fixture")},
            ),
            "fixture",
        ),
    )
    packet = b"admitted-source-packet"
    digest = hashlib.sha256(packet).hexdigest()
    content = FleetContentStore(tmp_path / "content")
    retained = content.put_frozen_bytes(packet, owner_repository_id=identity.repository_id)
    plan = ResolvedFleetPlan.create(
        name="candidate",
        description="",
        execution=MaintenanceExecutionPolicy(),
        native_agent=native,
        steps=(
            ResolvedAgentStep(
                step_id="native",
                source_step_id="native",
                timeout_seconds=60,
                workload=ResolvedNativeAgentWorkload(native_agent=native),
                delivery_sha256=digest,
                handoff_identity="handoff-fixture",
            ),
        ),
    )
    target = FleetPlanTarget(
        kind="fleet_plan",
        plan=plan,
        source=PlanFileSource(sha256="0" * 64),
        identity=structural_execution_identity(plan),
        reviewed_repository_source=admission.source,
        candidate_workspace=admission,
        agent_deliveries=(
            AgentStepDeliveryReference(
                step_id="native",
                handoff_identity="handoff-fixture",
                delivery_sha256=digest,
                reference_id=retained.reference_id,
            ),
        ),
    )
    request = replace(
        capability_request(
            repository, OperationAction.EXECUTE_PLAN, target, fleet_run_id=admission.fleet_run_id
        ),
        repository_admission=repository_plan_admission_evidence(identity, plan),
        require_execution_current=lambda: None,
        require_settlement_current=lambda: None,
    )
    return SimpleNamespace(database=database, request=request, content=content, root=tmp_path)


def _runner(case, factory: CandidateExecutionFactory | None) -> FleetPlanRunner:
    return FleetPlanRunner(
        GitService(),
        process_isolation_backends=(),
        path_authority=None,
        content_store=case.content,
        run_parent=case.root / "runs",
        candidate_execution_factory=factory,
    )


@pytest.mark.parametrize(
    "missing", ["factory", "launch-guard", "settlement-guard", "run", "source"]
)
def test_candidate_factory_cannot_allocate_from_missing_or_stale_original_authority(
    candidate_request, missing
):
    case = candidate_request
    calls = []

    def factory(admission, request) -> CandidateExecutionContext:
        calls.append((admission, request))
        raise AssertionError("Invalid authority reached the candidate allocation port")

    request = case.request
    if missing == "launch-guard":
        request = replace(request, require_execution_current=None)
    elif missing == "settlement-guard":
        request = replace(request, require_settlement_current=None)
    elif missing == "run":
        request = replace(
            request,
            attempt=request.attempt.model_copy(
                update={
                    "origin": request.attempt.origin.model_copy(
                        update={"fleet_run_id": "another-run"}
                    )
                }
            ),
        )
    elif missing == "source":
        (request.attempt.repository.path / "app.py").write_text("CHANGED\n")
    runner = _runner(case, None if missing == "factory" else factory)
    with pytest.raises(StateConflict):
        runner.prepare(request)
    assert calls == []


@pytest.mark.parametrize("fail_preflight", [False, True])
def test_candidate_preparation_binds_real_input_and_closes_context_after_preflight_failure(
    candidate_request, monkeypatch, fail_preflight
):
    case = candidate_request
    contexts: list[CandidateExecutionContext] = []
    preflights = []
    primary = OSError("native preflight refused")

    def factory(admission, request: CapabilityExecutionRequest) -> CandidateExecutionContext:
        assert admission == case.request.attempt.target.candidate_workspace
        assert request is case.request
        context = prepare_candidate_execution(
            admission,
            database=case.database,
            effect=request.attempt,
            require_execution_current=request.require_execution_current,
            require_settlement_current=request.require_settlement_current,
        )
        contexts.append(context)
        return context

    def preflight(_self, repository, plan, **authority):
        authority["require_candidate_current"]()
        preflights.append((repository, plan, authority))
        if fail_preflight:
            raise primary

    monkeypatch.setattr(FleetPlanRunExecutor, "prepare_direct_run", preflight)
    runner = _runner(case, factory)
    if fail_preflight:
        with pytest.raises(OSError) as failure:
            runner.prepare(case.request)
        assert failure.value is primary
    else:
        prepared = runner.prepare(case.request)
        assert prepared.execution_input_id == contexts[0].materialization.materialization_id
        prepared.close()
        prepared.close()
    context = contexts[0]
    repository, plan, authority = preflights[0]
    assert repository == case.request.attempt.repository
    assert plan == case.request.attempt.target.plan
    assert authority["execution_workspace"] == context.path != repository.path
    assert authority["workspace_materialization"] == context.materialization
    assert authority["workspace_directory_fd"] == context.workspace_directory_fd
    with pytest.raises(OSError):
        os.fstat(context.workspace_directory_fd)
    assert (context.path / "app.py").read_text() == "VALUE = 1\n"
