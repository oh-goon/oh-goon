from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from oh_goon.core.errors import RuntimeSafetyStale
from oh_goon.core.models.repository import RepositoryIdentity
from oh_goon.core.models.run_admission import (
    DirectFleetExecutionAdmission,
    FleetRunAdmissionIntent,
    fleet_repository_scope,
)
from oh_goon.core.product_identity import repository_attempt_operation_id
from oh_goon.core.schedule_transitions import begin_schedule_run
from oh_goon.core.time import utc_now
from oh_goon.core.vocabulary import (
    EffectBoundaryKind,
    FleetRunEventType,
    OperationAction,
    RuntimeSafetyStatus,
)
from oh_goon.runtime.database import LocalDatabase
from oh_goon.runtime.effect_attempts import RepositoryEffectAttemptStore
from oh_goon.runtime.execution import ExecutionCoordinator
from oh_goon.runtime.journal import Journal
from oh_goon.runtime.safety import RuntimeSafetyGate
from oh_goon.runtime.schedule_definitions import ScheduleDefinitionStore
from oh_goon.runtime.schedule_executions import ScheduleExecutionStore
from oh_goon.services.invariant_auditor import SystemInvariantAuditor
from oh_goon.services.runtime_safety import RuntimeSafetyService
from tests.factories.capabilities import resolved_intent
from tests.factories.local_state import local_state
from tests.factories.schedules import schedule_create, scheduled_fleet_run_intent


def _safety_stack(database: LocalDatabase) -> tuple[RuntimeSafetyService, SystemInvariantAuditor]:
    journal = Journal(database)
    execution = ExecutionCoordinator(database)
    attempts = RepositoryEffectAttemptStore(database)
    schedules = ScheduleExecutionStore(database)
    auditor = SystemInvariantAuditor(database, journal, execution, attempts, schedules)
    service = RuntimeSafetyService(auditor, RuntimeSafetyGate())
    service.refresh()
    return service, auditor


def test_empty_write_transaction_does_not_advance_durable_revision(tmp_path: Path) -> None:
    database = LocalDatabase(tmp_path / "state.db")
    before = database.durable_revision()

    with database.transaction(write=True):
        pass

    assert database.durable_revision() == before


def test_real_write_transaction_advances_durable_revision_once(tmp_path: Path) -> None:
    database = LocalDatabase(tmp_path / "state.db")
    before = database.durable_revision()

    with database.transaction(write=True) as connection:
        connection.execute(
            "INSERT INTO locations(path, position, added_at) VALUES (?, ?, ?)",
            (str(tmp_path / "repo"), 0, "2026-08-12T00:00:00+00:00"),
        )
        connection.execute(
            "INSERT INTO exclusions(path, reason, excluded_at) VALUES (?, ?, ?)",
            (str(tmp_path / "repo"), "test", "2026-08-12T00:00:00+00:00"),
        )

    assert database.durable_revision() == before + 1


def test_runtime_safety_proof_is_bound_to_durable_and_journal_revisions(tmp_path: Path) -> None:
    database = LocalDatabase(tmp_path / "state.db")
    state = local_state(database)
    service, _ = _safety_stack(database)

    initial = service.proof()
    assert initial.status is RuntimeSafetyStatus.PROVEN_SAFE
    assert initial.durable_revision == database.durable_revision() == 0
    assert initial.journal_revision == 0

    root = tmp_path / "repos"
    root.mkdir()
    state.add_location(root)

    refreshed = service.admit_effects()
    assert service.effects_allowed()
    assert refreshed.status is RuntimeSafetyStatus.PROVEN_SAFE
    assert refreshed.durable_revision == database.durable_revision() == 1
    assert refreshed.journal_revision == 0
    assert (
        refreshed.checked_at != initial.checked_at
        or refreshed.durable_revision != initial.durable_revision
    )


