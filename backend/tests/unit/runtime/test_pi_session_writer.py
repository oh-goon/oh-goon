from __future__ import annotations

import os
import sqlite3
import sys
from collections.abc import Generator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from threading import Barrier
from unittest.mock import Mock

import pytest

from oh_goon.core.errors import (
    ExecutionBusy,
    FleetPlanInvalid,
    InvalidRunTransition,
    OwnershipLost,
    PiSessionBusy,
    PiSessionRefusalCode,
)
from oh_goon.core.models.agent_providers import (
    AgentConnection,
    AgentModel,
    AgentProvider,
    NativeAgentDefinition,
    NativeAgentProfile,
    NoPersistentPiSession,
    PiSessionCleanupUnverified,
    PiSessionIntent,
    PiSessionUnavailable,
    ResolvedNativeAgent,
)
from oh_goon.core.models.explanation import ReasonCode
from oh_goon.core.models.fleet_plan import (
    ResolvedAgentStep,
    ResolvedCommandStep,
    ResolvedFleetPlan,
    ResolvedNativeAgentWorkload,
)
from oh_goon.core.models.fleet_plan_evidence import (
    CompletedPlanStepExecution,
    FailedPlanStepExecution,
    FleetPlanResult,
    PlanOutputEvidence,
    PlanStepSettlementEvidence,
)
from oh_goon.core.models.fleet_plan_execution_policy import MaintenanceExecutionPolicy
from oh_goon.core.models.goon_plan_source import PlanFileSource
from oh_goon.core.models.operation import FleetPlanOperationEvidence
from oh_goon.core.models.pi_session_storage import (
    PiSessionFileObservation,
    PiSessionNativeObservation,
    PiSessionRetirementDirectoryIdentity,
    PiSessionRetirementPin,
    PiSessionWriterObservation,
)
from oh_goon.core.models.repository import RepositoryIdentity
from oh_goon.core.models.run import FleetRunFailure
from oh_goon.core.models.run_admission import (
    DirectFleetExecutionAdmission,
    FleetRunAdmissionIntent,
    fleet_repository_scope,
)
from oh_goon.core.models.run_events import (
    DescendantsQuiescentEvent,
    PiSessionContinuationOrigin,
    PiSessionRetiredEvent,
    PiSessionRetirementRequestedEvent,
    PiSessionWriterObservedEvent,
    PiSessionWriterReservedEvent,
)
from oh_goon.core.models.target import AgentStepDeliveryReference, FleetPlanTarget
from oh_goon.core.path_layouts import ApplicationStatePathLayout
from oh_goon.core.plan_validation_diagnostics import safe_plan_diagnostic
from oh_goon.core.vocabulary import (
    FleetRunEventType,
    FleetRunStatus,
    FleetRunTerminalIntent,
    OperationAction,
    Verdict,
)
from oh_goon.engine.errors import OperationCancelledError
from oh_goon.engine.git import GitService
from oh_goon.integrations.fleet_plan_preview import repository_plan_admission_evidence
from oh_goon.runtime.database import LocalDatabase
from oh_goon.runtime.effect_attempts import RepositoryEffectAttemptStore
from oh_goon.runtime.execution import ExecutionCoordinator, ExecutionSession, ExecutionTicket
from oh_goon.runtime.journal import Journal
from oh_goon.runtime.journal_store import JournalFactStore
from oh_goon.runtime.pi_session_storage import pin_pi_session_directory
from oh_goon.runtime.safety import RuntimeSafetyGate
from oh_goon.runtime.schedule_executions import ScheduleExecutionStore
from oh_goon.services.fleet_admission import FleetAdmissionPlanner
from oh_goon.services.invariant_auditor import SystemInvariantAuditor
from oh_goon.services.runtime_safety import RuntimeSafetyService
from oh_goon.services.target_selection import FleetTargetSelector
from tests.factories.capabilities import capability_snapshot, resolved_intent
from tests.factories.fleet_plan_targets import structural_execution_identity
from tests.factories.journal import start_repository
from tests.factories.local_state import local_state
from tests.factories.operation_reports import make_operation_report
from tests.factories.repository_effect_runtime import effect_recovery
from tests.factories.run_admission import repository_admissions_for
from tests.fakes.fleet_worker import simulated_worker_coordinator


def _pi_target(
    prior_run_id: str | None = None,
    *,
    executable_sha256: str = "b" * 64,
    following_steps: tuple[ResolvedCommandStep, ...] = (),
) -> FleetPlanTarget:
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
        executable_sha256=executable_sha256,
        connection=connection,
        backend_configuration_id="fixture-bubblewrap",
        session=PiSessionIntent(
            mode="continue" if prior_run_id is not None else "new", prior_run_id=prior_run_id
        ),
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
        steps=(step, *following_steps),
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


