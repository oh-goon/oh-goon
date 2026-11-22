"""Concurrent publication refreshes original state without rediscovering frozen sources."""

from __future__ import annotations

import hashlib
import subprocess
from dataclasses import replace
from types import SimpleNamespace
from typing import cast

import pytest
from pydantic import JsonValue

from oh_goon.core.errors import FleetPlanInvalid, JournalConflict, TargetSelectionError
from oh_goon.core.models.agent_providers import (
    AgentConnection,
    AgentModel,
    AgentProvider,
    AgentProvidersConfig,
    NativeAgentDefinition,
    NativeAgentProfile,
    ResolvedNativeAgent,
)
from oh_goon.core.models.capability import (
    CapabilityRequirement,
    CapabilityRequirementCondition,
    CapabilityRequirementTarget,
)
from oh_goon.core.models.explanation import ReasonCode
from oh_goon.core.models.fleet_plan import (
    ResolvedAgentStep,
    ResolvedCommandStep,
    ResolvedFleetPlan,
    ResolvedNativeAgentWorkload,
)
from oh_goon.core.models.fleet_plan_execution_policy import VerificationExecutionPolicy
from oh_goon.core.models.goon_plan_source import PlanFileSource
from oh_goon.core.models.repository import RepositoryIdentity
from oh_goon.core.models.run_admission import ExplicitRepositoryScope
from oh_goon.core.models.run_events import RunResourcesObservedEvent
from oh_goon.core.models.run_resources import (
    RunResourceEnvelopeEvidence,
    RunResourceEnvelopeObservation,
)
from oh_goon.core.models.target import AgentStepDeliveryReference, FleetPlanTarget
from oh_goon.core.run_projection import project_fleet_run_detail
from oh_goon.core.vocabulary import (
    FleetRunEventType,
    FleetRunSource,
    FleetRunTerminalIntent,
    OperationAction,
    Verdict,
)
from oh_goon.integrations.fleet_content_store import FleetContentStore
from oh_goon.integrations.run_resource_envelope import VerifiedWorkerResourceClosure
from oh_goon.runtime.database import LocalDatabase
from oh_goon.runtime.effect_attempts import RepositoryEffectAttemptStore
from oh_goon.runtime.execution import ExecutionCoordinator
from oh_goon.runtime.executor_pool import FleetExecutorPool
from oh_goon.runtime.safety import RuntimeSafetyGate
from oh_goon.runtime.schedule_executions import ScheduleExecutionStore
from oh_goon.services.fleet_admission import FleetAdmissionPlanner
from oh_goon.services.fleet_plan_candidate import prepare_candidate_workspace
from oh_goon.services.fleet_run_lifecycle import FleetRunLifecycle
from oh_goon.services.invariant_auditor import SystemInvariantAuditor
from oh_goon.services.league import LeagueService
from oh_goon.services.migration import MigrationService
from oh_goon.services.repositories import RepositoryService
from oh_goon.services.repository_discovery import RepositoryDiscoveryService
from oh_goon.services.runtime_safety import RuntimeSafetyService
from oh_goon.services.target_selection import FleetTargetSelector
from tests.factories.capabilities import resolved_intent
from tests.factories.fleet_plan_targets import structural_execution_identity
from tests.factories.journal import commit_operation
from tests.factories.local_state import local_state
from tests.factories.operation_reports import make_operation_report
from tests.factories.run_concurrency import run_concurrency_scope


class CountingDiscovery(RepositoryDiscoveryService):
    def __init__(self):
        self.calls = 0

    def git_roots(self, *args, **kwargs):
        self.calls += 1
        return super().git_roots(*args, **kwargs)