def test_invariant_auditor_reads_one_transactional_safety_cut(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = LocalDatabase(tmp_path / "state.db")
    journal = Journal(database)
    execution = ExecutionCoordinator(database)
    attempts = RepositoryEffectAttemptStore(database)
    schedules = ScheduleExecutionStore(database)
    auditor = SystemInvariantAuditor(database, journal, execution, attempts, schedules)

    def fail_standalone(*_args, **_kwargs):
        raise AssertionError("auditor must use connection-bound reads from one transaction")

    monkeypatch.setattr(execution, "active_lease", fail_standalone)
    monkeypatch.setattr(attempts, "all", fail_standalone)
    monkeypatch.setattr(schedules, "active", fail_standalone)

    audit = auditor.audit()

    assert audit.healthy
    assert audit.durable_revision == database.durable_revision()
    assert audit.journal_revision == journal.read_view().revision


def test_direct_lease_admission_rejects_a_stale_proof_without_creating_a_lease(
    tmp_path: Path,
) -> None:
    path = tmp_path / "state.db"
    first = LocalDatabase(path)
    second = LocalDatabase(path)
    execution = ExecutionCoordinator(first)
    proved_revision = first.durable_revision()

    root = tmp_path / "repos"
    root.mkdir()
    local_state(second).add_location(root)

    with pytest.raises(RuntimeSafetyStale, match="expected revision"):
        execution.reserve_direct_with(
            expected_durable_revision=proved_revision, mutate=lambda _connection: None
        )

    assert execution.active_lease() is None


def test_fleet_lease_and_run_admission_commit_or_rollback_together(tmp_path: Path) -> None:
    database = LocalDatabase(tmp_path / "state.db")
    journal = Journal(database)
    execution = ExecutionCoordinator(database)
    before = database.durable_revision()

    def fail(_connection):
        raise RuntimeError("admission failed")

    with pytest.raises(RuntimeError, match="admission failed"):
        execution.reserve_fleet_with(
            "fleet-atomic-admission",
            expected_durable_revision=before,
            mutate=fail,
        )

    assert database.durable_revision() == before
    assert execution.active_lease() is None
    assert journal.read_view().run_detail("fleet-atomic-admission") is None

    ticket, state = execution.reserve_fleet_with(
        "fleet-atomic-admission",
        expected_durable_revision=before,
        mutate=lambda connection: journal.admit_run_in(
            connection,
            "fleet-atomic-admission",
            FleetRunAdmissionIntent(
                capability=resolved_intent(OperationAction.INIT).definition.durable_snapshot(),
                execution=DirectFleetExecutionAdmission(),
                repository_admissions=(),
            ),
            fleet_repository_scope(None),
            [],
        ),
    )
    assert state.fleet_run_id == "fleet-atomic-admission"
    assert database.durable_revision() == before + 1
    assert execution.active_lease() is not None
    events = journal.run_events("fleet-atomic-admission").events
    assert [event.event_type for event in events] == [FleetRunEventType.RUN_ADMITTED]
    execution.activate(ticket).finish(lambda _connection: None)


def test_schedule_admission_cas_rejects_definition_cut_that_became_stale(tmp_path: Path) -> None:
    database = LocalDatabase(tmp_path / "state.db")
    definitions = ScheduleDefinitionStore(database)
    executions = ScheduleExecutionStore(database)
    definition = definitions.create(
        "schedule-atomic",
        schedule_create(name="Atomic"),
    )
    proved_revision = database.durable_revision()

    definitions.create(
        "schedule-concurrent-change",
        schedule_create(name="Concurrent change"),
    )

    stale_execution = begin_schedule_run(
        schedule_run_id="schedule-run-stale-admission",
        schedule_id=definition.schedule_id,
        intent=scheduled_fleet_run_intent(definition),
        started_at=utc_now(),
    )
    with pytest.raises(RuntimeSafetyStale):
        executions.insert(stale_execution, expected_durable_revision=proved_revision)

    assert executions.active() == []


def test_invariant_auditor_treats_live_owned_direct_attempt_as_normal_work(tmp_path: Path) -> None:
    from tests.factories.repository_state import baseline_for

    database = LocalDatabase(tmp_path / "owned-attempt.db")
    journal = Journal(database)
    execution = ExecutionCoordinator(database)
    attempts = RepositoryEffectAttemptStore(database)
    schedules = ScheduleExecutionStore(database)
    auditor = SystemInvariantAuditor(database, journal, execution, attempts, schedules)
    repository = tmp_path / "repo"
    repository.mkdir()
    (repository / "pyproject.toml").write_text("[project]\nname='owned'\n", encoding="utf-8")
    intent = resolved_intent(OperationAction.UPDATE)

    ticket, attempt = execution.reserve_direct_with(
        expected_durable_revision=database.durable_revision(),
        mutate=lambda connection: attempts.begin_direct_in(
            connection,
            repository,
            intent.definition.durable_snapshot(),
            target=intent.target,
            baseline=baseline_for(repository),
            effect_boundary=EffectBoundaryKind.EXTERNAL,
            operation_id="owned-direct-attempt",
        ),
    )

    audit = auditor.audit()
    assert audit.healthy
    assert not any(item.code == "UNOWNED_EFFECT_ATTEMPT" for item in audit.findings)

    session = execution.activate(ticket)
    session.finish(
        lambda connection: attempts.retire_unstarted_in(connection, attempt.operation_id)
    )


def test_invariant_auditor_flags_unowned_attempt_and_direct_lease_without_attempt(
    tmp_path: Path,
) -> None:
    from tests.factories.repository_state import baseline_for

    database = LocalDatabase(tmp_path / "invalid-ownership.db")
    journal = Journal(database)
    execution = ExecutionCoordinator(database)
    attempts = RepositoryEffectAttemptStore(database)
    schedules = ScheduleExecutionStore(database)
    auditor = SystemInvariantAuditor(database, journal, execution, attempts, schedules)
    repository = tmp_path / "repo"
    repository.mkdir()
    (repository / "pyproject.toml").write_text("[project]\nname='orphan'\n", encoding="utf-8")
    intent = resolved_intent(OperationAction.UPDATE)

    with database.transaction(write=True) as connection:
        orphan = attempts.begin_direct_in(
            connection,
            repository,
            intent.definition.durable_snapshot(),
            target=intent.target,
            baseline=baseline_for(repository),
            effect_boundary=EffectBoundaryKind.EXTERNAL,
            operation_id="orphan-direct-attempt",
        )

    orphan_audit = auditor.audit()
    assert any(item.code == "UNOWNED_EFFECT_ATTEMPT" for item in orphan_audit.findings)

    with database.transaction(write=True) as connection:
        attempts.retire_unstarted_in(connection, orphan.operation_id)

    ticket, _ = execution.reserve_direct_with(
        expected_durable_revision=database.durable_revision(),
        mutate=lambda _connection: None,
    )
    lease_audit = auditor.audit()
    assert any(
        item.code == "DIRECT_LEASE_WITHOUT_EXACT_EFFECT_ATTEMPT" for item in lease_audit.findings
    )
    execution.activate(ticket).finish(lambda _connection: None)


def test_invariant_auditor_accepts_parallel_owned_fleet_attempts(tmp_path: Path) -> None:
    from tests.factories.repository_state import baseline_for

    database = LocalDatabase(tmp_path / "parallel-fleet-ownership.db")
    journal = Journal(database)
    execution = ExecutionCoordinator(database)
    attempts = RepositoryEffectAttemptStore(database)
    schedules = ScheduleExecutionStore(database)
    auditor = SystemInvariantAuditor(database, journal, execution, attempts, schedules)
    intent = resolved_intent(OperationAction.UPDATE)
    repositories = [tmp_path / "repo-a", tmp_path / "repo-b"]
    for repository in repositories:
        repository.mkdir()
        (repository / "pyproject.toml").write_text(
            f"[project]\nname='{repository.name}'\n", encoding="utf-8"
        )
    targets = [RepositoryIdentity(name=path.name, path=path) for path in repositories]
    fleet_run_id = "fleet-parallel-owned"
    admission = FleetRunAdmissionIntent(
        capability=intent.definition.durable_snapshot(),
        execution=DirectFleetExecutionAdmission(),
        repository_admissions=(),
    )

    ticket, _ = execution.reserve_fleet_with(
        fleet_run_id,
        expected_durable_revision=database.durable_revision(),
        mutate=lambda connection: journal.admit_run_in(
            connection, fleet_run_id, admission, fleet_repository_scope(None), targets
        ),
    )
    session = execution.activate(ticket)
    session.mutate(lambda connection: journal.start_run_in(connection, fleet_run_id))
    created = []
    for repository, target in zip(repositories, targets, strict=True):
        created.append(
            session.mutate(
                lambda connection, repository=repository, target=target: attempts.begin_fleet_in(
                    connection,
                    repository,
                    intent.definition.durable_snapshot(),
                    fleet_run_id=fleet_run_id,
                    operation_id=repository_attempt_operation_id(
                        fleet_run_id, target.repository_id
                    ),
                    target=intent.target,
                    baseline=baseline_for(repository),
                    effect_boundary=EffectBoundaryKind.EXTERNAL,
                )
            )
        )

    audit = auditor.audit()
    assert audit.healthy
    assert not any(item.code == "UNOWNED_EFFECT_ATTEMPT" for item in audit.findings)
    assert not any(item.code.startswith("FLEET_EFFECT_ATTEMPT_") for item in audit.findings)
    assert not any(
        item.code == "MULTIPLE_LIVE_FLEET_REPOSITORY_ATTEMPTS" for item in audit.findings
    )

    def retire(connection):
        for attempt in created:
            attempts.retire_unstarted_in(connection, attempt.operation_id)

    session.finish(retire)


def test_invariant_auditor_rejects_corrupt_fleet_attempt_structure(tmp_path: Path) -> None:
    from tests.factories.repository_state import baseline_for

    database = LocalDatabase(tmp_path / "corrupt-fleet-attempt.db")
    journal = Journal(database)
    execution = ExecutionCoordinator(database)
    attempts = RepositoryEffectAttemptStore(database)
    schedules = ScheduleExecutionStore(database)
    auditor = SystemInvariantAuditor(database, journal, execution, attempts, schedules)
    intent = resolved_intent(OperationAction.UPDATE)
    repository = tmp_path / "repo"
    repository.mkdir()
    (repository / "pyproject.toml").write_text("[project]\nname='repo'\n", encoding="utf-8")
    target = RepositoryIdentity(name="repo", path=repository)
    fleet_run_id = "fleet-corrupt-attempt"
    admission = FleetRunAdmissionIntent(
        capability=intent.definition.durable_snapshot(),
        execution=DirectFleetExecutionAdmission(),
        repository_admissions=(),
    )
    ticket, _ = execution.reserve_fleet_with(
        fleet_run_id,
        expected_durable_revision=database.durable_revision(),
        mutate=lambda connection: journal.admit_run_in(
            connection, fleet_run_id, admission, fleet_repository_scope(None), [target]
        ),
    )
    session = execution.activate(ticket)
    session.mutate(lambda connection: journal.start_run_in(connection, fleet_run_id))
    attempt = session.mutate(
        lambda connection: attempts.begin_fleet_in(
            connection,
            repository,
            intent.definition.durable_snapshot(),
            fleet_run_id=fleet_run_id,
            operation_id=repository_attempt_operation_id(fleet_run_id, target.repository_id),
            target=intent.target,
            baseline=baseline_for(repository),
            effect_boundary=EffectBoundaryKind.EXTERNAL,
        )
    )
    cut = auditor.read_cut()

    noncanonical = attempt.model_copy(update={"operation_id": "op-corrupt"})
    noncanonical_audit = auditor.audit(replace(cut, effect_attempts=(noncanonical,)))
    assert any(
        item.code == "FLEET_EFFECT_ATTEMPT_NONCANONICAL_ID" for item in noncanonical_audit.findings
    )

    outside_repository = RepositoryIdentity(name="outside", path=tmp_path / "outside")
    outside = attempt.model_copy(update={"repository": outside_repository})
    outside_audit = auditor.audit(replace(cut, effect_attempts=(outside,)))
    assert any(
        item.code == "FLEET_EFFECT_ATTEMPT_OUTSIDE_CAPTURE" for item in outside_audit.findings
    )

    duplicate = attempt.model_copy(update={"operation_id": "op-duplicate-corrupt"})
    duplicate_audit = auditor.audit(replace(cut, effect_attempts=(attempt, duplicate)))
    assert any(
        item.code == "MULTIPLE_LIVE_FLEET_REPOSITORY_ATTEMPTS" for item in duplicate_audit.findings
    )

    session.finish(
        lambda connection: attempts.retire_unstarted_in(connection, attempt.operation_id)
    )
