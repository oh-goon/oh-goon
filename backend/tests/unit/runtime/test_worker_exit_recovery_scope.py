"""Scoped worker recovery requires its exact dead predecessor and original writer."""

from __future__ import annotations

from datetime import UTC, datetime
from functools import partial
from pathlib import Path

import pytest

from oh_goon.core.errors import ExecutionBusy, OwnershipLost, RunNotFound, StateConflict
from oh_goon.core.models.repository import RepositoryIdentity
from oh_goon.core.models.run import FleetRunFailure
from oh_goon.core.models.run_admission import (
    DirectFleetExecutionAdmission,
    FleetRunAdmissionIntent,
    fleet_repository_scope,
)
from oh_goon.core.models.run_events import ProcessExitedEvent
from oh_goon.core.product_identity import repository_attempt_operation_id
from oh_goon.core.vocabulary import (
    EffectAttemptStatus,
    EffectBoundaryKind,
    FleetRunEventType,
    FleetRunStatus,
    FleetRunTerminalIntent,
    OperationAction,
)
from oh_goon.engine.git import GitService
from oh_goon.runtime.database import LocalDatabase
from oh_goon.runtime.effect_attempts import RepositoryEffectAttemptStore
from oh_goon.runtime.execution import ExecutionCoordinator, ExecutionTicket
from oh_goon.runtime.executor_pool import FleetExecutorPool
from oh_goon.runtime.journal import Journal
from oh_goon.services import effect_recovery as recovery_owner
from oh_goon.services.effect_recovery import RepositoryEffectRecovery
from oh_goon.services.fleet_run_lifecycle import FleetRunLifecycle
from oh_goon.services.reconciliation import RepositoryReconciler
from oh_goon.services.repository_effect_settlement import RepositoryEffectSettlement
from tests.factories.capabilities import capability_snapshot


class _Environment:
    """Declared process identities for SQLite policy tests, not native custody proof."""

    def __init__(self, pid: int, alive: set[int], births: dict[int, str]) -> None:
        self.pid, self.alive, self.births = pid, alive, births

    def now(self) -> datetime:
        return datetime(2026, 11, 5, tzinfo=UTC)

    def process_id(self) -> int:
        return self.pid

    def process_alive(self, pid: int) -> bool:
        return pid in self.alive

    def process_identity(self, pid: int) -> str | None:
        return self.births.get(pid) if pid in self.alive else None


def _admit(
    database: LocalDatabase,
    owner: ExecutionCoordinator,
    run_id: str,
    *,
    targets: list[RepositoryIdentity] | None = None,
    action: OperationAction = OperationAction.INIT,
) -> ExecutionTicket:
    journal = Journal(database)
    intent = FleetRunAdmissionIntent(
        capability=capability_snapshot(action),
        execution=DirectFleetExecutionAdmission(),
        repository_admissions=(),
    )
    captured = targets or []
    ticket, _ = owner.reserve_fleet_with(
        run_id,
        expected_durable_revision=database.durable_revision(),
        mutate=lambda connection: journal.admit_run_in(
            connection,
            run_id,
            intent,
            fleet_repository_scope({item.repository_id for item in captured} if captured else None),
            captured,
        ),
    )
    return ticket


def _worker(
    tmp_path: Path,
) -> tuple[LocalDatabase, ExecutionCoordinator, _Environment]:
    database = LocalDatabase(tmp_path / "runtime.db")
    alive = {101, 202, 303}
    births = {pid: f"birth-{pid}" for pid in alive}
    parent = ExecutionCoordinator(database, lease_environment=_Environment(101, alive, births))
    ticket = _admit(database, parent, "worker-run")
    worker_database = LocalDatabase.open_worker(database.capture_worker_context())
    worker = ExecutionCoordinator(
        worker_database, lease_environment=_Environment(202, alive, births)
    )
    worker.claim_fleet_worker(ticket)
    environment = _Environment(303, alive, births)
    return database, ExecutionCoordinator(database, lease_environment=environment), environment


def _recovery(database: LocalDatabase, execution: ExecutionCoordinator) -> RepositoryEffectRecovery:
    attempts = RepositoryEffectAttemptStore(database)
    journal = Journal(database)
    return RepositoryEffectRecovery(
        database,
        attempts,
        journal,
        execution,
        RepositoryReconciler(GitService()),
        RepositoryEffectSettlement(database, journal, attempts),
    )


