from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from oh_goon.core.errors import OperationNotFound, RuntimeSafetyStale, StateConflict
from oh_goon.core.models.runtime_safety import ProvenRuntimeSafetyProof
from oh_goon.core.vocabulary import EffectBoundaryKind, OperationAction, RuntimeSafetyStatus
from oh_goon.engine.git import GitService
from oh_goon.runtime.database import LocalDatabase
from oh_goon.runtime.effect_attempts import RepositoryEffectAttemptStore
from oh_goon.runtime.journal import Journal
from oh_goon.runtime.journal_view import JournalReadView
from oh_goon.runtime.safety_cut import ConsistentRuntimeSafetyCut
from oh_goon.runtime.schedule_definitions import ScheduleDefinitionStore
from oh_goon.runtime.schedule_executions import ScheduleExecutionStore
from oh_goon.services.direct_effect_admission import DirectEffectAdmission
from oh_goon.services.schedule_admission import ScheduleAdmissionPlan, ScheduleAdmissionPlanner
from oh_goon.services.schedules import ScheduleService
from tests.factories.capabilities import capability_snapshot
from tests.factories.journal import commit_operation
from tests.factories.operation_reports import make_operation_report
from tests.factories.repository_state import baseline_for
from tests.factories.runtime_stack import direct_operation_stack
from tests.factories.schedules import schedule_create, scheduled_fleet_run_intent


class _SafetyProof:
    def __init__(self, revision: int) -> None:
        self.calls = 0
        self._proof = ProvenRuntimeSafetyProof(
            status=RuntimeSafetyStatus.PROVEN_SAFE,
            durable_revision=revision,
            journal_revision=0,
        )

    def admit_effect_cut(self) -> ConsistentRuntimeSafetyCut:
        self.calls += 1
        return ConsistentRuntimeSafetyCut(
            durable_revision=self._proof.durable_revision or 0,
            journal=JournalReadView.from_entries((), "test"),
            active_lease=None,
            effect_attempts=(),
            active_schedules=(),
        )

    def require_repository_effects_allowed(self, cut, *, safety, repository_ids) -> None:
        del cut, safety, repository_ids


class _StaleDirectExecution:
    def __init__(self) -> None:
        self.reserve_calls = 0

    def reserve_direct_with(self, *, expected_durable_revision: int, mutate):
        self.reserve_calls += 1
        raise RuntimeSafetyStale(f"stale revision {expected_durable_revision}")


class _NoCommittedOperation:
    def operation(self, operation_id: str):
        del operation_id


class _NoUnresolvedAttempt:
    def get(self, operation_id: str):
        raise OperationNotFound(operation_id)


class _NeverCalled:
    def __getattr__(self, name: str):
        raise AssertionError(f"{name} must not be reached after stale admission")


class _NeverExecuteAdapter:
    def __init__(self) -> None:
        self.calls = 0

    def execute_request(self, request):
        del request
        self.calls += 1
        raise AssertionError("capability must not execute for an existing Direct identity")


class _CommitDuringSafetyProof:
    def __init__(self, database: LocalDatabase, journal: Journal, report) -> None:
        self._database = database
        self._journal = journal
        self._report = report

    def admit_effect_cut(self) -> ConsistentRuntimeSafetyCut:
        commit_operation(self._journal, self._report)
        return ConsistentRuntimeSafetyCut(
            durable_revision=self._database.durable_revision(),
            journal=self._journal.read_view(),
            active_lease=None,
            effect_attempts=(),
            active_schedules=(),
        )

    def require_repository_effects_allowed(self, cut, *, safety, repository_ids) -> None:
        del cut, safety, repository_ids


class _ClaimDuringSafetyProof:
    def __init__(
        self, database: LocalDatabase, attempts: RepositoryEffectAttemptStore, repository: Path
    ) -> None:
        self._database = database
        self._attempts = attempts
        self._repository = repository

    def admit_effect_cut(self) -> ConsistentRuntimeSafetyCut:
        with self._database.transaction(write=True) as connection:
            self._attempts.begin_direct_in(
                connection,
                self._repository,
                capability_snapshot(OperationAction.UPDATE),
                target=None,
                baseline=baseline_for(self._repository),
                effect_boundary=EffectBoundaryKind.EXTERNAL,
                operation_id="op-raced-claim",
            )
        return ConsistentRuntimeSafetyCut(
            durable_revision=self._database.durable_revision(),
            journal=JournalReadView.from_entries((), "test"),
            active_lease=None,
            effect_attempts=(),
            active_schedules=(),
        )

    def require_repository_effects_allowed(self, cut, *, safety, repository_ids) -> None:
        del cut, safety, repository_ids


def _direct_repository(tmp_path: Path) -> Path:
    repository = tmp_path / "repo"
    repository.mkdir()
    (repository / "pyproject.toml").write_text("[project]\nname='fixture'\n", encoding="utf-8")
    return repository


