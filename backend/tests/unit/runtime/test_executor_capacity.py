from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from oh_goon.core.errors import ExecutorCapacityBusy, FleetPlanInvalid
from oh_goon.core.models.fleet_executor import FleetExecutorIdentity, PlanExecutorCapacityEvidence
from oh_goon.core.models.fleet_plan_evidence import PlanIsolationBackendEvidence
from oh_goon.core.models.fleet_plan_execution_policy import (
    CicdExecutionPolicy,
    ParallelRepositoryExecution,
)
from oh_goon.core.models.fleet_plan_isolation import CicdResourcePolicy
from oh_goon.runtime.database import LocalDatabase
from oh_goon.runtime.executor_capacity import (
    ExecutorCapacitySettings,
    FleetExecutorCapacityReservations,
)


def _backend(*, remote: bool = True, configuration: str = "cfg") -> PlanIsolationBackendEvidence:
    return PlanIsolationBackendEvidence(
        backend_id="remote" if remote else "local",
        configuration_id=configuration,
        kernel_boundary="virtual_machine" if remote else "shared_host_kernel",
        remote_execution=remote,
        experimental=False,
    )


def _execution(
    *,
    cpu: int = 2000,
    memory: int = 4096,
    max_processes: int | None = None,
    parallel: int = 8,
) -> CicdExecutionPolicy:
    return CicdExecutionPolicy(
        trust_repository_code=True,
        resources=CicdResourcePolicy(
            cpu_millis=cpu,
            memory_mib=memory,
            max_processes=max_processes,
        ),
        repositories=ParallelRepositoryExecution(max_parallel=parallel),
    )


def _settings(*, cpu: int = 8000, memory: int = 16384, pids: int | None = None):
    return ExecutorCapacitySettings(
        executor=FleetExecutorIdentity(executor_id="executor-a", generation=1),
        cpu_millis=cpu,
        memory_mib=memory,
        max_processes=pids,
    )


def _store(tmp_path: Path) -> tuple[LocalDatabase, FleetExecutorCapacityReservations]:
    database = LocalDatabase(tmp_path / "state.db")
    return database, FleetExecutorCapacityReservations(database)


def _placement() -> dict[str, object]:
    return {
        "pool_id": "pool-test",
        "placement_policy": "priority_rendezvous_fifo_v2",
        "placement_rank": 0,
        "executor_priority": 10,
    }


def _reserve(
    database: LocalDatabase,
    reservations: FleetExecutorCapacityReservations,
    *,
    settings: ExecutorCapacitySettings,
    fleet_run_id: str,
    backend: PlanIsolationBackendEvidence | None = None,
    execution: CicdExecutionPolicy | None = None,
    target_count: int = 1,
) -> PlanExecutorCapacityEvidence:
    backend_evidence = backend or _backend()
    execution_policy = execution or _execution()
    reservations.validate_candidate(
        settings=settings,
        backend=backend_evidence,
        execution=execution_policy,
        target_count=target_count,
    )
    with database.transaction(write=True) as connection:
        return reservations.reserve_in(
            connection,
            settings=settings,
            fleet_run_id=fleet_run_id,
            backend=backend_evidence,
            execution=execution_policy,
            target_count=target_count,
            **_placement(),
        )


def _release(
    database: LocalDatabase,
    reservations: FleetExecutorCapacityReservations,
    evidence: PlanExecutorCapacityEvidence,
) -> None:
    with database.transaction(write=True) as connection:
        reservations.release_in(connection, evidence)


def test_capacity_reservation_binds_deterministic_parallel_ceiling(tmp_path: Path) -> None:
    database, reservations = _store(tmp_path)
    evidence = _reserve(
        database,
        reservations,
        settings=_settings(),
        fleet_run_id="run-a",
        execution=_execution(),
        target_count=10,
    )

    assert evidence.requested_parallelism == 8
    assert evidence.admitted_parallelism == 4
    assert evidence.reserved_cpu_millis == 8000
    assert evidence.reserved_memory_bytes == 16 * 1024 * 1024 * 1024
    assert evidence.source == "remote_operator_attested"

    payload = evidence.model_dump(mode="json", exclude_computed_fields=True)
    payload["reserved_cpu_millis"] = 1
    with pytest.raises(ValidationError):
        PlanExecutorCapacityEvidence.model_validate(payload)


def test_omitted_resource_caps_inherit_observed_executor_capacity_and_may_change_next_run(
    tmp_path: Path,
) -> None:
    database, reservations = _store(tmp_path)
    execution = CicdExecutionPolicy(
        trust_repository_code=True,
        resources=CicdResourcePolicy(),
        repositories=ParallelRepositoryExecution(max_parallel=8),
    )
    first = _reserve(
        database,
        reservations,
        settings=_settings(cpu=8000, memory=16384, pids=256),
        fleet_run_id="run-inherit-a",
        execution=execution,
        target_count=10,
    )

    assert first.capacity_cpu_millis == 8000
    assert first.capacity_memory_bytes == 16384 * 1024 * 1024
    assert first.capacity_max_processes == 256
    assert first.per_attempt_cpu_millis is None
    assert first.per_attempt_memory_bytes is None
    assert first.per_attempt_max_processes is None
    assert first.reserved_cpu_millis == 0
    assert first.reserved_memory_bytes == 0
    assert first.reserved_max_processes is None
    assert first.admitted_parallelism == 8

    _release(database, reservations, first)
    second = _reserve(
        database,
        reservations,
        settings=_settings(cpu=6000, memory=12288, pids=192),
        fleet_run_id="run-inherit-b",
        execution=execution,
        target_count=10,
    )
    assert second.capacity_cpu_millis == 6000
    assert second.capacity_memory_bytes == 12288 * 1024 * 1024
    assert second.capacity_max_processes == 192
    assert second.per_attempt_cpu_millis is None
    assert second.per_attempt_memory_bytes is None
    assert second.per_attempt_max_processes is None