@pytest.fixture
def prepared_candidates(tmp_path):
    database = LocalDatabase(tmp_path / "state/database.sqlite")
    state = local_state(database)
    repositories = tmp_path / "repositories"
    repositories.mkdir()
    for name in ("a", "b"):
        repository = repositories / name
        repository.mkdir()
        (repository / "app.txt").write_text("original\n")
        for args in [
            ("init", "-q"),
            ("add", "."),
            (
                "-c",
                "user.name=Test",
                "-c",
                "user.email=test@example.invalid",
                "commit",
                "-qm",
                "initial",
            ),
        ]:
            subprocess.run(
                ("git", *args), cwd=repository, capture_output=True, check=True, timeout=10
            )
    state.add_location(repositories)
    discovery = CountingDiscovery()
    service = RepositoryService(state, discovery)
    execution = ExecutionCoordinator(database)
    journal = state.journal
    attempts = RepositoryEffectAttemptStore(database)
    auditor = SystemInvariantAuditor(
        database, journal, execution, attempts, ScheduleExecutionStore(database)
    )
    safety = RuntimeSafetyService(auditor, RuntimeSafetyGate())
    planner = FleetAdmissionPlanner(
        state,
        FleetTargetSelector(
            service, MigrationService(state, discovery), LeagueService(state, discovery)
        ),
        auditor,
        safety,
        execution=execution,
    )
    lifecycle = FleetRunLifecycle(
        database, journal, execution, FleetExecutorPool(database, None), standard_local_fleet=True
    )
    native = ResolvedNativeAgent(
        agent="pi",
        profile_name="candidate",
        definition=NativeAgentDefinition(executable="/bin/true"),
        profile=NativeAgentProfile(backend="bubblewrap", inference_profile="fixture"),
        executable_sha256="a" * 64,
        backend_configuration_id="fixture-backend",
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
    plan = ResolvedFleetPlan.create(
        name="candidate-admission",
        description="",
        execution=VerificationExecutionPolicy(),
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
            ResolvedCommandStep(step_id="verify", argv=("/bin/true",), timeout_seconds=5),
        ),
    )
    cases = []
    for name in ("a", "b"):
        repository = RepositoryIdentity(path=repositories / name)
        candidate = prepare_candidate_workspace(
            database=database,
            repositories=service,
            repository_id=repository.repository_id,
            fleet_run_id="run-" + name,
        )
        target = FleetPlanTarget(
            kind="fleet_plan",
            plan=plan,
            source=PlanFileSource(sha256="0" * 64),
            identity=structural_execution_identity(plan),
            candidate_workspace=candidate,
            reviewed_repository_source=candidate.source,
            agent_deliveries=(
                AgentStepDeliveryReference(
                    step_id="native",
                    handoff_identity="handoff-fixture",
                    delivery_sha256=digest,
                    reference_id=content.put_frozen_bytes(
                        packet, owner_repository_id=repository.repository_id
                    ).reference_id,
                ),
            ),
        )
        deferred = run_concurrency_scope("run-" + name)
        cases.append(
            SimpleNamespace(
                run_id="run-" + name,
                repository=repository,
                target=target,
                intent=resolved_intent(OperationAction.EXECUTE_PLAN, target),
                deferred=deferred,
                concurrency=deferred.admission,
                scope=ExplicitRepositoryScope(repository_ids=(repository.repository_id,)),
            )
        )
    return SimpleNamespace(
        database=database,
        state=state,
        journal=journal,
        execution=execution,
        planner=planner,
        lifecycle=lifecycle,
        discovery=discovery,
        repositories=service,
        first=cases[0],
        second=cases[1],
    )


def prepare(stack, case):
    return stack.planner.plan(
        case.intent, repository_scope=case.scope, concurrency=case.concurrency
    )


def admit(stack, case, prepared):
    return stack.lifecycle.admit(
        case.run_id,
        case.intent,
        case.scope,
        [entry.repository for entry in prepared.targets],
        expected_durable_revision=prepared.durable_revision,
        repository_admissions=prepared.repository_admissions,
        source=FleetRunSource.MANUAL,
        concurrency=case.concurrency,
        deferred_worker_resource_scope=cast(dict[str, JsonValue], case.deferred.to_payload()),
        refresh_concurrency_in=lambda connection: stack.planner.refresh_concurrent_plan_in(
            connection,
            prepared,
            case.intent,
            repository_scope=case.scope,
            concurrency=case.concurrency,
        ).durable_revision,
    )


def test_sibling_control_progress_does_not_invalidate_prepared_source_or_trigger_discovery(
    prepared_candidates,
):
    stack = prepared_candidates
    first, second = prepare(stack, stack.first), prepare(stack, stack.second)
    admit(stack, stack.first, first)
    stack.lifecycle.cancel(stack.first.run_id)
    assert stack.database.durable_revision() > second.durable_revision
    observed_walks = stack.discovery.calls
    _, admitted = admit(stack, stack.second, second)
    assert admitted.fleet_run_id == stack.second.run_id
    assert stack.discovery.calls == observed_walks
    detail = stack.journal.read_view().required_run_detail(stack.second.run_id)
    assert detail.repository_admissions == second.repository_admissions
    assert len(stack.journal.active_run_details()) == 2