def _custody(database: LocalDatabase) -> tuple[object, ...] | None:
    with database.transaction() as connection:
        row = connection.execute(
            "SELECT kind,fleet_run_id,custody_phase,owner_session_id,owner_pid,owner_identity"
            " FROM execution_lease WHERE singleton=1"
        ).fetchone()
    return tuple(row) if row is not None else None


def test_dead_worker_assertion_is_effect_free_and_bound_to_canonical_writer(tmp_path: Path) -> None:
    database, owner, environment = _worker(tmp_path)
    environment.alive.remove(202)
    before, revision = _custody(database), database.durable_revision()
    with database.transaction(write=True) as connection:
        owner.assert_dead_fleet_worker_in(connection, "worker-run")
    assert _custody(database) == before
    assert database.durable_revision() == revision
    with database.transaction() as connection, pytest.raises(RuntimeError):
        owner.assert_dead_fleet_worker_in(connection, "worker-run")
    assert _custody(database) == before


@pytest.mark.parametrize("liveness", ("live", "unproven-birth", "reused-live-pid"))
def test_scoped_worker_recovery_cannot_infer_death_from_inactive_or_ambiguous_identity(
    tmp_path: Path, liveness: str
) -> None:
    database, owner, environment = _worker(tmp_path)
    if liveness == "unproven-birth":
        del environment.births[202]
    elif liveness == "reused-live-pid":
        environment.births[202] = "another-process-birth"
        # The ordinary active projection can distinguish PID reuse; scoped
        # recovery still requires the stronger explicit native death condition.
        assert owner.active_lease() is None
    before, revision = _custody(database), database.durable_revision()
    with pytest.raises(ExecutionBusy):
        _recovery(database, owner).recover(fleet_run_id="worker-run")
    assert _custody(database) == before
    assert database.durable_revision() == revision
    assert (
        Journal(database).read_view().required_run_detail("worker-run").run.status
        is FleetRunStatus.QUEUED
    )


@pytest.mark.parametrize("attack", ("missing", "wrong-run", "direct", "unclaimed", "recovery"))
def test_scoped_recovery_rejects_missing_or_rebound_custody_before_any_mutation(
    tmp_path: Path, attack: str
) -> None:
    database, owner, environment = _worker(tmp_path)
    environment.alive.remove(202)
    # Establish the valid baseline before each deliberately corrupt/rebound cut.
    with database.transaction(write=True) as connection:
        owner.assert_dead_fleet_worker_in(connection, "worker-run")
        if attack == "missing":
            connection.execute("DELETE FROM execution_lease")
        elif attack == "wrong-run":
            connection.execute("UPDATE execution_lease SET fleet_run_id='another-run'")
        elif attack == "direct":
            connection.execute(
                "UPDATE execution_lease SET kind='DIRECT',fleet_run_id=NULL,custody_phase='DIRECT_OWNED'"
            )
        elif attack == "unclaimed":
            connection.execute("UPDATE execution_lease SET custody_phase='ADMITTED_UNCLAIMED'")
        else:
            connection.execute(
                "UPDATE execution_lease SET custody_phase='RECOVERY_OWNED',owner_session_id='recovery:original'"
            )
    before, revision = _custody(database), database.durable_revision()
    with pytest.raises(OwnershipLost):
        _recovery(database, owner).recover(fleet_run_id="worker-run")
    assert _custody(database) == before
    assert database.durable_revision() == revision
    assert (
        Journal(database).read_view().required_run_detail("worker-run").run.status
        is FleetRunStatus.QUEUED
    )


def test_scoped_recovery_refuses_an_unadmitted_run_before_inspecting_other_work(
    tmp_path: Path,
) -> None:
    database, owner, _environment = _worker(tmp_path)
    before, revision = _custody(database), database.durable_revision()
    with pytest.raises(RunNotFound):
        _recovery(database, owner).recover(fleet_run_id="not-admitted")
    assert _custody(database) == before
    assert database.durable_revision() == revision