def test_direct_admission_rechecks_committed_identity_inside_reservation(tmp_path: Path) -> None:
    database = LocalDatabase(tmp_path / "direct-commit-race.db")
    journal = Journal(database)
    repository = _direct_repository(tmp_path)
    operation_id = "op-raced-commit"
    report = make_operation_report(
        repository, OperationAction.UPDATE, fleet_run_id=None, operation_id=operation_id
    )
    adapter = _NeverExecuteAdapter()
    safety = _CommitDuringSafetyProof(database, journal, report)
    operations, attempts, execution, _ = direct_operation_stack(
        database, journal, adapter, runtime_safety=safety
    )

    replayed = operations.run(repository, OperationAction.UPDATE, operation_id=operation_id)

    assert replayed.report == report
    assert adapter.calls == 0
    assert attempts.all() == []
    assert execution.active_lease() is None


def test_direct_admission_rechecks_unresolved_attempt_inside_reservation(tmp_path: Path) -> None:
    database = LocalDatabase(tmp_path / "direct-claim-race.db")
    journal = Journal(database)
    repository = _direct_repository(tmp_path)
    attempts = RepositoryEffectAttemptStore(database)
    adapter = _NeverExecuteAdapter()
    safety = _ClaimDuringSafetyProof(database, attempts, repository)
    operations, _, execution, _ = direct_operation_stack(
        database, journal, adapter, runtime_safety=safety
    )

    with pytest.raises(StateConflict, match="unresolved repository effect"):
        operations.run(repository, OperationAction.UPDATE, operation_id="op-raced-claim")

    assert adapter.calls == 0
    assert execution.active_lease() is None


def test_direct_admission_uses_one_proof_and_one_cas_attempt(tmp_path: Path) -> None:
    safety = _SafetyProof(7)
    execution = _StaleDirectExecution()
    service = DirectEffectAdmission(
        _NoCommittedOperation(),  # type: ignore[arg-type]
        execution,  # type: ignore[arg-type]
        _NoUnresolvedAttempt(),  # type: ignore[arg-type]
        safety,  # type: ignore[arg-type]
        _NeverCalled(),  # type: ignore[arg-type]
        GitService(),
    )

    with pytest.raises(RuntimeSafetyStale):
        service.run(tmp_path / "repo", OperationAction.PLAN)

    assert safety.calls == 1
    assert execution.reserve_calls == 1


def test_schedule_start_does_not_hide_stale_admission_retry(tmp_path: Path) -> None:
    database = LocalDatabase(tmp_path / "schedule.db")
    definitions = ScheduleDefinitionStore(database)
    executions = ScheduleExecutionStore(database)
    definition = definitions.create(
        "schedule-one-shot",
        schedule_create(name="one-shot", repository_ids=("repo-a",)),
    )

    class Admission:
        calls = 0

        def plan(self, schedule_id: str) -> ScheduleAdmissionPlan:
            self.calls += 1
            assert schedule_id == definition.schedule_id
            return ScheduleAdmissionPlan(
                durable_revision=database.durable_revision(),
                schedule_id=definition.schedule_id,
                intent=scheduled_fleet_run_intent(definition),
            )

    class Engine:
        calls = 0

        def start(self, plan: ScheduleAdmissionPlan, *, schedule_run_id: str):
            assert schedule_run_id == "schedule-run-stale"
            assert plan.schedule_id == definition.schedule_id
            self.calls += 1
            raise RuntimeSafetyStale(f"stale revision {plan.durable_revision}")

    admission = Admission()
    engine = Engine()
    service = ScheduleService(
        database,
        definitions,
        executions,
        engine,  # type: ignore[arg-type]
        admission,  # type: ignore[arg-type]
        SimpleNamespace(effects_allowed=lambda: True),  # type: ignore[arg-type]
    )

    with pytest.raises(RuntimeSafetyStale):
        service.start(
            definition.schedule_id,
            "schedule-run-stale",
            expected_generation_id=definition.generation_id,
            expected_record_version=definition.record_version,
        )

    assert admission.calls == 1
    assert engine.calls == 1


def test_schedule_admission_captures_definition_and_safety_cut_in_same_transaction(
    tmp_path: Path,
) -> None:
    database = LocalDatabase(tmp_path / "schedule-cut.db")
    definition = ScheduleDefinitionStore(database).create(
        "schedule-same-cut",
        schedule_create(name="same-cut", repository_ids=("repo-a",)),
    )
    marker = object()

    class Auditor:
        connection = None

        def read_cut_in(self, connection):
            self.connection = connection
            return marker

    auditor = Auditor()

    class Definitions:
        def get_in(self, connection, schedule_id: str):
            assert connection is auditor.connection
            assert schedule_id == definition.schedule_id
            return definition

    class Safety:
        def admit_cut(self, cut):
            assert cut is marker
            return ProvenRuntimeSafetyProof(
                status=RuntimeSafetyStatus.PROVEN_SAFE,
                durable_revision=database.durable_revision(),
                journal_revision=0,
            )

    planner = ScheduleAdmissionPlanner(
        database,
        Definitions(),  # type: ignore[arg-type]
        ScheduleExecutionStore(database),
        auditor,  # type: ignore[arg-type]
        Safety(),  # type: ignore[arg-type]
    )

    plan = planner.plan(definition.schedule_id)

    assert plan.schedule_id == definition.schedule_id
    assert plan.intent == scheduled_fleet_run_intent(definition)
    assert plan.durable_revision == database.durable_revision()