def _claim_attempt(
    database: LocalDatabase,
    fleet_run_id: str,
    *,
    prior_run_id: str | None = None,
    repo_name: str = "repo",
    repository_path: Path | None = None,
    executable_sha256: str = "b" * 64,
    following_steps: tuple[ResolvedCommandStep, ...] = (),
) -> _ClaimedPiAttempt:
    owner = ExecutionCoordinator(database)
    journal = Journal(database)
    repository = RepositoryIdentity.model_validate(
        {"name": "sample", "path": repository_path or database.path.parent / repo_name}
    )
    target = _pi_target(
        prior_run_id, executable_sha256=executable_sha256, following_steps=following_steps
    )
    intent = FleetRunAdmissionIntent(
        target=target,
        capability=capability_snapshot(OperationAction.EXECUTE_PLAN, target),
        execution=DirectFleetExecutionAdmission(),
        repository_admissions=(
            (repository_plan_admission_evidence(repository, target.plan),)
            if repository_path is not None
            else repository_admissions_for(target, [repository])
        ),
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


def _accepted_pi_result(
    attempt: _ClaimedPiAttempt, *, following_failure: FailedPlanStepExecution | None = None
) -> None:
    run_id = attempt.session.require_fleet_run_id()
    target = attempt.journal.run_events(run_id).run.target
    assert isinstance(target, FleetPlanTarget)
    observe = attempt.journal.execution_lifecycle_observer(
        run_id, attempt.repository.repository_id, attempt.operation_id
    )
    observe("agent", 1, "STEP_SUCCEEDED", returncode=0)
    if following_failure is not None:
        step_id = following_failure.step_id
        observe(step_id, 2, "STEP_REACHED")
        observe(step_id, 2, "STEP_STARTED")
        observe(step_id, 2, "STEP_FAILED", returncode=following_failure.returncode)
    empty_output = PlanOutputEvidence(sha256="0" * 64, byte_count=0, line_count=0)
    result = CompletedPlanStepExecution(
        step_id="agent",
        kind="agent",
        stdout_evidence=empty_output,
        stderr_evidence=empty_output,
        repository_state_before="before",
        repository_state_after="after",
        settlement=PlanStepSettlementEvidence(
            outcome="SUCCESS", cleanup="VERIFIED", reason="ACCEPTED_EXIT", observed_returncode=0
        ),
    )
    report = make_operation_report(
        attempt.repository,
        OperationAction.EXECUTE_PLAN,
        verdict=Verdict.READY if following_failure is None else Verdict.FAILED,
        reason_code=ReasonCode.READY if following_failure is None else ReasonCode.OPERATION_FAILED,
        target=target,
        fleet_run_id=run_id,
        operation_id=attempt.operation_id,
        evidence=FleetPlanOperationEvidence(
            kind="FLEET_PLAN",
            plan=FleetPlanResult(
                plan_id=target.plan.plan_id,
                steps=(result,) if following_failure is None else (result, following_failure),
            ),
        ),
    )

    def finish(connection: sqlite3.Connection) -> None:
        attempt.journal.commit_operation_in(connection, report)
        attempt.journal.terminalize_run_in(connection, run_id, FleetRunTerminalIntent.COMPLETE)

    attempt.session.finish(finish)


def _observe_pi_file(
    attempt: _ClaimedPiAttempt,
    reservation: PiSessionWriterReservedEvent,
    *,
    sha256: str,
    leaf_id: str,
) -> PiSessionWriterObservation:
    attempt.record_physical("original-writer")
    observation = PiSessionWriterObservation(
        physical_execution_id="original-writer",
        persistence="observed",
        file=PiSessionFileObservation(
            device=1,
            inode=2,
            size=10,
            sha256=sha256,
            save_verification="pi_rpc_synced",
        ),
        session_id="native-session",
        leaf_id=leaf_id,
        file_name="session.jsonl",
    )
    attempt.journal.observe_pi_session_writer(attempt.session, reservation, observation=observation)
    return observation


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
    assert "continuation" not in winner.model_dump(mode="json")
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


@pytest.mark.parametrize("offer_observed_file", [False, True])
def test_pi_observation_needs_original_process_quiescence_even_after_cancel(
    tmp_path: Path, offer_observed_file: bool
) -> None:
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
    offered = (
        PiSessionWriterObservation(
            physical_execution_id=active,
            persistence="observed",
            file=PiSessionFileObservation(device=1, inode=2, size=10, sha256="a" * 64),
            session_id="native-session",
            leaf_id="leaf-a",
            file_name="session.jsonl",
        )
        if offer_observed_file
        else observation
    )
    attempt.journal.observe_pi_session_writer(attempt.session, reservation, observation=offered)
    verified = Journal(LocalDatabase(attempt.database.path)).run_events("cancelled").events
    receipt = verified[-1]
    assert isinstance(receipt, PiSessionWriterObservedEvent)
    assert receipt.reservation_sequence == reservation.sequence
    assert receipt.observation == observation
    assert isinstance(verified[receipt.quiescent_sequence - 1], DescendantsQuiescentEvent)


def test_continuation_retains_exact_predecessor_and_advances_only_latest_writer(
    tmp_path: Path,
) -> None:
    database = LocalDatabase(tmp_path / "state.db")
    original = _claim_attempt(database, "original")
    created = original.reserve()
    first = _observe_pi_file(original, created, sha256="a" * 64, leaf_id="leaf-a")
    original_lease = original.coordinator.active_lease()
    assert original_lease is not None
    late_session = original.coordinator.activate(ExecutionTicket(original_lease))
    _accepted_pi_result(original)

    reopened = _claim_attempt(LocalDatabase(database.path), "next", prior_run_id="original")
    continued = reopened.reserve()
    assert continued.storage_id == created.storage_id
    assert continued.lease_binding != created.lease_binding
    event = next(
        item
        for item in original.journal.run_events("original").events
        if isinstance(item, PiSessionWriterObservedEvent)
    )
    assert continued.continuation == PiSessionContinuationOrigin(
        prior_run_id="original",
        reservation_sequence=created.sequence,
        observation_sequence=event.sequence,
    )
    assert continued.continuation is not None
    assert reopened.journal.pi_session_continuation_source(reopened.session, continued) == (
        first.file,
        PiSessionNativeObservation(session_id="native-session", leaf_id="leaf-a"),
    )
    reader = Journal(LocalDatabase(database.path, read_only=True))
    before_original = reader.run_events("original")
    before_stale_callback = reader.run_events("next")
    next_lease = reopened.coordinator.active_lease()
    assert next_lease is not None and next_lease.fleet_run_id == "next"
    assert late_session.finished is False
    with pytest.raises(OwnershipLost):
        original.journal.observe_pi_session_writer(late_session, created, observation=first)
    with pytest.raises(OwnershipLost):
        original.journal.require_pi_session_writer(late_session, created)
    terminalize = Mock()
    with pytest.raises(OwnershipLost):
        late_session.finish(terminalize)
    terminalize.assert_not_called()
    assert reader.run_events("original") == before_original
    assert reader.run_events("next") == before_stale_callback
    assert reopened.coordinator.active_lease() == next_lease
    reopened.journal.require_pi_session_writer(reopened.session, continued)
    replacement = continued.model_copy(
        update={
            "continuation": continued.continuation.model_copy(update={"observation_sequence": 99})
        }
    )
    with pytest.raises(OwnershipLost):
        reopened.journal.pi_session_continuation_source(reopened.session, replacement)
    second = _observe_pi_file(reopened, continued, sha256="b" * 64, leaf_id="leaf-b")
    _accepted_pi_result(reopened)

    before = reopened.journal.read_view()
    with pytest.raises(PiSessionUnavailable, match="newer writer"):
        _claim_attempt(database, "stale", prior_run_id="original")
    after = reopened.journal.read_view()
    assert after.revision == before.revision
    assert after.run_detail("stale") is None
    assert ExecutionCoordinator(database).active_lease() is None

    latest = _claim_attempt(database, "latest", prior_run_id="next")
    reservation = latest.reserve()
    assert reservation.storage_id == created.storage_id
    assert latest.journal.pi_session_continuation_source(latest.session, reservation) == (
        second.file,
        PiSessionNativeObservation(session_id="native-session", leaf_id="leaf-b"),
    )


@pytest.mark.parametrize("cut", ["metadata-only", "digest-only", "before-original-acceptance"])
def test_continuation_refuses_unqualified_original_persistence(
    tmp_path: Path, cut: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = LocalDatabase(tmp_path / "state.db")
    original = _claim_attempt(database, "original")
    reservation = original.reserve()
    if cut != "before-original-acceptance":
        original.record_physical("original-writer")
        last = original.journal.run_events("original").events[-1]
        assert isinstance(last, DescendantsQuiescentEvent)
        legacy = PiSessionWriterObservedEvent(
            event_type=FleetRunEventType.PI_SESSION_WRITER_OBSERVED,
            sequence=last.sequence + 1,
            fleet_run_id="original",
            reservation_sequence=reservation.sequence,
            quiescent_sequence=last.sequence,
            observation=PiSessionWriterObservation(
                physical_execution_id="original-writer",
                persistence="observed",
                file=PiSessionFileObservation(
                    device=1, inode=2, size=10, sha256="a" * 64 if cut == "digest-only" else None
                ),
                session_id="native-session",
                leaf_id="leaf-a",
                file_name="session.jsonl",
            ),
        )
        # Historical metadata-only and digest-only facts remain readable.
        assert legacy.observation.file is not None
        historical_file = legacy.observation.file.model_dump(mode="json")
        assert ("sha256" in historical_file) == (cut == "digest-only")
        assert "save_verification" not in historical_file
        if cut == "digest-only":
            before_publication = original.journal.run_events("original")
            with pytest.raises(InvalidRunTransition, match="require a verified native save"):
                original.journal.observe_pi_session_writer(
                    original.session, reservation, observation=legacy.observation
                )
            assert original.journal.run_events("original") == before_publication
        original.session.mutate(
            lambda connection: JournalFactStore(original.database).append_run_event(
                connection, legacy
            )
        )
        _accepted_pi_result(original)
        reader = Journal(LocalDatabase(database.path, read_only=True))
        assert reader.run_events("original").events[legacy.sequence - 1] == legacy
        inspected = reader.inspect_pi_session("original")
        assert inspected.availability == "unverified"
        assert inspected.refusal_code is PiSessionRefusalCode.HISTORY_UNQUALIFIED
        reason = (
            "no verified native save receipt"
            if cut == "digest-only"
            else "no qualified persistence observation"
        )
        code = PiSessionRefusalCode.HISTORY_UNQUALIFIED
    else:
        _observe_pi_file(original, reservation, sha256="a" * 64, leaf_id="leaf-a")
        recovered = LocalDatabase(database.path)
        effect_recovery(
            recovered,
            RepositoryEffectAttemptStore(recovered),
            Journal(recovered),
            ExecutionCoordinator(recovered),
            GitService(),
        ).recover(fleet_run_id="original")
        reason = "no accepted original Agent result"
        code = PiSessionRefusalCode.SETTLEMENT_UNCERTAIN
    before = original.journal.read_view()
    with pytest.raises(PiSessionUnavailable, match=reason) as refusal:
        _claim_attempt(database, "contender", prior_run_id="original")
    assert refusal.value.code is code
    diagnostic = safe_plan_diagnostic(refusal.value)
    assert diagnostic["code"] == code
    assert "runs handoff RUN_ID --json" in diagnostic["safe_next_action"]
    after = original.journal.read_view()
    assert after.revision == before.revision
    assert after.run_detail("contender") is None
    assert ExecutionCoordinator(database).active_lease() is None

    if cut == "digest-only":
        # Finish a retirement already committed by the earlier implementation.
        events = after.run_events("original").events
        request = PiSessionRetirementRequestedEvent(
            event_type=FleetRunEventType.PI_SESSION_RETIREMENT_REQUESTED,
            fleet_run_id="original",
            sequence=len(events) + 1,
            reservation_sequence=reservation.sequence,
            observation_sequence=legacy.sequence,
            directory=PiSessionRetirementDirectoryIdentity(device=7, inode=11),
        )
        with database.transaction(write=True) as connection:
            JournalFactStore(database).append_run_event(connection, request)
        journal = Journal(LocalDatabase(database.path))
        assert journal.inspect_pi_session("original").retirement == "pending"
        remove = Mock()

        @contextmanager
        def pin(**options: object) -> Generator[PiSessionRetirementPin, None, None]:
            assert options["expected_directory"] == request.directory
            assert options["expected_file"] == legacy.observation.file
            yield PiSessionRetirementPin(directory=request.directory, remove=remove)

        monkeypatch.setattr("oh_goon.runtime.journal.pin_pi_session_retirement", pin)
        assert journal.retire_pi_session("original").request_sequence == request.sequence
        remove.assert_called_once_with()
        inspected = Journal(LocalDatabase(database.path, read_only=True)).inspect_pi_session(
            "original"
        )
        assert (inspected.availability, inspected.retirement) == ("unavailable", "verified")


@pytest.mark.parametrize(
    ("prior_run_id", "repo_name", "executable_sha256", "reason", "code"),
    [
        (
            "unknown",
            "repo",
            "b" * 64,
            "no persistent Pi writer",
            PiSessionRefusalCode.NO_PERSISTENT_SESSION,
        ),
        (
            "original",
            "another",
            "b" * 64,
            "different repository subject",
            PiSessionRefusalCode.IDENTITY_MISMATCH,
        ),
        (
            "original",
            "repo",
            "c" * 64,
            "different native implementation",
            PiSessionRefusalCode.NATIVE_CONFIGURATION_MISMATCH,
        ),
    ],
)
def test_pi_continuation_admission_refuses_unknown_or_foreign_prior_before_work(
    tmp_path: Path,
    prior_run_id: str,
    repo_name: str,
    executable_sha256: str,
    reason: str,
    code: PiSessionRefusalCode,
) -> None:
    database = LocalDatabase(tmp_path / "state.db")
    original = _claim_attempt(database, "original")
    reservation = original.reserve()
    _observe_pi_file(original, reservation, sha256="a" * 64, leaf_id="leaf-a")
    _accepted_pi_result(original)
    before = original.journal.read_view()

    with pytest.raises(PiSessionUnavailable, match=reason) as refusal:
        _claim_attempt(
            database,
            "refused",
            prior_run_id=prior_run_id,
            repo_name=repo_name,
            executable_sha256=executable_sha256,
        )

    assert refusal.value.code is code
    after = original.journal.read_view()
    assert after.revision == before.revision
    assert after.run_detail("refused") is None
    assert ExecutionCoordinator(database).active_lease() is None


def test_pi_busy_refusal_requires_an_active_writer_of_the_requested_lineage(tmp_path: Path) -> None:
    original = _claim_attempt(LocalDatabase(tmp_path / "state.db"), "original")
    original.reserve()
    database = original.database
    attempts = RepositoryEffectAttemptStore(database)
    auditor = SystemInvariantAuditor(
        database, original.journal, original.coordinator, attempts, ScheduleExecutionStore(database)
    )
    planner = FleetAdmissionPlanner(
        local_state(database),
        Mock(spec=FleetTargetSelector),
        auditor,
        RuntimeSafetyService(auditor, RuntimeSafetyGate()),
        execution=original.coordinator,
    )
    original_scope = fleet_repository_scope({original.repository.repository_id})
    before = original.journal.read_view().revision
    with pytest.raises(PiSessionBusy) as busy:
        planner.plan(
            resolved_intent(OperationAction.EXECUTE_PLAN, _pi_target("original")),
            repository_scope=original_scope,
        )
    assert safe_plan_diagnostic(busy.value)["code"] == "PI_SESSION_BUSY"
    with pytest.raises(ExecutionBusy):
        planner.plan(
            resolved_intent(OperationAction.EXECUTE_PLAN, _pi_target("unrelated")),
            repository_scope=original_scope,
        )
    assert original.journal.read_view().revision == before


def test_pi_writer_without_quiescence_reports_cleanup_uncertainty_after_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = LocalDatabase(tmp_path / "state.db")
    original = _claim_attempt(database, "original")
    original.reserve()
    observe = original.journal.execution_lifecycle_observer(
        "original", original.repository.repository_id, original.operation_id
    )
    observe("agent", 1, "PROCESS_STARTING", attempt_number=1, physical_execution_id="writer")
    for kind in ("PROCESS_STARTED", "PROCESS_STATE_UNKNOWN"):
        observe(
            "agent",
            1,
            kind,
            attempt_number=1,
            physical_execution_id="writer",
            pid=123,
            process_identity="original-birth",
        )
    original.session.finish(
        lambda connection: original.journal.terminalize_run_in(
            connection,
            "original",
            FleetRunTerminalIntent.FAIL,
            FleetRunFailure.recovery("Original descendants have no quiescence proof"),
        )
    )
    reopened = LocalDatabase(database.path)
    before = Journal(reopened).run_events("original")
    with pytest.raises(PiSessionUnavailable, match="cleanup is unverified") as refusal:
        _claim_attempt(reopened, "contender", prior_run_id="original")
    assert refusal.value.code is PiSessionRefusalCode.CLEANUP_UNVERIFIED
    inspect_storage = Mock()
    monkeypatch.setattr(
        "oh_goon.runtime.journal.inspect_recovered_pi_session_storage", inspect_storage
    )
    inspection = Journal(reopened).inspect_pi_session("original")
    assert inspection.availability == "unverified"
    assert inspection.refusal_code is PiSessionRefusalCode.CLEANUP_UNVERIFIED
    inspect_storage.assert_not_called()
    assert Journal(reopened).run_events("original") == before
    with pytest.raises(PiSessionCleanupUnverified):
        Journal(reopened).retire_pi_session("original")
    assert Journal(reopened).run_events("original") == before


@pytest.mark.parametrize("unlink_interrupted", [False, True])
def test_pi_retirement_revokes_before_unlink_and_converges_on_exact_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, unlink_interrupted: bool
) -> None:
    database = LocalDatabase(tmp_path / "state.db")
    original = _claim_attempt(database, "original")
    writer = original.reserve()
    observed = _observe_pi_file(original, writer, sha256="a" * 64, leaf_id="old-leaf")
    _accepted_pi_result(original)
    historical = original.journal.run_events("original")
    directory = PiSessionRetirementDirectoryIdentity(device=7, inode=11)
    present, remove_attempts = True, 0
    expected_directories: list[PiSessionRetirementDirectoryIdentity | None] = []

    @contextmanager
    def pin(
        *,
        layout: ApplicationStatePathLayout,
        storage_id: str,
        expected_file: PiSessionFileObservation,
        expected_directory: PiSessionRetirementDirectoryIdentity | None = None,
    ) -> Generator[PiSessionRetirementPin, None, None]:
        assert layout == ApplicationStatePathLayout.from_database(database.path)
        assert storage_id == writer.storage_id
        assert expected_file == observed.file
        expected_directories.append(expected_directory)
        if expected_directory is None and not present:
            raise PiSessionUnavailable("Original Pi session file is absent")

        def remove() -> None:
            nonlocal present, remove_attempts
            remove_attempts += 1
            if remove_attempts == 1:
                if unlink_interrupted:
                    present = False
                raise PiSessionCleanupUnverified("Original Pi retirement cleanup is unverified")
            assert expected_directory == directory
            present = False

        yield PiSessionRetirementPin(directory=directory, remove=remove)

    monkeypatch.setattr("oh_goon.runtime.journal.pin_pi_session_retirement", pin)
    inspector = Journal(LocalDatabase(database.path, read_only=True))
    inspection = inspector.inspect_pi_session("original")
    assert inspection.availability == "available"
    assert inspection.native == PiSessionNativeObservation(
        session_id="native-session", leaf_id="old-leaf"
    )
    assert original.journal.run_events("original") == historical
    present = False
    missing = inspector.inspect_pi_session("original")
    assert missing.availability == "unavailable" and missing.retirement == "none"
    assert missing.refusal_code is PiSessionRefusalCode.UNAVAILABLE
    assert original.journal.run_events("original") == historical
    present = True

    with pytest.raises(PiSessionCleanupUnverified):
        original.journal.retire_pi_session("original")
    reopened = Journal(LocalDatabase(database.path))
    pending = reopened.run_events("original")
    assert pending.run == historical.run
    assert pending.events[:-1] == historical.events
    request = pending.events[-1]
    assert isinstance(request, PiSessionRetirementRequestedEvent)
    assert request.directory == directory
    assert request.reservation_sequence == writer.sequence
    assert reopened.inspect_pi_session("original").retirement == "pending"
    with pytest.raises(PiSessionUnavailable, match="pending retirement"):
        _claim_attempt(database, "revoked", prior_run_id="original")
    assert reopened.run_events("original") == pending

    retired = reopened.retire_pi_session("original")
    assert isinstance(retired, PiSessionRetiredEvent)
    assert retired.request_sequence == request.sequence
    assert reopened.inspect_pi_session("original").availability == "unavailable"
    assert reopened.inspect_pi_session("original").retirement == "verified"
    assert reopened.retire_pi_session("original") == retired
    assert reopened.run_events("original").events == (*pending.events, retired)
    assert remove_attempts == 2 and not present
    assert expected_directories == [None, None, None, directory]


def test_pi_retirement_refuses_live_stale_and_foreign_writers_before_storage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = LocalDatabase(tmp_path / "state.db")
    original = _claim_attempt(database, "original")
    original_writer = original.reserve()
    pin = Mock(side_effect=AssertionError("unqualified retirement reached native storage"))
    monkeypatch.setattr("oh_goon.runtime.journal.pin_pi_session_retirement", pin)

    with pytest.raises(PiSessionBusy):
        original.journal.retire_pi_session("original")
    foreign = Journal(LocalDatabase(tmp_path / "foreign" / "state.db"))
    with pytest.raises(NoPersistentPiSession):
        foreign.retire_pi_session("original")

    _observe_pi_file(original, original_writer, sha256="a" * 64, leaf_id="old-leaf")
    _accepted_pi_result(original)
    successor = _claim_attempt(database, "successor", prior_run_id="original")
    second_writer = successor.reserve()
    _observe_pi_file(successor, second_writer, sha256="b" * 64, leaf_id="current-leaf")
    _accepted_pi_result(successor)
    before = successor.journal.run_events("original")
    with pytest.raises(PiSessionUnavailable, match="newer writer reservation"):
        original.journal.retire_pi_session("original")
    assert successor.journal.run_events("original") == before
    pin.assert_not_called()


def test_pi_retirement_fences_a_previously_admitted_but_unreserved_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = LocalDatabase(tmp_path / "state.db")
    original = _claim_attempt(database, "original")
    writer = original.reserve()
    _observe_pi_file(original, writer, sha256="a" * 64, leaf_id="old-leaf")
    _accepted_pi_result(original)
    contender = _claim_attempt(database, "contender", prior_run_id="original")
    remove = Mock()

    @contextmanager
    def pin(**_options: object) -> Generator[PiSessionRetirementPin, None, None]:
        yield PiSessionRetirementPin(
            directory=PiSessionRetirementDirectoryIdentity(device=7, inode=11), remove=remove
        )

    monkeypatch.setattr("oh_goon.runtime.journal.pin_pi_session_retirement", pin)
    original.journal.retire_pi_session("original")
    before = contender.journal.run_events("contender")
    with pytest.raises(PiSessionUnavailable, match="retired or pending retirement"):
        contender.reserve()
    assert contender.journal.run_events("contender") == before
    remove.assert_called_once_with()


def test_concurrent_pi_retirement_uses_one_durable_request_and_native_effect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = LocalDatabase(tmp_path / "state.db")
    original = _claim_attempt(database, "original")
    writer = original.reserve()
    _observe_pi_file(original, writer, sha256="a" * 64, leaf_id="old-leaf")
    _accepted_pi_result(original)
    before = original.journal.run_events("original")
    ready = Barrier(2)
    remove = Mock()

    @contextmanager
    def pin(**_options: object) -> Generator[PiSessionRetirementPin, None, None]:
        ready.wait(timeout=5)
        yield PiSessionRetirementPin(
            directory=PiSessionRetirementDirectoryIdentity(device=7, inode=11), remove=remove
        )

    monkeypatch.setattr("oh_goon.runtime.journal.pin_pi_session_retirement", pin)
    with ThreadPoolExecutor(max_workers=2) as threads:
        contenders = [
            threads.submit(Journal(LocalDatabase(database.path)).retire_pi_session, "original")
            for _ in range(2)
        ]
        first, second = [contender.result(timeout=5) for contender in contenders]
    assert first == second
    request, completion = original.journal.run_events("original").events[len(before.events) :]
    assert isinstance(request, PiSessionRetirementRequestedEvent)
    assert completion == first and first.request_sequence == request.sequence
    remove.assert_called_once_with()


def test_interrupted_successor_never_releases_an_older_pi_predecessor(tmp_path: Path) -> None:
    database = LocalDatabase(tmp_path / "state.db")
    original = _claim_attempt(database, "original")
    created = original.reserve()
    _observe_pi_file(original, created, sha256="a" * 64, leaf_id="leaf-a")
    _accepted_pi_result(original)
    successor = _claim_attempt(database, "successor", prior_run_id="original")
    claimed = successor.reserve()
    assert claimed.storage_id == created.storage_id

    recovered = LocalDatabase(database.path)
    journal = Journal(recovered)
    effect_recovery(
        recovered,
        RepositoryEffectAttemptStore(recovered),
        journal,
        ExecutionCoordinator(recovered),
        GitService(),
    ).recover(fleet_run_id="successor")
    cut = journal.run_events("successor")
    assert cut.run.status is FleetRunStatus.FAILED
    assert cut.events[claimed.sequence - 1] == claimed
    for selected, reason in (
        ("original", "newer writer"),
        ("successor", "no qualified persistence observation"),
    ):
        with pytest.raises(PiSessionUnavailable, match=reason):
            _claim_attempt(recovered, "later", prior_run_id=selected)
        assert journal.run_events("successor") == cut
        assert journal.read_view().run_detail("later") is None


@pytest.mark.parametrize("cut", ["before_allocation", "after_native_save"])
def test_restart_preserves_unobserved_writer_and_fences_late_settlement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cut: str
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
    evidence = post_crash.pi_session
    assert evidence is not None
    assert evidence == journal.read_view().required_run_detail("original").game_tape.pi_session
    assert (evidence.persistence, evidence.native, evidence.availability) == (
        "unknown",
        None,
        "not_checked",
    )
    assert (evidence.quiescent_sequence is not None) == (cut == "after_native_save")
    selected_availability = "orphaned" if cut == "after_native_save" else "unavailable"
    observe_storage = Mock(return_value=selected_availability)
    monkeypatch.setattr(
        "oh_goon.runtime.journal.inspect_recovered_pi_session_storage", observe_storage
    )
    inspection = Journal(LocalDatabase(database.path, read_only=True)).inspect_pi_session(
        "original"
    )
    assert inspection.availability == selected_availability
    assert inspection.refusal_code is PiSessionRefusalCode.SETTLEMENT_UNCERTAIN
    assert inspection.native is None
    observe_storage.assert_called_once_with(
        layout=ApplicationStatePathLayout.from_database(database.path),
        storage_id=reservation.storage_id,
    )
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


@pytest.mark.skipif(sys.platform != "linux", reason="Linux native directory custody")
def test_pi_interrupted_allocation_follows_committed_writer_reservation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = tmp_path / "repo"
    repository.mkdir()
    (repository / "source.txt").write_text("original source", encoding="utf-8")
    assert GitService().run("init", "-q", str(repository)).returncode == 0
    database = LocalDatabase(tmp_path / "state" / "state.db")
    original = _claim_attempt(database, "original", repository_path=repository)
    reservation = original.reserve()
    reader = Journal(LocalDatabase(database.path, read_only=True))
    detail = reader.read_view().required_run_detail("original")
    assert reader.inspect_pi_session("original").availability == "busy"
    layout = ApplicationStatePathLayout.from_database(database.path)
    leaf = layout.pi_sessions / reservation.storage_id
    native_mkdir = os.mkdir
    observed_allocations: list[str] = []

    def mkdir(path: str, mode: int = 0o777, *, dir_fd: int | None = None) -> None:
        if dir_fd is not None and path in (layout.pi_sessions.name, reservation.storage_id):
            assert reader.run_events("original").events[reservation.sequence - 1] == reservation
            observed_allocations.append(path)
        native_mkdir(path, mode, dir_fd=dir_fd)
        if path == reservation.storage_id:
            raise InterruptedError("controlled interruption after slot creation")

    with monkeypatch.context() as patch:
        patch.setattr(os, "mkdir", mkdir)
        with (
            pytest.raises(FleetPlanInvalid, match="storage is unavailable"),
            pin_pi_session_directory(
                layout=layout,
                fleet_run_id=reservation.fleet_run_id,
                repository=original.repository,
                repository_admission=detail.repository_admissions[0],
                step_id=reservation.step_id,
                attempt_number=reservation.attempt_number,
                workspace=repository,
                require_current=partial(
                    original.journal.require_pi_session_writer, original.session, reservation
                ),
            ),
        ):
            pytest.fail("Interrupted allocation reached Pi")
    assert observed_allocations == [layout.pi_sessions.name, reservation.storage_id]
    assert list(leaf.iterdir()) == []

    recovered = LocalDatabase(database.path)
    journal = Journal(recovered)
    effect_recovery(
        recovered,
        RepositoryEffectAttemptStore(recovered),
        journal,
        ExecutionCoordinator(recovered),
        GitService(),
    ).recover(fleet_run_id="original")
    completed = Journal(LocalDatabase(database.path, read_only=True))
    assert completed.run_events("original").run.status is FleetRunStatus.FAILED
    inspection = completed.inspect_pi_session("original")
    assert inspection.availability == "unavailable"
    assert inspection.refusal_code is PiSessionRefusalCode.SETTLEMENT_UNCERTAIN
    assert list(leaf.iterdir()) == []


def test_pi_continuation_requires_original_repository_resource_settlement(tmp_path: Path) -> None:
    database = LocalDatabase(tmp_path / "state.db")
    verifier = ResolvedCommandStep(step_id="verify", argv=("/usr/bin/true",), timeout_seconds=5)
    original = _claim_attempt(database, "original", following_steps=(verifier,))
    reservation = original.reserve()
    _observe_pi_file(original, reservation, sha256="a" * 64, leaf_id="last-leaf")
    empty_output = PlanOutputEvidence(sha256="0" * 64, byte_count=0, line_count=0)
    failed_verifier = FailedPlanStepExecution(
        step_id=verifier.step_id,
        kind="command",
        returncode=1,
        stdout_evidence=empty_output,
        stderr_evidence=empty_output,
        repository_state_before="before",
        repository_state_after="after",
        settlement=PlanStepSettlementEvidence(
            outcome="INCOMPLETE",
            cleanup="UNVERIFIED",
            reason="CLEANUP_UNVERIFIED",
            observed_returncode=0,
        ),
    )
    _accepted_pi_result(original, following_failure=failed_verifier)

    reopened = LocalDatabase(database.path)
    journal = Journal(reopened)
    history = journal.read_view()
    detail = history.required_run_detail("original")
    assert detail.run.status.terminal
    assert detail.operations[0].resource_settlement == "UNRESOLVED"
    inspect = journal.inspect_pi_session("original")
    assert inspect.availability == "unverified"
    assert inspect.refusal_code is PiSessionRefusalCode.CLEANUP_UNVERIFIED
    with pytest.raises(PiSessionCleanupUnverified, match="Original Run resource cleanup"):
        _claim_attempt(reopened, "contender", prior_run_id="original")
    with pytest.raises(PiSessionCleanupUnverified, match="Original Run resource cleanup"):
        journal.retire_pi_session("original")
    final = journal.read_view()
    assert final.revision == history.revision
    assert final.required_run_detail("original") == detail
    assert final.run_detail("contender") is None
    assert ExecutionCoordinator(reopened).active_lease() is None