def test_final_refresh_rejects_changed_configuration_namespace(prepared_candidates, tmp_path):
    stack = prepared_candidates
    prepared = prepare(stack, stack.second)
    stack.state.add_location(tmp_path / "new-scope")
    with pytest.raises(TargetSelectionError, match="scope changed"):
        admit(stack, stack.second, prepared)
    assert stack.journal.read_view().run_detail(stack.second.run_id) is None
    assert stack.execution.active_lease() is None


def test_final_refresh_rejects_same_path_subject_replacement(prepared_candidates):
    stack, case = prepared_candidates, prepared_candidates.second
    prepared = prepare(stack, case)
    root = case.repository.path
    root.rename(root.with_name("original-subject"))
    root.mkdir()
    (root / "app.txt").write_text("original\n")
    with pytest.raises(TargetSelectionError, match="instance changed"):
        admit(stack, case, prepared)
    assert stack.journal.read_view().run_detail(case.run_id) is None


def test_final_refresh_rejects_changed_capability_prerequisite(prepared_candidates):
    stack, case = prepared_candidates, prepared_candidates.second
    ready = make_operation_report(case.repository, OperationAction.INIT, fleet_run_id=None)
    commit_operation(stack.journal, ready)
    case.intent = replace(
        case.intent,
        definition=replace(
            case.intent.definition,
            requirements=(
                CapabilityRequirement(
                    capability_id=ready.capability.capability_id,
                    target_binding=CapabilityRequirementTarget.NONE,
                    condition=CapabilityRequirementCondition.SUCCESSFUL,
                ),
            ),
        ),
    )
    prepared = prepare(stack, case)
    commit_operation(
        stack.journal,
        make_operation_report(
            case.repository,
            OperationAction.INIT,
            Verdict.FAILED,
            ReasonCode.OPERATION_FAILED,
            fleet_run_id=None,
        ),
    )
    with pytest.raises(TargetSelectionError, match="prerequisite is not satisfied"):
        admit(stack, case, prepared)
    assert stack.journal.read_view().run_detail(case.run_id) is None


def test_atomic_journal_publication_refuses_same_candidate_even_with_a_new_passive_run_binding(
    prepared_candidates,
):
    stack, first, second = (
        prepared_candidates,
        prepared_candidates.first,
        prepared_candidates.second,
    )
    one = prepare(stack, first)
    admit(stack, first, one)
    candidate = first.target.candidate_workspace.model_copy(update={"fleet_run_id": second.run_id})
    second.target = first.target.model_copy(update={"candidate_workspace": candidate})
    second.intent = resolved_intent(OperationAction.EXECUTE_PLAN, second.target)
    second.scope = first.scope
    prepared = prepare(stack, second)
    with pytest.raises(JournalConflict, match="cannot share an owned candidate"):
        admit(stack, second, prepared)
    assert stack.journal.read_view().run_detail(second.run_id) is None
    with stack.database.transaction() as connection:
        retained = stack.execution.retained_leases_in(connection)
        assert len(retained) == 1 and retained[0].fleet_run_id == first.run_id


def test_slot_two_cancellation_stays_a_live_original_request(prepared_candidates):
    stack, case = prepared_candidates, prepared_candidates.second
    admit(stack, case, prepare(stack, case))
    with stack.database.transaction(write=True) as connection:
        before = stack.execution.active_fleet_lease_in(connection, case.run_id)
        connection.execute(
            "UPDATE execution_lease SET singleton=2 WHERE fleet_run_id=?", (case.run_id,)
        )
    cancelled = stack.lifecycle.cancel(case.run_id)
    assert cancelled.status.cancel_request_satisfied and not cancelled.status.terminal
    with stack.database.transaction() as connection:
        lease = stack.execution.active_fleet_lease_in(connection, case.run_id)
        assert lease is not None and lease == before
        assert connection.execute(
            "SELECT cancel_requested_at FROM execution_lease WHERE fleet_run_id=?", (case.run_id,)
        ).fetchone()[0]


