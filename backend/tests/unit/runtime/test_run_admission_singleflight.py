from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from threading import Lock

import pytest

from oh_goon.core.errors import ExecutionBusy, ExecutorAdmissionCancelled, StateConflict, TargetSelectionError
from oh_goon.core.models.fleet_executor import (
    ExecutorCapacityEvidenceInput,
    FleetExecutorIdentity,
    FleetExecutorPlacement,
    PlanExecutorCapacityEvidence,
)
from oh_goon.core.models.fleet_plan import ResolvedCommandStep, ResolvedFleetPlan
from oh_goon.core.models.fleet_plan_evidence import PlanIsolationBackendEvidence
from oh_goon.core.models.fleet_plan_execution_policy import (
    CicdExecutionPolicy,
)
from oh_goon.core.models.fleet_plan_isolation import (
    CicdResourcePolicy,
)
from oh_goon.core.models.fleet_plan_step_contract import PlanStepCapabilities
from oh_goon.core.models.repository import (
    IncludedRepositoryCatalogEntry,
    RepositoryCatalogEntry,
    RepositoryIdentity,
)
from oh_goon.core.models.run_admission import PlanRepositoryAdmissionEvidence, fleet_repository_scope
from oh_goon.core.models.scheduling import SchedulingPriority
from oh_goon.core.vocabulary import (
    FleetRunEventType,
    FleetRunSource,
    FleetRunStatus,
    FleetRunTerminalIntent,
    FleetScope,
    OperationAction,
)
from oh_goon.runtime.database import LocalDatabase
from oh_goon.runtime.effect_attempts import RepositoryEffectAttemptStore
from oh_goon.runtime.execution import ExecutionCoordinator
from oh_goon.runtime.game_tape_live_output import GameTapeLiveOutputStore
from oh_goon.runtime.journal import Journal
from oh_goon.services.fleet_admission import FleetAdmissionPlan
from oh_goon.services.fleet_run_lifecycle import FleetRunLifecycle
from oh_goon.services.fleet_run_control_plane import FleetRunControlPlane
from tests.factories.capabilities import resolved_intent
from tests.factories.fleet_plan_targets import structural_fleet_plan_target
from tests.factories.run_admission import repository_admission_for


class _Admission:
    def __init__(self, database: LocalDatabase, target: RepositoryCatalogEntry) -> None:
        self._database = database
        self._target = target

    def plan(self, *args: object, **kwargs: object) -> FleetAdmissionPlan:
        del kwargs
        intent = args[0]
        target = intent.target
        repository_admissions = (
            (
                repository_admission_for(
                    repository_id=self._target.repository.repository_id,
                    plan_id=target.plan.plan_id,
                    active_step_ids=tuple(step.step_id for step in target.plan.steps),
                ),
            )
            if target is not None and getattr(target, "kind", None) == "fleet_plan"
            else ()
        )
        return FleetAdmissionPlan(
            durable_revision=self._database.durable_revision(),
            targets=(self._target,),
            repository_admissions=repository_admissions,
        )


class _RejectingAdmission:
    def plan(self, *args: object, **kwargs: object) -> FleetAdmissionPlan:
        del args, kwargs
        raise TargetSelectionError("known-invalid repository/Plan authority")


class _CountingPool:
    def __init__(self, database: LocalDatabase) -> None:
        self._database = database
        self.admit_calls = 0
        self._lock = Lock()

    def admit_strict_with(
        self,
        *,
        fleet_run_id: str,
        plan: ResolvedFleetPlan,
        targets: tuple[RepositoryIdentity, ...],
        repository_admissions=None,
        requested_priority=None,
        commit=None,
    ):
        del plan, targets, repository_admissions, requested_priority
        with self._lock:
            self.admit_calls += 1
        backend = PlanIsolationBackendEvidence(
            backend_id="remote-test",
            configuration_id="singleflight-config",
            kernel_boundary="virtual_machine",
            remote_execution=True,
            experimental=False,
        )
        capacity = PlanExecutorCapacityEvidence.create(
            ExecutorCapacityEvidenceInput(
                fleet_run_id=fleet_run_id,
                executor=FleetExecutorIdentity(executor_id="executor-a", generation=1),
                source="remote_operator_attested",
                backend_configuration_id=backend.configuration_id,
                capacity_cpu_millis=2000,
                capacity_memory_bytes=2 * 1024 * 1024 * 1024,
                capacity_max_processes=None,
                requested_parallelism=1,
                admitted_parallelism=1,
                per_attempt_cpu_millis=1000,
                per_attempt_memory_bytes=1024 * 1024 * 1024,
                per_attempt_max_processes=None,
                pool_id="singleflight-pool",
                placement_policy="priority_rendezvous_fifo_v2",
                placement_rank=0,
                executor_priority=0,
            )
        )
        placement = FleetExecutorPlacement(
            isolation_backend=backend,
            executor_capacity=capacity,
            executor_trust_epoch=0,
        )
        with self._database.transaction(write=True) as connection:
            result = commit(connection, placement)
        return placement, result

    @staticmethod
    def scheduling_receipt_in(connection, fleet_run_id: str):
        del connection, fleet_run_id
        return None

    @staticmethod
    def release_run_in(connection, fleet_run_id: str) -> None:
        del connection, fleet_run_id