@pytest.mark.parametrize("another_worker_alive", (False, True), ids=("dead-other", "live-other"))
def test_terminal_scope_does_not_affect_another_worker(
    tmp_path: Path, another_worker_alive: bool
) -> None:
    database = LocalDatabase(tmp_path / "runtime.db")
    alive, births = {101, 202, 303}, {101: "birth-101", 202: "birth-202", 303: "birth-303"}
    parent = ExecutionCoordinator(database, lease_environment=_Environment(101, alive, births))
    terminal = _admit(database, parent, "already-cancelled")
    parent.activate_submission_failure(terminal).finish(
        lambda connection: Journal(database).terminalize_run_in(
            connection, "already-cancelled", FleetRunTerminalIntent.CANCEL
        )
    )
    ticket = _admit(database, parent, "another-worker")
    worker_database = LocalDatabase.open_worker(database.capture_worker_context())
    worker = ExecutionCoordinator(
        worker_database, lease_environment=_Environment(202, alive, births)
    )
    worker.claim_fleet_worker(ticket)
    if not another_worker_alive:
        alive.remove(202)
    observer = ExecutionCoordinator(database, lease_environment=_Environment(303, alive, births))
    before, revision = _custody(database), database.durable_revision()
    _recovery(database, observer).recover(fleet_run_id="already-cancelled")
    assert _custody(database) == before
    assert database.durable_revision() == revision
    assert (
        Journal(database).read_view().required_run_detail("another-worker").run.status
        is FleetRunStatus.QUEUED
    )


def test_scoped_worker_recovery_does_not_reconcile_an_unrelated_unknown_direct_effect(
    tmp_path: Path,
) -> None:
    database = LocalDatabase(tmp_path / "runtime.db")
    alive, births = {101, 202, 303}, {101: "birth-101", 202: "birth-202", 303: "birth-303"}
    parent = ExecutionCoordinator(database, lease_environment=_Environment(101, alive, births))
    journal = Journal(database)
    attempts = RepositoryEffectAttemptStore(database)
    unrelated = tmp_path / "unrelated-subject"
    unrelated.mkdir()
    baseline = GitService().fingerprint(unrelated)
    # A valid earlier Direct owner settles interruption as unknown and releases
    # its lease. The unrelated unresolved effect is not a worker-exit target.
    direct, attempt = parent.reserve_direct_with(
        expected_durable_revision=database.durable_revision(),
        mutate=lambda connection: attempts.begin_direct_in(
            connection,
            unrelated,
            capability_snapshot(OperationAction.UPDATE),
            target=None,
            baseline=baseline,
            effect_boundary=EffectBoundaryKind.EXTERNAL,
        ),
    )
    direct_session = parent.activate(direct)
    direct_session.mutate(
        lambda connection: attempts.start_effect_in(connection, attempt.operation_id)
    )
    direct_session.finish(
        lambda connection: attempts.mark_unknown_in(
            connection, attempt.operation_id, note="original Direct effect ended without a report"
        )
    )
    original = attempts.get(attempt.operation_id)
    assert original.status is EffectAttemptStatus.OUTCOME_UNKNOWN

    ticket = _admit(database, parent, "cancelled-worker")
    worker_database = LocalDatabase.open_worker(database.capture_worker_context())
    worker = ExecutionCoordinator(
        worker_database, lease_environment=_Environment(202, alive, births)
    )
    worker.claim_fleet_worker(ticket)
    FleetRunLifecycle(database, journal, parent, FleetExecutorPool(database, None)).cancel(
        "cancelled-worker"
    )
    alive.remove(202)
    observer = ExecutionCoordinator(database, lease_environment=_Environment(303, alive, births))
    _recovery(database, observer).recover(fleet_run_id="cancelled-worker")
    assert (
        journal.read_view().required_run_detail("cancelled-worker").run.status
        is FleetRunStatus.CANCELLED
    )
    assert attempts.get(attempt.operation_id) == original
    assert journal.read_view().latest_effect_reconciliation(attempt.operation_id) is None


