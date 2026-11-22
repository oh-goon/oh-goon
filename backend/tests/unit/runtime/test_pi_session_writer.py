from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from threading import Barrier

import pytest

from oh_goon.core.errors import InvalidRunTransition, OwnershipLost, PiSessionBusy
from oh_goon.core.models.agent_providers import (
    AgentConnection,
    AgentModel,
    AgentProvider,
    NativeAgentDefinition,
    NativeAgentProfile,
    PiSessionIntent,
    ResolvedNativeAgent,
)
from oh_goon.core.models.fleet_plan import (
    ResolvedAgentStep,
    ResolvedFleetPlan,
    ResolvedNativeAgentWorkload,
)
from oh_goon.core.models.fleet_plan_execution_policy import MaintenanceExecutionPolicy
from oh_goon.core.models.goon_plan_source import PlanFileSource
from oh_goon.core.models.pi_session_storage import (
    PiSessionFileObservation,
    PiSessionWriterObservation,
)
from oh_goon.core.models.repository import RepositoryIdentity
from oh_goon.core.models.run_admission import (
    DirectFleetExecutionAdmission,
    FleetRunAdmissionIntent,
    fleet_repository_scope,
)
from oh_goon.core.models.run_events import (
    DescendantsQuiescentEvent,
    PiSessionWriterObservedEvent,
    PiSessionWriterReservedEvent,
)
from oh_goon.core.models.target import AgentStepDeliveryReference, FleetPlanTarget
from oh_goon.core.path_layouts import ApplicationStatePathLayout
from oh_goon.core.vocabulary import (
    FleetRunEventType,
    FleetRunStatus,
    FleetRunTerminalIntent,
    OperationAction,
)
from oh_goon.engine.errors import OperationCancelledError
from oh_goon.engine.git import GitService
from oh_goon.runtime.database import LocalDatabase
from oh_goon.runtime.effect_attempts import RepositoryEffectAttemptStore
from oh_goon.runtime.execution import ExecutionCoordinator, ExecutionSession
from oh_goon.runtime.journal import Journal
from tests.factories.capabilities import capability_snapshot
from tests.factories.fleet_plan_targets import structural_execution_identity
from tests.factories.journal import start_repository
from tests.factories.repository_effect_runtime import effect_recovery
from tests.factories.run_admission import repository_admissions_for
from tests.fakes.fleet_worker import simulated_worker_coordinator


def _new_pi_target() -> FleetPlanTarget:
    connection = AgentConnection(
        profile="fixture",
        provider_name="fixture",
        provider=AgentProvider(api="openai-chat-completions", base_url="http://127.0.0.1:9/v1"),
        selection=AgentModel(provider="fixture", model="fake-model"),
    )
    native = ResolvedNativeAgent(
        agent="pi",
        profile_name="owned",
        definition=NativeAgentDefinition(executable="/fixture/pi"),
        profile=NativeAgentProfile(
            backend="bubblewrap", allowed_agents=("pi",), inference_profile="fixture", pi_rpc=True
        ),
        executable_sha256="b" * 64,
        connection=connection,
        backend_configuration_id="fixture-bubblewrap",
        session=PiSessionIntent(mode="new"),
    )
    step = ResolvedAgentStep(
        step_id="agent",
        timeout_seconds=5,
        workload=ResolvedNativeAgentWorkload(native_agent=native),
        delivery_sha256="a" * 64,
        handoff_identity="frozen-fixture",
        source_step_id="agent",
    )
    plan = ResolvedFleetPlan.create(
        name="pi-writer-journal",
        description="Durable ownership regression fixture",
        execution=MaintenanceExecutionPolicy(),
        steps=(step,),
        native_agent=native,
    )
    return FleetPlanTarget(
        kind="fleet_plan",
        plan=plan,
        source=PlanFileSource(sha256="0" * 64),
        identity=structural_execution_identity(plan),
        agent_deliveries=(
            AgentStepDeliveryReference(
                step_id=step.step_id,
                delivery_sha256=step.delivery_sha256,
                handoff_identity=step.handoff_identity,
                reference_id="retained-fixture",
            ),
        ),
    )


