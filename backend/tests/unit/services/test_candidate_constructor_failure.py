"""Failed candidate preparation releases only proven unused mutation authority."""

from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from oh_goon.core.models.agent_providers import (
    AgentConnection,
    AgentModel,
    AgentProvider,
    AgentProvidersConfig,
    NativeAgentDefinition,
    NativeAgentProfile,
    ResolvedNativeAgent,
)
from oh_goon.core.models.effect_attempt import ClaimedRepositoryEffectAttempt
from oh_goon.core.models.effect_origin import FleetEffectOrigin
from oh_goon.core.models.fleet_plan import (
    ResolvedAgentStep,
    ResolvedFleetPlan,
    ResolvedNativeAgentWorkload,
)
from oh_goon.core.models.fleet_plan_evidence import (
    CompletedPlanStepExecution,
    FleetPlanResult,
    PlanEgressEvidence,
    PlanExecutionSupervisorEvidence,
    PlanIsolationBackendEvidence,
    PlanOutputEvidence,
    PlanStandardIsolationEvidence,
    PlanStepAttemptEvidence,
    PlanStepSettlementEvidence,
    PlanWorkspaceWriterScopeEvidence,
)
from oh_goon.core.models.fleet_plan_execution_policy import MaintenanceExecutionPolicy
from oh_goon.core.models.goon_plan_source import PlanFileSource
from oh_goon.core.models.repository import RepositoryIdentity
from oh_goon.core.models.run_events import ProcessStartingEvent
from oh_goon.core.models.target import AgentStepDeliveryReference, FleetPlanTarget
from oh_goon.core.process_birth import ProcessBirthIdentity
from oh_goon.core.time import utc_now
from oh_goon.core.vocabulary import (
    EffectAttemptOwner,
    EffectAttemptStatus,
    EffectBoundaryKind,
    FleetRunEventType,
    OperationAction,
)
from oh_goon.core.vocabulary.workspace import WorkspaceLifecycleState
from oh_goon.runtime.database import LocalDatabase
from oh_goon.runtime.execution_materializations import ExecutionMaterializationAuthority
from oh_goon.runtime.journal import Journal
from oh_goon.runtime.workspaces import RepositoryWorkspaceAuthority
from oh_goon.services.fleet_plan_candidate import (
    prepare_candidate_execution,
    prepare_candidate_workspace,
    workspace_control_git,
)
from oh_goon.services.repositories import RepositoryService
from oh_goon.services.repository_discovery import RepositoryDiscoveryService
from tests.factories.capabilities import capability_snapshot
from tests.factories.fleet_plan_targets import structural_execution_identity
from tests.factories.local_state import local_state


@pytest.fixture
def prepared(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()

    def git(*args):
        subprocess.run(("git", *args), cwd=repo, check=True, capture_output=True)

    git("init", "-q")
    git("config", "user.name", "Test")
    git("config", "user.email", "test@example.invalid")
    (repo / "app.py").write_text("VALUE = 1\n")
    git("add", "app.py")
    git("commit", "-qm", "base")
    database = LocalDatabase(tmp_path / "state/control.sqlite")
    state = local_state(database)
    state.add_location(repo)
    repository = RepositoryIdentity(path=repo)
    admission = prepare_candidate_workspace(
        database=database,
        repositories=RepositoryService(state, RepositoryDiscoveryService()),
        repository_id=repository.repository_id,
        fleet_run_id="constructor-failure-run",
    )
    native = ResolvedNativeAgent(
        agent="pi",
        profile_name="candidate",
        definition=NativeAgentDefinition(executable="/bin/true"),
        profile=NativeAgentProfile(backend="bubblewrap", inference_profile="model"),
        executable_sha256="a" * 64,
        backend_configuration_id="backend-test",
        connection=AgentConnection.select(
            AgentProvidersConfig(
                providers={
                    "test": AgentProvider(
                        api="openai-chat-completions", base_url="http://127.0.0.1:1"
                    )
                },
                models={"model": AgentModel(provider="test", model="synthetic")},
            ),
            "model",
        ),
    )
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
                delivery_sha256="a" * 64,
                handoff_identity="handoff-test",
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
                handoff_identity="handoff-test",
                delivery_sha256="a" * 64,
                reference_id="ref-" + "a" * 32,
            ),
        ),
    )
    now = utc_now()
    claim = ClaimedRepositoryEffectAttempt(
        operation_id="original-candidate-operation",
        repository=repository,
        capability=capability_snapshot(OperationAction.EXECUTE_PLAN, target),
        target=target,
        origin=FleetEffectOrigin(
            kind=EffectAttemptOwner.FLEET, fleet_run_id=admission.fleet_run_id
        ),
        status=EffectAttemptStatus.CLAIMED,
        effect_boundary=EffectBoundaryKind.EXTERNAL,
        baseline=workspace_control_git().observe(repo).fingerprint,
        started_at=now,
        updated_at=now,
    )
    return database, admission, claim