def test_missing_native_process_start_identity_cannot_be_promoted_to_containment(
    tmp_path: Path,
) -> None:
    database = LocalDatabase(tmp_path / "runtime.db")
    alive, births = {101, 202, 303}, {101: "birth-101", 202: "birth-202", 303: "birth-303"}
    parent = ExecutionCoordinator(database, lease_environment=_Environment(101, alive, births))
    repository = tmp_path / "subject"
    repository.mkdir()
    identity = RepositoryIdentity.model_validate({"path": repository})
    ticket = _admit(database, parent, "starting-worker", targets=[identity])
    worker_database = LocalDatabase.open_worker(database.capture_worker_context())
    worker = ExecutionCoordinator(
        worker_database, lease_environment=_Environment(202, alive, births)
    )
    claimed = worker.claim_fleet_worker(ticket)
    session = worker.activate(claimed)
    journal = Journal(worker_database)
    session.mutate(lambda connection: journal.start_run_in(connection, "starting-worker"))
    operation_id = repository_attempt_operation_id("starting-worker", identity.repository_id)
    session.mutate(
        lambda connection: journal.record_execution_lifecycle_in(
            connection,
            fleet_run_id="starting-worker",
            repository_id=identity.repository_id,
            operation_id=operation_id,
            event_type=FleetRunEventType.REPOSITORY_STARTED,
        )
    )
    session.mutate(
        lambda connection: journal.record_execution_lifecycle_in(
            connection,
            fleet_run_id="starting-worker",
            repository_id=identity.repository_id,
            operation_id=operation_id,
            event_type=FleetRunEventType.PROCESS_STARTING,
            physical_execution_id="unbound-start",
        )
    )
    assert (
        journal.read_view().required_run_detail("starting-worker").run.status
        is FleetRunStatus.RUNNING
    )
    alive.remove(202)
    observer = ExecutionCoordinator(database, lease_environment=_Environment(303, alive, births))
    before, revision = _custody(database), database.durable_revision()
    with pytest.raises(StateConflict):
        _recovery(database, observer).recover(fleet_run_id="starting-worker")
    assert _custody(database) == before
    assert database.durable_revision() == revision
    assert (
        journal.read_view().required_run_detail("starting-worker").run.status
        is FleetRunStatus.RUNNING
    )


def test_queued_empty_capture_is_interrupted_not_completed_after_worker_death(
    tmp_path: Path,
) -> None:
    database, owner, environment = _worker(tmp_path)
    environment.alive.remove(202)
    _recovery(database, owner).recover(fleet_run_id="worker-run")
    detail = Journal(database).read_view().required_run_detail("worker-run")
    assert detail.run.status is FleetRunStatus.FAILED
    assert detail.run.failure.code == "RUN_INTERRUPTED_BY_RESTART"
    events = Journal(database).read_view().run_events("worker-run").events
    assert all(event.event_type is not FleetRunEventType.RUN_STARTED for event in events)


@pytest.mark.parametrize("marked_unknown", (False, True), ids=("started", "outcome-unknown"))
def test_unobserved_external_interruption_retains_custody_until_original_process_evidence(
    tmp_path: Path, marked_unknown: bool
) -> None:
    database = LocalDatabase(tmp_path / "runtime.db")
    alive, births = {101, 202, 303}, {101: "birth-101", 202: "birth-202", 303: "birth-303"}
    parent = ExecutionCoordinator(database, lease_environment=_Environment(101, alive, births))
    repository = tmp_path / "subject"
    repository.mkdir()
    identity = RepositoryIdentity.model_validate({"path": repository})
    ticket = _admit(database, parent, "legacy-unobserved", targets=[identity])
    worker_database = LocalDatabase.open_worker(database.capture_worker_context())
    worker = ExecutionCoordinator(
        worker_database, lease_environment=_Environment(202, alive, births)
    )
    session = worker.activate(worker.claim_fleet_worker(ticket))
    journal = Journal(worker_database)
    session.mutate(lambda connection: journal.start_run_in(connection, "legacy-unobserved"))
    attempts = RepositoryEffectAttemptStore(worker_database)
    operation_id = repository_attempt_operation_id("legacy-unobserved", identity.repository_id)
    baseline = GitService().fingerprint(repository)
    attempt = session.mutate(
        lambda connection: attempts.begin_fleet_in(
            connection,
            repository,
            capability_snapshot(OperationAction.INIT),
            fleet_run_id="legacy-unobserved",
            operation_id=operation_id,
            target=None,
            baseline=baseline,
            effect_boundary=EffectBoundaryKind.EXTERNAL,
        )
    )
    session.mutate(lambda connection: attempts.start_effect_in(connection, attempt.operation_id))
    if marked_unknown:
        session.mutate(
            lambda connection: attempts.mark_unknown_in(
                connection,
                attempt.operation_id,
                note="legacy interruption without native process facts",
            )
        )
    # This is an explicit legacy evidence gap, not evidence of no child process.
    # Fake process identities qualify SQLite refusal only, not physical custody.
    alive.remove(202)
    observer = ExecutionCoordinator(database, lease_environment=_Environment(303, alive, births))
    before, revision = _custody(database), database.durable_revision()
    original = attempts.get(attempt.operation_id)
    with pytest.raises(StateConflict):
        _recovery(database, observer).recover(fleet_run_id="legacy-unobserved")
    assert _custody(database) == before
    assert database.durable_revision() == revision
    assert attempts.get(attempt.operation_id) == original
    assert (
        journal.read_view().required_run_detail("legacy-unobserved").run.status
        is FleetRunStatus.RUNNING
    )