@dataclass(frozen=True, slots=True)
class _ClaimedPiAttempt:
    database: LocalDatabase
    journal: Journal
    coordinator: ExecutionCoordinator
    session: ExecutionSession
    repository: RepositoryIdentity
    operation_id: str

    def reserve(self) -> PiSessionWriterReservedEvent:
        return self.journal.reserve_pi_session_writer(
            self.session,
            repository_id=self.repository.repository_id,
            operation_id=self.operation_id,
            step_id="agent",
            attempt_number=1,
        )

    def record_physical(self, physical_id: str, *, quiescent: bool = True) -> None:
        observer = self.journal.execution_lifecycle_observer(
            self.session.require_fleet_run_id(), self.repository.repository_id, self.operation_id
        )
        observer(
            "agent", 1, "PROCESS_STARTING", attempt_number=1, physical_execution_id=physical_id
        )
        for event_type in (
            "PROCESS_STARTED",
            "PROCESS_EXITED",
            *(("DESCENDANTS_QUIESCENT",) if quiescent else ()),
        ):
            observer(
                "agent",
                1,
                event_type,
                attempt_number=1,
                physical_execution_id=physical_id,
                pid=100,
                process_identity="fixture-native-process",
                returncode=0 if event_type == "PROCESS_EXITED" else None,
            )


def _claim_attempt(database: LocalDatabase, fleet_run_id: str) -> _ClaimedPiAttempt:
    owner = ExecutionCoordinator(database)
    journal = Journal(database)
    repository = RepositoryIdentity(name="sample", path=database.path.parent / "repo")
    target = _new_pi_target()
    intent = FleetRunAdmissionIntent(
        target=target,
        capability=capability_snapshot(OperationAction.EXECUTE_PLAN, target),
        execution=DirectFleetExecutionAdmission(),
        repository_admissions=repository_admissions_for(target, [repository]),
    )
    ticket, _ = owner.reserve_fleet_with(
        fleet_run_id,
        expected_durable_revision=database.durable_revision(),
        mutate=lambda connection: journal.admit_run_in(
            connection,
            fleet_run_id,
            intent,
            fleet_repository_scope({repository.repository_id}),
            [repository],
        ),
    )
    worker_database, worker = simulated_worker_coordinator(database)
    journal = Journal(worker_database)
    session = worker.activate(worker.claim_fleet_worker(ticket))
    session.mutate(lambda connection: journal.start_run_in(connection, fleet_run_id))
    operation_id = start_repository(journal, fleet_run_id, repository)
    observer = journal.execution_lifecycle_observer(
        fleet_run_id, repository.repository_id, operation_id
    )
    observer("agent", 1, "STEP_REACHED")
    observer("agent", 1, "STEP_STARTED")
    observer("agent", 1, "STEP_ATTEMPT_CREATED", attempt_number=1)
    return _ClaimedPiAttempt(worker_database, journal, worker, session, repository, operation_id)


def test_pi_writer_reservation_has_one_winner_and_stable_replay_refusal(tmp_path: Path) -> None:
    database = LocalDatabase(tmp_path / "state.db")
    attempt = _claim_attempt(database, "first")
    ready = Barrier(2)

    def compete() -> PiSessionWriterReservedEvent | str:
        ready.wait(timeout=5)
        try:
            return attempt.reserve()
        except PiSessionBusy as error:
            return error.code

    with ThreadPoolExecutor(max_workers=2) as threads:
        first = threads.submit(compete)
        second = threads.submit(compete)
        results = (first.result(timeout=5), second.result(timeout=5))

    (winner,) = [item for item in results if isinstance(item, PiSessionWriterReservedEvent)]
    assert "PI_SESSION_BUSY" in results
    readback = Journal(LocalDatabase(database.path)).run_events("first")
    assert readback.events[-1] == winner
    assert winner.attempt_sequence == winner.sequence - 1
    assert attempt.session.original_fleet_lease_token() not in winner.model_dump_json()
    with pytest.raises(OwnershipLost):
        attempt.journal.require_pi_session_writer(
            attempt.session, winner.model_copy(update={"sequence": winner.sequence - 1})
        )
    with pytest.raises(PiSessionBusy):
        attempt.reserve()
    assert Journal(LocalDatabase(database.path)).run_events("first") == readback


def test_second_physical_pi_writer_is_refused_at_the_prelaunch_cut(tmp_path: Path) -> None:
    attempt = _claim_attempt(LocalDatabase(tmp_path / "state.db"), "run")
    attempt.record_physical("read-only-prework")
    reservation = attempt.reserve()

    def announce(physical_id: str) -> None:
        attempt.session.mutate(
            lambda connection: attempt.journal.record_execution_lifecycle_in(
                connection,
                fleet_run_id=reservation.fleet_run_id,
                repository_id=reservation.repository_id,
                operation_id=reservation.operation_id,
                event_type=FleetRunEventType.PROCESS_STARTING,
                step_id=reservation.step_id,
                step_order=1,
                attempt_number=1,
                physical_execution_id=physical_id,
            )
        )

    announce("first-writer")
    original = attempt.journal.run_events("run")
    with pytest.raises(InvalidRunTransition, match="crossing its launch cut"):
        announce("duplicate-writer")
    assert attempt.journal.run_events("run") == original