@pytest.mark.parametrize("commit_happened", [False, True])
def test_constructor_materialization_fault_preserves_primary_and_releases_only_no_effect(
    prepared, monkeypatch, commit_happened
):
    database, admission, claim = prepared
    original = ExecutionMaterializationAuthority.materialize
    primary = OSError("materialization storage fault")

    def fault(self, *args, **kwargs):
        if commit_happened:
            original(self, *args, **kwargs)
        raise primary

    monkeypatch.setattr(ExecutionMaterializationAuthority, "materialize", fault)
    owner_guard = Mock()
    with pytest.raises(OSError, match="materialization storage fault") as failure:
        prepare_candidate_execution(
            admission,
            database=database,
            effect=claim,
            require_execution_current=Mock(),
            require_settlement_current=owner_guard,
        )
    assert failure.value is primary
    owner_guard.assert_called_once_with()
    owner = RepositoryWorkspaceAuthority(database)
    state = owner.state(admission.workspace.workspace_id)
    with database.transaction() as connection:
        rows = connection.execute(
            "SELECT status FROM workspace_leases WHERE workspace_id=?",
            (admission.workspace.workspace_id,),
        ).fetchall()
        materializations = connection.execute(
            "SELECT COUNT(*) FROM execution_materializations WHERE workspace_id=?",
            (admission.workspace.workspace_id,),
        ).fetchone()[0]
    if commit_happened:
        assert state.lifecycle_state is WorkspaceLifecycleState.IN_USE
        assert state.current_lease_id is not None
        assert [row[0] for row in rows] == ["ACTIVE"]
        assert materializations == 1
        assert any("lease retained" in note for note in primary.__notes__)
    else:
        assert state.lifecycle_state is WorkspaceLifecycleState.READY
        assert state.current_lease_id is None
        assert [row[0] for row in rows] == ["RELEASED"]
        assert materializations == 0
    assert owner.physical_path(admission.workspace).is_dir()
    assert (claim.repository.path / "app.py").read_text() == "VALUE = 1\n"


@pytest.mark.parametrize("extra_writer", [False, True])
def test_settlement_requires_complete_canonical_scoped_launch_partition(
    prepared, monkeypatch, extra_writer
):
    database, admission, claim = prepared
    context = prepare_candidate_execution(
        admission,
        database=database,
        effect=claim,
        require_execution_current=Mock(),
        require_settlement_current=Mock(),
    )
    try:
        context.mark_writer_started()
        materialization = context.materialization
        # Typed synthetic facts exercise membership admission only. The separate
        # native backend tests establish actual namespace/PID observations.
        scope = PlanWorkspaceWriterScopeEvidence(
            materialization=materialization,
            physical_execution_id="payload",
            backend_configuration_id="backend-test",
            namespace_device=1,
            namespace_inode=2,
            workspace_device=materialization.workspace_device,
            workspace_inode=materialization.workspace_inode,
            workspace_mode=materialization.workspace_mode,
            init_process=ProcessBirthIdentity(pid=123, boot_id="test-boot", start_ticks=100),
            observed_at=utc_now(),
        )
        output = PlanOutputEvidence(
            sha256="e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
            byte_count=0,
            line_count=0,
        )
        result = FleetPlanResult(
            plan_id=claim.target.plan.plan_id,
            steps=(
                CompletedPlanStepExecution(
                    step_id="native",
                    kind="agent",
                    stdout_evidence=output,
                    stderr_evidence=output,
                    repository_state_before="before",
                    repository_state_after="after",
                    settlement=PlanStepSettlementEvidence(
                        outcome="SUCCESS",
                        cleanup="VERIFIED",
                        reason="ACCEPTED_EXIT",
                        observed_returncode=0,
                    ),
                    attempts=(
                        PlanStepAttemptEvidence(
                            attempt=1,
                            physical_execution_id="payload",
                            returncode=0,
                            stdout_evidence=output,
                            stderr_evidence=output,
                            repository_state_before="before",
                            repository_state_after="after",
                            egress=PlanEgressEvidence(mode="none"),
                            supervisor=PlanExecutionSupervisorEvidence(
                                final_process_group_quiescent=True
                            ),
                            standard_isolation=PlanStandardIsolationEvidence(
                                backend=PlanIsolationBackendEvidence(
                                    backend_id="bubblewrap",
                                    configuration_id="backend-test",
                                    kernel_boundary="shared_host_kernel",
                                    remote_execution=False,
                                    experimental=False,
                                ),
                                workspace_read_only=False,
                                filesystem_cleanup="verified",
                                broker_socket_mounted=True,
                                resource_owner="none",
                                workspace_writer_scope=scope,
                            ),
                        ),
                    ),
                ),
            ),
        )
        physical = ("payload", "unrepresented-writer") if extra_writer else ("payload",)
        events = tuple(
            ProcessStartingEvent(
                event_type=FleetRunEventType.PROCESS_STARTING,
                sequence=index,
                fleet_run_id=admission.fleet_run_id,
                repository_id=claim.repository.repository_id,
                operation_id=claim.operation_id,
                step_id="native",
                step_order=1,
                attempt_number=1,
                step_attempt_id="original-step-attempt",
                physical_execution_id=value,
                workspace_materialization_id=materialization.materialization_id,
            )
            for index, value in enumerate(physical, 1)
        )
        detail = SimpleNamespace(
            run=SimpleNamespace(target=claim.target, fleet_run_id=admission.fleet_run_id),
            lifecycle_events=events,
        )
        monkeypatch.setattr(
            Journal,
            "read_view",
            lambda _self: SimpleNamespace(required_run_detail=lambda _id: detail),
        )
        receipt = context.settle(result)
        if extra_writer:
            assert receipt.settlement == "retained_incomplete"
            assert receipt.checkpoint is receipt.acceptance is None
            assert receipt.state.lifecycle_state is WorkspaceLifecycleState.IN_USE
            assert receipt.state.current_checkpoint_id == admission.checkpoint.checkpoint_id
        else:
            assert receipt.settlement == "checkpointed"
            assert receipt.acceptance is not None
            assert receipt.state.lifecycle_state is WorkspaceLifecycleState.READY
    finally:
        context.close()