def test_passive_native_observations_cannot_issue_canonical_closure_or_free_capacity(
    prepared_candidates,
):
    stack, case = prepared_candidates, prepared_candidates.second
    ticket, _ = admit(stack, case, prepare(stack, case))
    stack.lifecycle.finish_admitted(ticket, FleetRunTerminalIntent.CANCEL)
    observation = RunResourceEnvelopeObservation(
        fleet_run_id=case.run_id,
        unit_name=case.concurrency.worker_unit_name,
        state="TERMINAL",
        cleanup="VERIFIED",
    )
    forged = VerifiedWorkerResourceClosure(case.concurrency, None, observation, object())
    with pytest.raises(FleetPlanInvalid):
        stack.lifecycle.release_worker_reservation(case.run_id, ticket, forged)
    with pytest.raises(FleetPlanInvalid):
        with stack.database.transaction(write=True) as connection:
            stack.journal.record_worker_resource_observation_in(
                connection,
                fleet_run_id=case.run_id,
                concurrency=case.concurrency,
                envelope=None,
                observation=observation,
                proof=forged,
            )
    detail = stack.journal.read_view().required_run_detail(case.run_id)
    assert detail.resource_observation is None
    with stack.database.transaction() as connection:
        retained = stack.execution.retained_fleet_lease_in(connection, case.run_id)
        assert retained is not None and retained.custody.phase == "RESOURCE_RESERVED"


def test_distinct_allocations_for_one_registered_subject_do_not_inherit_cross_repository_qualification(
    prepared_candidates,
):
    stack, first, second = (
        prepared_candidates,
        prepared_candidates.first,
        prepared_candidates.second,
    )
    admit(stack, first, prepare(stack, first))
    candidate = prepare_candidate_workspace(
        database=stack.database,
        repositories=stack.repositories,
        repository_id=first.repository.repository_id,
        fleet_run_id=second.run_id,
    )
    assert (
        candidate.workspace.workspace_id != first.target.candidate_workspace.workspace.workspace_id
    )
    second.target = first.target.model_copy(
        update={"candidate_workspace": candidate, "reviewed_repository_source": candidate.source}
    )
    second.intent = resolved_intent(OperationAction.EXECUTE_PLAN, second.target)
    second.scope = first.scope
    prepared = prepare(stack, second)
    with pytest.raises(JournalConflict, match="distinct registered repository subjects"):
        admit(stack, second, prepared)
    assert stack.journal.read_view().run_detail(second.run_id) is None


def test_worker_projection_preserves_incomplete_accounting_with_verified_cleanup(
    prepared_candidates,
):
    stack, case = prepared_candidates, prepared_candidates.second
    ticket, _ = admit(stack, case, prepare(stack, case))
    stack.lifecycle.finish_admitted(ticket, FleetRunTerminalIntent.CANCEL)
    birth = replace(
        case.concurrency.aggregate.controller_birth,
        pid=case.concurrency.aggregate.controller_birth.pid + 1000,
        start_ticks=case.concurrency.aggregate.controller_birth.start_ticks + 1,
    )
    worker = RunResourceEnvelopeEvidence(
        subject="worker",
        fleet_run_id=case.run_id,
        unit_name=case.concurrency.worker_unit_name,
        invocation_id="b" * 32,
        handoff_sha256="sha256:" + "c" * 64,
        process_birth=birth,
        resources=case.concurrency.policy.worker_resources,
        cgroup_device=1,
        cgroup_inode=4,
        cpu_max="100000 100000",
        memory_max_bytes=384 * 1024 * 1024,
        memory_swap_max_bytes=0,
        pids_max=48,
        oom_policy="kill",
        memory_oom_group=True,
    )
    observation = RunResourceEnvelopeObservation(
        fleet_run_id=case.run_id,
        unit_name=worker.unit_name,
        invocation_id=worker.invocation_id,
        state="INCOMPLETE",
        cleanup="VERIFIED",
        reason_code="OPTIONAL_ACCOUNTING_UNAVAILABLE",
    )
    view = stack.journal.read_view()
    events = view.run_events(case.run_id).events
    event = RunResourcesObservedEvent(
        event_type=FleetRunEventType.RUN_RESOURCES_OBSERVED,
        sequence=events[-1].sequence + 1,
        fleet_run_id=case.run_id,
        worker_resource_envelope=worker,
        observation=observation,
    )
    # This is the pure projection contract; it issues no native closure authority.
    projected = project_fleet_run_detail([*events, event], view.operation)
    assert projected.resource_observation.state == "INCOMPLETE"
    assert projected.resource_observation.cleanup == "VERIFIED"
    assert projected.resource_observation.cpu_usage_usec is None
    assert projected.worker_resource_envelope == worker