class _CancelledAdmissionPool(_CountingPool):
    def admit_strict_with(
        self,
        *,
        fleet_run_id: str,
        plan: ResolvedFleetPlan,
        targets: tuple[RepositoryIdentity, ...],
        repository_admissions=None,
        requested_priority=None,
        commit=None,
    ):
        del plan, targets, repository_admissions, requested_priority, commit
        with self._lock:
            self.admit_calls += 1
        raise ExecutorAdmissionCancelled(f"Queued executor admission cancelled: {fleet_run_id}")


class _RecordingSupervisor:
    def __init__(self) -> None:
        self.submit_calls = 0
        self._lock = Lock()

    def submit(self, fleet_run_id: str, work: object) -> None:
        del fleet_run_id, work
        with self._lock:
            self.submit_calls += 1

    def shutdown(self, *, wait: bool = True) -> None:
        del wait


class _UnusedRunExecution:
    def execute(self, *args: object, **kwargs: object) -> None:
        raise AssertionError("recording supervisor must not execute worker work")


def _lose_next_product_write_response(database: LocalDatabase) -> None:
    original = database.transaction
    lost = False

    @contextmanager
    def ambiguous_transaction(*, write: bool = False, track_revision: bool = True):
        nonlocal lost
        with original(write=write, track_revision=track_revision) as connection:
            yield connection
        if write and track_revision and not lost:
            lost = True
            raise RuntimeError("Fleet admission commit response lost after commit")

    database.transaction = ambiguous_transaction  # type: ignore[method-assign]


def _plan() -> ResolvedFleetPlan:
    return ResolvedFleetPlan.create(
        name="singleflight",
        description="",
        execution=CicdExecutionPolicy(
            trust_repository_code=True,
            resources=CicdResourcePolicy(cpu_millis=1000, memory_mib=1024, max_processes=256),
        ),
        requirements=(),
        steps=(
            ResolvedCommandStep(
                timeout_seconds=3600,
                step_id="noop",
                argv=("true",),
                capabilities=PlanStepCapabilities(),
            ),
        ),
    )


def test_direct_fleet_admission_replays_exact_bundle_after_ambiguous_commit_response(
    tmp_path: Path,
) -> None:
    database = LocalDatabase(tmp_path / "direct-ambiguous.db")
    journal = Journal(database)
    execution = ExecutionCoordinator(database)
    lifecycle = FleetRunLifecycle(database, journal, execution, _CountingPool(database))  # type: ignore[arg-type]
    repository = RepositoryIdentity(path=tmp_path / "repo")
    intent = resolved_intent(OperationAction.INIT)
    expected_revision = database.durable_revision()

    _lose_next_product_write_response(database)
    ticket, state = lifecycle.admit(
        "direct-fleet-ambiguous",
        intent,
        fleet_repository_scope(None),
        [repository],
        expected_durable_revision=expected_revision,
        repository_admissions=(),
        source=FleetRunSource.MANUAL,
    )

    assert state.status is FleetRunStatus.QUEUED
    assert journal.read_view().run_detail("direct-fleet-ambiguous") is not None
    lease = execution.active_lease()
    assert lease is not None and lease.fleet_run_id == "direct-fleet-ambiguous"

    cancelled = lifecycle.finish_admitted(ticket, FleetRunTerminalIntent.CANCEL)
    assert cancelled.status is FleetRunStatus.CANCELLED
    assert execution.active_lease() is None


def test_direct_replay_identity_mismatch_preserves_original_admission_conflict(
    tmp_path: Path,
) -> None:
    database = LocalDatabase(tmp_path / "direct-replay-mismatch.db")
    journal = Journal(database)
    execution = ExecutionCoordinator(database)
    lifecycle = FleetRunLifecycle(database, journal, execution, _CountingPool(database))  # type: ignore[arg-type]
    repository = RepositoryIdentity(path=tmp_path / "repo")
    scope = fleet_repository_scope(None)

    ticket, _state = lifecycle.admit(
        "direct-fixed",
        resolved_intent(OperationAction.INIT),
        scope,
        [repository],
        expected_durable_revision=database.durable_revision(),
        repository_admissions=(),
        source=FleetRunSource.MANUAL,
    )

    with pytest.raises(ExecutionBusy):
        lifecycle.admit(
            "direct-fixed",
            resolved_intent(OperationAction.VERIFY),
            scope,
            [repository],
            expected_durable_revision=database.durable_revision(),
            repository_admissions=(),
            source=FleetRunSource.MANUAL,
        )

    lifecycle.finish_admitted(ticket, FleetRunTerminalIntent.CANCEL)