def test_pi_observation_needs_original_process_quiescence_even_after_cancel(tmp_path: Path) -> None:
    attempt = _claim_attempt(LocalDatabase(tmp_path / "state.db"), "cancelled")
    before_reserve = "before-reservation"
    attempt.record_physical(before_reserve)
    reservation = attempt.reserve()
    active = "original-writer"
    attempt.record_physical(active, quiescent=False)
    observation = PiSessionWriterObservation(physical_execution_id=active, persistence="unknown")
    with attempt.database.transaction(write=True) as connection:
        attempt.coordinator.request_fleet_cancel_in(connection, "cancelled")
        attempt.journal.request_cancel_in(connection, "cancelled")
    before = attempt.journal.run_events("cancelled")
    with pytest.raises(OperationCancelledError, match="fences Pi writer use"):
        attempt.journal.require_pi_session_writer(attempt.session, reservation)
    for value in (
        observation,
        observation.model_copy(update={"physical_execution_id": before_reserve}),
    ):
        with pytest.raises(InvalidRunTransition, match="process-tree quiescence"):
            attempt.journal.observe_pi_session_writer(
                attempt.session, reservation, observation=value
            )
    assert attempt.journal.run_events("cancelled") == before
    attempt.journal.execution_lifecycle_observer(
        "cancelled", attempt.repository.repository_id, attempt.operation_id
    )(
        "agent",
        1,
        "DESCENDANTS_QUIESCENT",
        attempt_number=1,
        physical_execution_id=active,
        pid=100,
        process_identity="fixture-native-process",
    )
    attempt.journal.observe_pi_session_writer(attempt.session, reservation, observation=observation)
    verified = Journal(LocalDatabase(attempt.database.path)).run_events("cancelled").events
    receipt = verified[-1]
    assert isinstance(receipt, PiSessionWriterObservedEvent)
    assert receipt.reservation_sequence == reservation.sequence
    assert receipt.observation == observation
    assert isinstance(verified[receipt.quiescent_sequence - 1], DescendantsQuiescentEvent)


@pytest.mark.parametrize("cut", ["before_allocation", "after_native_save"])
def test_restart_preserves_unobserved_writer_and_fences_late_settlement(
    tmp_path: Path, cut: str
) -> None:
    database = LocalDatabase(tmp_path / "state.db")
    original = _claim_attempt(database, "original")
    reservation = original.reserve()
    observation = PiSessionWriterObservation(
        physical_execution_id="original-writer",
        persistence="observed",
        file=PiSessionFileObservation(device=1, inode=2, size=5),
        session_id="original-native",
        leaf_id="original-native-leaf",
        file_name="session.jsonl",
    )
    if cut == "after_native_save":
        original.record_physical(observation.physical_execution_id)
        leaf = (
            ApplicationStatePathLayout.from_database(database.path).pi_sessions
            / reservation.storage_id
        )
        leaf.mkdir(parents=True)
        (leaf / "session.jsonl").write_text("saved", encoding="utf-8")
    # Open a separate runtime; the simulated claimant no longer has native birth evidence.
    recovered = LocalDatabase(database.path)
    journal = Journal(recovered)
    effect_recovery(
        recovered,
        RepositoryEffectAttemptStore(recovered),
        journal,
        ExecutionCoordinator(recovered),
        GitService(),
    ).recover(fleet_run_id="original")
    post_crash = journal.run_events("original")
    assert post_crash.run.status is FleetRunStatus.FAILED
    assert post_crash.events[reservation.sequence - 1] == reservation
    assert not any(isinstance(event, PiSessionWriterObservedEvent) for event in post_crash.events)
    replacement = _claim_attempt(recovered, "replacement")
    successor = replacement.reserve()
    assert successor.storage_id != reservation.storage_id
    assert successor.lease_binding != reservation.lease_binding
    current = journal.run_events("original")
    assert current.events == post_crash.events
    assert current.run == post_crash.run
    with pytest.raises(OwnershipLost):
        original.journal.observe_pi_session_writer(
            original.session, reservation, observation=observation
        )
    with pytest.raises(OwnershipLost):
        replacement.journal.observe_pi_session_writer(
            replacement.session, reservation, observation=observation
        )
    assert journal.run_events("original") == current
    replacement.journal.require_pi_session_writer(replacement.session, successor)
    replacement.session.finish(
        lambda connection: replacement.journal.terminalize_run_in(
            connection, "replacement", FleetRunTerminalIntent.CANCEL, None
        )
    )