@pytest.mark.parametrize(
    "session_observation", (True, None), ids=("guardian-session-live", "session-unknown")
)
def test_operator_inspection_does_not_replace_missing_original_resource_proof(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, session_observation: bool | None
) -> None:
    database = LocalDatabase(tmp_path / "runtime.db")
    alive, births = {101, 202, 303}, {101: "birth-101", 202: "birth-202", 303: "birth-303"}
    parent = ExecutionCoordinator(database, lease_environment=_Environment(101, alive, births))
    repository = tmp_path / "subject"
    repository.mkdir()
    identity = RepositoryIdentity.model_validate({"path": repository})
    ticket = _admit(
        database, parent, "legacy-uncertain", targets=[identity], action=OperationAction.VERIFY
    )
    worker_database = LocalDatabase.open_worker(database.capture_worker_context())
    worker = ExecutionCoordinator(
        worker_database, lease_environment=_Environment(202, alive, births)
    )
    session = worker.activate(worker.claim_fleet_worker(ticket))
    journal = Journal(worker_database)
    session.mutate(lambda connection: journal.start_run_in(connection, "legacy-uncertain"))
    attempts = RepositoryEffectAttemptStore(worker_database)
    operation_id = repository_attempt_operation_id("legacy-uncertain", identity.repository_id)
    baseline = GitService().fingerprint(repository)
    attempt = session.mutate(
        lambda connection: attempts.begin_fleet_in(
            connection,
            repository,
            capability_snapshot(OperationAction.VERIFY),
            fleet_run_id="legacy-uncertain",
            operation_id=operation_id,
            target=None,
            baseline=baseline,
            effect_boundary=EffectBoundaryKind.EXTERNAL,
        )
    )
    session.mutate(lambda connection: attempts.start_effect_in(connection, attempt.operation_id))
    for event_type in (
        FleetRunEventType.REPOSITORY_STARTED,
        FleetRunEventType.PROCESS_STARTING,
        FleetRunEventType.PROCESS_STARTED,
        FleetRunEventType.PROCESS_STATE_UNKNOWN,
        FleetRunEventType.OUTPUT_QUARANTINED,
    ):
        session.mutate(
            partial(
                journal.record_execution_lifecycle_in,
                fleet_run_id="legacy-uncertain",
                repository_id=identity.repository_id,
                operation_id=operation_id,
                event_type=event_type,
                physical_execution_id="original-legacy-process"
                if event_type is not FleetRunEventType.REPOSITORY_STARTED
                else None,
                pid=909090
                if event_type
                in {FleetRunEventType.PROCESS_STARTED, FleetRunEventType.PROCESS_STATE_UNKNOWN}
                else None,
                process_identity="legacy-child-birth"
                if event_type
                in {FleetRunEventType.PROCESS_STARTED, FleetRunEventType.PROCESS_STATE_UNKNOWN}
                else None,
            )
        )
    session.mutate(
        lambda connection: attempts.mark_unknown_in(
            connection, attempt.operation_id, note="legacy worker lost process result"
        )
    )
    FleetRunLifecycle(
        worker_database, journal, worker, FleetExecutorPool(worker_database, None)
    ).finish(
        session, FleetRunTerminalIntent.FAIL, FleetRunFailure.recovery("original worker stopped")
    )
    owner = ExecutionCoordinator(database, lease_environment=_Environment(303, alive, births))
    recovery = _recovery(database, owner)
    # A prior filesystem classification is not an immutable native cleanup
    # receipt. The original result remains unknown without an exit event.
    with monkeypatch.context() as prior_observation:
        prior_observation.setattr(recovery_owner, "process_session_exists", lambda _pid: False)
        recovery.reconcile(operation_id)
    original = attempts.get(operation_id)
    assert original.status is EffectAttemptStatus.INDETERMINATE
    before = database.durable_revision()
    with monkeypatch.context() as unavailable:
        # The workload group may be empty while its guardian remains in a
        # different group of the same owned session. Neither that state nor an
        # unavailable session observation can establish resource retirement.
        unavailable.setattr(
            recovery_owner, "process_group_exists", lambda _pid: False, raising=False
        )
        unavailable.setattr(
            recovery_owner, "process_session_exists", lambda _pid: session_observation
        )
        with pytest.raises(ExecutionBusy):
            recovery.acknowledge(
                operation_id,
                note="Operator note cannot prove containment",
                expected_record_version=original.record_version,
            )
    assert database.durable_revision() == before
    assert attempts.get(operation_id) == original
    assert journal.read_view().effect_acknowledgement(operation_id) is None