def test_lifecycle_cannot_mint_plan_run_without_common_admission_evidence(
    tmp_path: Path,
) -> None:
    database = LocalDatabase(tmp_path / "missing-plan-admission-evidence.db")
    journal = Journal(database)
    execution = ExecutionCoordinator(database)
    executor_pool = _CountingPool(database)
    lifecycle = FleetRunLifecycle(database, journal, execution, executor_pool)  # type: ignore[arg-type]
    repository = RepositoryIdentity(path=tmp_path / "repo")
    target = structural_fleet_plan_target(_plan())
    intent = resolved_intent(OperationAction.EXECUTE_PLAN, target)

    with pytest.raises(StateConflict, match="missing repository/Plan evidence before RUN_ADMITTED"):
        lifecycle.admit(
            "must-not-admit",
            intent,
            fleet_repository_scope({repository.repository_id}),
            [repository],
            expected_durable_revision=database.durable_revision(),
            repository_admissions=(),
            source=FleetRunSource.MANUAL,
        )

    assert journal.read_view().run_detail("must-not-admit") is None
    assert execution.active_lease() is None
    assert executor_pool.admit_calls == 0


def test_known_invalid_plan_creates_no_run_attempt_or_worker_before_admission(
    tmp_path: Path,
) -> None:
    database = LocalDatabase(tmp_path / "reject-before-run.db")
    journal = Journal(database)
    execution = ExecutionCoordinator(database)
    supervisor = _RecordingSupervisor()
    lifecycle = FleetRunLifecycle(database, journal, execution, _CountingPool(database))  # type: ignore[arg-type]
    manager = FleetRunControlPlane(
        _RejectingAdmission(),  # type: ignore[arg-type]
        journal,
        lifecycle,
        _UnusedRunExecution(),  # type: ignore[arg-type]
        supervisor,  # type: ignore[arg-type]
        GameTapeLiveOutputStore(),
    )

    with pytest.raises(TargetSelectionError, match="known-invalid"):
        manager.start_prepared_plan(
            structural_fleet_plan_target(_plan()),
            all_repositories=True,
            fleet_run_id="must-not-exist",
        )

    assert journal.read_view().run_detail("must-not-exist") is None
    assert RepositoryEffectAttemptStore(database).all() == []
    assert execution.active_lease() is None
    assert supervisor.submit_calls == 0


def test_concurrent_fixed_strict_run_identity_admits_and_submits_once(tmp_path: Path) -> None:
    database = LocalDatabase(tmp_path / "singleflight.db")
    journal = Journal(database)
    execution = ExecutionCoordinator(database)
    repository = IncludedRepositoryCatalogEntry(
        scope=FleetScope.INCLUDED, repository=RepositoryIdentity(path=tmp_path / "repo")
    )
    executor_pool = _CountingPool(database)
    supervisor = _RecordingSupervisor()
    lifecycle = FleetRunLifecycle(database, journal, execution, executor_pool)  # type: ignore[arg-type]
    manager = FleetRunControlPlane(
        _Admission(database, repository),  # type: ignore[arg-type]
        journal,
        lifecycle,
        _UnusedRunExecution(),  # type: ignore[arg-type]
        supervisor,  # type: ignore[arg-type]
        GameTapeLiveOutputStore(),
    )
    target = structural_fleet_plan_target(_plan())

    with ThreadPoolExecutor(max_workers=2) as threads:
        results = list(
            threads.map(
                lambda _index: manager.start_prepared_plan(
                    target,
                    all_repositories=True,
                    fleet_run_id="strict-fixed",
                    requested_priority=SchedulingPriority.NORMAL,
                ),
                range(2),
            )
        )

    assert [item.fleet_run_id for item in results] == ["strict-fixed", "strict-fixed"]
    assert executor_pool.admit_calls == 1
    assert supervisor.submit_calls == 1
    events = manager.events("strict-fixed").events
    assert [event.event_type for event in events] == [FleetRunEventType.RUN_ADMITTED]


def test_queue_cancellation_before_admission_commit_leaves_no_run_fact(tmp_path: Path) -> None:
    database = LocalDatabase(tmp_path / "cancel-before-admit.db")
    journal = Journal(database)
    execution = ExecutionCoordinator(database)
    repository = IncludedRepositoryCatalogEntry(
        scope=FleetScope.INCLUDED, repository=RepositoryIdentity(path=tmp_path / "repo")
    )
    executor_pool = _CancelledAdmissionPool(database)
    supervisor = _RecordingSupervisor()
    lifecycle = FleetRunLifecycle(database, journal, execution, executor_pool)  # type: ignore[arg-type]
    manager = FleetRunControlPlane(
        _Admission(database, repository),  # type: ignore[arg-type]
        journal,
        lifecycle,
        _UnusedRunExecution(),  # type: ignore[arg-type]
        supervisor,  # type: ignore[arg-type]
        GameTapeLiveOutputStore(),
    )

    with pytest.raises(ExecutorAdmissionCancelled):
        manager.start_prepared_plan(
            structural_fleet_plan_target(_plan()),
            all_repositories=True,
            fleet_run_id="cancel-before-admit",
        )

    assert executor_pool.admit_calls == 1
    assert supervisor.submit_calls == 0
    assert journal.read_view().run_detail("cancel-before-admit") is None