def test_capacity_reservation_is_sqlite_state_not_filesystem_sidecar(tmp_path: Path) -> None:
    database, reservations = _store(tmp_path)
    evidence = _reserve(database, reservations, settings=_settings(), fleet_run_id="run-sqlite")

    with database.transaction() as connection:
        row = connection.execute(
            "SELECT fleet_run_id, reservation_id, executor_id FROM executor_capacity_reservations"
        ).fetchone()
    assert row is not None
    assert row["fleet_run_id"] == "run-sqlite"
    assert row["reservation_id"] == evidence.reservation_id
    assert row["executor_id"] == "executor-a"
    assert not list(tmp_path.rglob("*.json"))


def test_capacity_reservation_rolls_back_with_caller_transaction(tmp_path: Path) -> None:
    database, reservations = _store(tmp_path)
    with pytest.raises(RuntimeError, match="abort admission"):
        with database.transaction(write=True) as connection:
            reservations.reserve_in(
                connection,
                settings=_settings(),
                fleet_run_id="run-rollback",
                backend=_backend(),
                execution=_execution(),
                target_count=1,
                **_placement(),
            )
            raise RuntimeError("abort admission")

    assert reservations.active_fleet_run_ids("executor-a") == ()


def test_capacity_reservations_prevent_cross_process_style_oversubscription(tmp_path: Path) -> None:
    database, reservations = _store(tmp_path)
    first = _reserve(
        database,
        reservations,
        settings=_settings(),
        fleet_run_id="run-a",
        target_count=10,
    )

    with pytest.raises(ExecutorCapacityBusy, match="insufficient unreserved aggregate capacity"):
        _reserve(
            database,
            reservations,
            settings=_settings(),
            fleet_run_id="run-b",
            target_count=10,
        )

    _release(database, reservations, first)
    second = _reserve(
        database,
        reservations,
        settings=_settings(),
        fleet_run_id="run-b",
        target_count=10,
    )
    assert second.admitted_parallelism == 4


def test_capacity_release_requires_canonical_reservation_evidence(tmp_path: Path) -> None:
    database, reservations = _store(tmp_path)
    evidence = _reserve(database, reservations, settings=_settings(), fleet_run_id="run-release")
    changed = evidence.model_copy(update={"admitted_parallelism": 2})

    with pytest.raises(FleetPlanInvalid, match="identity changed before release"):
        _release(database, reservations, changed)

    reservations.require_bound(_settings(), evidence, _backend())
    _release(database, reservations, evidence)


def test_corrupt_capacity_evidence_fails_closed(tmp_path: Path) -> None:
    database, reservations = _store(tmp_path)
    evidence = _reserve(database, reservations, settings=_settings(), fleet_run_id="run-corrupt")
    with database.transaction(write=True) as connection:
        connection.execute(
            "UPDATE executor_capacity_reservations SET evidence_json = '{}' WHERE fleet_run_id = ?",
            (evidence.fleet_run_id,),
        )

    with pytest.raises(FleetPlanInvalid, match="reservation evidence is invalid"):
        reservations.require_bound(_settings(), evidence, _backend())


def test_pid_capacity_is_required_only_when_plan_requests_pid_envelope(tmp_path: Path) -> None:
    database, reservations = _store(tmp_path)
    with pytest.raises(FleetPlanInvalid, match="max_processes capacity"):
        _reserve(
            database,
            reservations,
            settings=_settings(pids=None),
            fleet_run_id="run-pids",
            execution=_execution(max_processes=128),
            target_count=2,
        )


def test_capacity_parallelism_is_constrained_by_pid_inventory(tmp_path: Path) -> None:
    database, reservations = _store(tmp_path)
    evidence = _reserve(
        database,
        reservations,
        settings=_settings(pids=256),
        fleet_run_id="run-pids",
        execution=_execution(max_processes=128),
        target_count=8,
    )
    assert evidence.admitted_parallelism == 2
    assert evidence.reserved_max_processes == 256


def test_capacity_inventory_cannot_change_while_live_reservation_exists(tmp_path: Path) -> None:
    database, reservations = _store(tmp_path)
    _reserve(
        database,
        reservations,
        settings=_settings(cpu=8000),
        fleet_run_id="run-a",
    )

    with pytest.raises(FleetPlanInvalid, match="inventory changed"):
        _reserve(
            database,
            reservations,
            settings=_settings(cpu=6000),
            fleet_run_id="run-b",
        )


def test_local_inventory_overclaim_fails_closed(tmp_path: Path) -> None:
    database, reservations = _store(tmp_path)
    with pytest.raises(FleetPlanInvalid, match="CPU inventory overclaims"):
        _reserve(
            database,
            reservations,
            settings=_settings(cpu=1_024_000),
            fleet_run_id="run-local",
            backend=_backend(remote=False),
        )


def test_same_run_capacity_reservation_is_not_shared_between_duplicate_admissions(
    tmp_path: Path,
) -> None:
    database, reservations = _store(tmp_path)
    _reserve(
        database,
        reservations,
        settings=_settings(),
        fleet_run_id="run-duplicate",
    )

    with pytest.raises(FleetPlanInvalid, match="single-owner"):
        _reserve(
            database,
            reservations,
            settings=_settings(),
            fleet_run_id="run-duplicate",
        )