@pytest.mark.parametrize(
    ("output_disposition", "native_wait"),
    (
        (None, "immediate"),
        (FleetRunEventType.OUTPUT_FINALIZED, "immediate"),
        (FleetRunEventType.OUTPUT_ABANDONED, "immediate"),
        (None, "retired"),
        (None, "timeout"),
        (None, "unproven-at-writer"),
    ),
    ids=(
        "exit-before-descendants",
        "finalized-output",
        "abandoned-output",
        "retirement-wait",
        "retirement-timeout",
        "wait-is-not-authority",
    ),
)
def test_worker_recovery_preserves_original_exit_without_fabricating_effect_or_output_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    output_disposition: FleetRunEventType | None,
    native_wait: str,
) -> None:
    database = LocalDatabase(tmp_path / "runtime.db")
    alive, births = {101, 202, 303}, {101: "birth-101", 202: "birth-202", 303: "birth-303"}
    parent = ExecutionCoordinator(database, lease_environment=_Environment(101, alive, births))
    repository = tmp_path / "subject"
    repository.mkdir()
    identity = RepositoryIdentity.model_validate({"path": repository})
    run_id = "worker-exited-before-resource-settlement"
    ticket = _admit(database, parent, run_id, targets=[identity], action=OperationAction.VERIFY)
    worker_database = LocalDatabase.open_worker(database.capture_worker_context())
    worker = ExecutionCoordinator(
        worker_database, lease_environment=_Environment(202, alive, births)
    )
    session = worker.activate(worker.claim_fleet_worker(ticket))
    journal = Journal(worker_database)
    session.mutate(lambda connection: journal.start_run_in(connection, run_id))
    attempts = RepositoryEffectAttemptStore(worker_database)
    operation_id = repository_attempt_operation_id(run_id, identity.repository_id)
    attempt = session.mutate(
        lambda connection: attempts.begin_fleet_in(
            connection,
            repository,
            capability_snapshot(OperationAction.VERIFY),
            fleet_run_id=run_id,
            operation_id=operation_id,
            target=None,
            baseline=GitService().fingerprint(repository),
            effect_boundary=EffectBoundaryKind.EXTERNAL,
        )
    )
    session.mutate(lambda connection: attempts.start_effect_in(connection, attempt.operation_id))
    original_events = [
        FleetRunEventType.REPOSITORY_STARTED,
        FleetRunEventType.PROCESS_STARTING,
        FleetRunEventType.PROCESS_STARTED,
        FleetRunEventType.PROCESS_EXITED,
    ]
    if output_disposition is not None:
        original_events.extend((FleetRunEventType.DESCENDANTS_QUIESCENT, output_disposition))
    physical_id = "declared-original-process"
    for event_type in original_events:
        # These are explicit SQLite lifecycle-policy fixtures. Real worker and
        # command death are qualified by test_run_worker_recovery.py.
        physical = event_type in {
            FleetRunEventType.PROCESS_STARTED,
            FleetRunEventType.PROCESS_EXITED,
            FleetRunEventType.DESCENDANTS_QUIESCENT,
        }
        session.mutate(
            partial(
                journal.record_execution_lifecycle_in,
                fleet_run_id=run_id,
                repository_id=identity.repository_id,
                operation_id=operation_id,
                event_type=event_type,
                physical_execution_id=physical_id
                if event_type is not FleetRunEventType.REPOSITORY_STARTED
                else None,
                pid=909090 if physical else None,
                process_identity="declared-original-birth" if physical else None,
                returncode=0 if event_type is FleetRunEventType.PROCESS_EXITED else None,
                output_count=1 if event_type is FleetRunEventType.OUTPUT_FINALIZED else None,
            )
        )
    original_detail = journal.read_view().required_run_detail(run_id)
    original_events_recorded = journal.read_view().run_events(run_id).events
    original_exit = next(
        event for event in original_detail.lifecycle_events if isinstance(event, ProcessExitedEvent)
    )
    assert original_exit.returncode == 0
    alive.remove(202)
    owner = ExecutionCoordinator(database, lease_environment=_Environment(303, alive, births))
    monkeypatch.setattr(
        recovery_owner,
        "process_session_exists",
        (lambda _pid: native_wait in {"timeout", "unproven-at-writer"})
        if output_disposition is None
        else lambda _pid: pytest.fail("Original immutable resource receipt must be reused"),
    )
    waited: list[int] = []

    def wait_for_session(pid: int, *, timeout: float) -> bool:
        # The native integration test covers real retirement. This fixture
        # qualifies timeout and the separate writer fence without sleeping.
        assert 0 <= timeout <= 1
        waited.append(pid)
        return native_wait != "timeout"

    monkeypatch.setattr(recovery_owner, "wait_for_process_session_quiescence", wait_for_session)
    recovery = _recovery(database, owner)
    budget = 0.0 if native_wait == "immediate" else 1.0
    if native_wait in {"timeout", "unproven-at-writer"}:
        revision, custody = database.durable_revision(), _custody(database)
        with pytest.raises(ExecutionBusy):
            recovery.recover(fleet_run_id=run_id, quiescence_timeout_seconds=budget)
        assert waited == [909090]
        assert database.durable_revision() == revision
        assert _custody(database) == custody
        assert journal.read_view().required_run_detail(run_id) == original_detail
        return
    recovery.recover(fleet_run_id=run_id, quiescence_timeout_seconds=budget)
    assert waited == ([909090] if native_wait == "retired" else [])

    detail = journal.read_view().required_run_detail(run_id)
    assert detail.run.status is FleetRunStatus.FAILED
    assert detail.run.failure.code == "REPOSITORY_EFFECT_OUTCOME_UNKNOWN"
    assert detail.operations == ()
    assert attempts.get(operation_id).status is EffectAttemptStatus.INDETERMINATE
    assert attempts.get(operation_id).quarantines_repository
    assert tuple(
        event for event in detail.lifecycle_events if isinstance(event, ProcessExitedEvent)
    ) == (original_exit,)
    recovered_events = journal.read_view().run_events(run_id).events
    types = [event.event_type for event in recovered_events]
    assert types.count(FleetRunEventType.DESCENDANTS_QUIESCENT) == 1
    assert FleetRunEventType.PROCESS_STATE_UNKNOWN not in types
    assert FleetRunEventType.OUTPUT_QUARANTINED not in types
    assert FleetRunEventType.STEP_SUCCEEDED not in types
    assert tuple(
        event
        for event in recovered_events
        if event.event_type
        in {FleetRunEventType.OUTPUT_FINALIZED, FleetRunEventType.OUTPUT_ABANDONED}
    ) == tuple(
        event
        for event in original_events_recorded
        if event.event_type
        in {FleetRunEventType.OUTPUT_FINALIZED, FleetRunEventType.OUTPUT_ABANDONED}
    )
    revision = database.durable_revision()
    recovery.recover(fleet_run_id=run_id)
    assert database.durable_revision() == revision
