from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from pydantic import ValidationError

from oh_goon.core.errors import ExecutorCapacityBusy, FleetPlanInvalid
from oh_goon.core.models.fleet_executor import (
    ExecutorCapacityEvidenceInput,
    FleetExecutorIdentity,
    PlanExecutorCapacityEvidence,
)
from oh_goon.core.models.fleet_plan_evidence import PlanIsolationBackendEvidence
from oh_goon.core.models.fleet_plan_execution_policy import (
    CicdExecutionPolicy,
    ParallelRepositoryExecution,
)
from oh_goon.runtime._executor_local_capacity import observe_local_executor_capacity
from oh_goon.runtime.database import LocalDatabase


class ExecutorCapacitySource(StrEnum):
    LOCAL_VERIFIED = "local_verified"
    REMOTE_OPERATOR_ATTESTED = "remote_operator_attested"


@dataclass(frozen=True, slots=True)
class ExecutorCapacitySettings:
    """Operator-owned aggregate Fleet executor budget captured before admission."""

    executor: FleetExecutorIdentity
    cpu_millis: int
    memory_mib: int
    max_processes: int | None
    cgroup_root: Path | None = None

    @property
    def memory_bytes(self) -> int:
        return self.memory_mib * 1024 * 1024


class FleetExecutorCapacityReservations:
    """SQLite-backed aggregate capacity reservations for admitted strict Fleet runs.

    A reservation is never created before durable Fleet admission. Callers use
    ``reserve_in`` in the same transaction as execution-lease creation and
    RUN_ADMITTED, and ``release_run_in`` in the same terminal transaction.
    """

    def __init__(self, database: LocalDatabase) -> None:
        self._database = database

    def validate_candidate(
        self,
        *,
        settings: ExecutorCapacitySettings,
        backend: PlanIsolationBackendEvidence,
        execution: CicdExecutionPolicy,
        target_count: int,
    ) -> None:
        """Validate physical executor inventory before the short admission transaction."""
        if target_count < 1:
            raise FleetPlanInvalid(
                "Strict Fleet CI/CD capacity admission requires at least one target"
            )
        source = _capacity_source(backend)
        if source is ExecutorCapacitySource.LOCAL_VERIFIED:
            _verify_local_inventory(settings)
        _require_process_inventory(settings, execution)

    def capacity_available(
        self,
        *,
        settings: ExecutorCapacitySettings,
        backend: PlanIsolationBackendEvidence,
        execution: CicdExecutionPolicy,
        target_count: int,
    ) -> bool:
        """Return whether one attempt can currently fit without reserving it.

        This is only a scheduling hint. ``reserve_in`` remains the authority and
        rechecks the same constraints atomically in the final admission cut.
        """
        self.validate_candidate(
            settings=settings, backend=backend, execution=execution, target_count=target_count
        )
        requested = _requested_parallelism(execution, target_count)
        with self._database.transaction() as connection:
            active = self._active_in(connection)
            _validate_inventory_consistency(settings, active)
            try:
                _available_parallelism(
                    settings=settings, execution=execution, active=active, requested=requested
                )
            except ExecutorCapacityBusy:
                return False
        return True

    def reserve_in(
        self,
        connection: sqlite3.Connection,
        *,
        settings: ExecutorCapacitySettings,
        fleet_run_id: str,
        backend: PlanIsolationBackendEvidence,
        execution: CicdExecutionPolicy,
        target_count: int,
        pool_id: str,
        placement_policy: str,
        placement_rank: int,
        executor_priority: int,
    ) -> PlanExecutorCapacityEvidence:
        if target_count < 1:
            raise FleetPlanInvalid(
                "Strict Fleet CI/CD capacity admission requires at least one target"
            )
        source = _capacity_source(backend)
        _require_process_inventory(settings, execution)
        requested = _requested_parallelism(execution, target_count)
        active = self._active_in(connection)
        _validate_inventory_consistency(settings, active)
        _reject_existing_reservation(active, fleet_run_id=fleet_run_id, backend=backend)
        admitted = _available_parallelism(
            settings=settings,
            execution=execution,
            active=active,
            requested=requested,
        )
        evidence = _create_capacity_evidence(
            settings=settings,
            execution=execution,
            backend=backend,
            source=source,
            fleet_run_id=fleet_run_id,
            requested=requested,
            admitted=admitted,
            pool_id=pool_id,
            placement_policy=placement_policy,
            placement_rank=placement_rank,
            executor_priority=executor_priority,
        )
        connection.execute(
            """
            INSERT INTO executor_capacity_reservations(
                fleet_run_id, reservation_id, executor_id, evidence_json
            ) VALUES (?, ?, ?, ?)
            """,
            (
                fleet_run_id,
                evidence.reservation_id,
                evidence.executor_id,
                evidence.model_dump_json(exclude_computed_fields=True),
            ),
        )
        return evidence

    def validate_bound_candidate(
        self,
        settings: ExecutorCapacitySettings,
        evidence: PlanExecutorCapacityEvidence,
        backend: PlanIsolationBackendEvidence,
    ) -> None:
        """Validate exact configured/physical inventory before reading reservation state."""
        expected_source = _capacity_source(backend)
        expected = (
            settings.executor.executor_id,
            settings.executor.generation,
            expected_source.value,
            settings.cpu_millis,
            settings.memory_bytes,
            settings.max_processes,
            backend.configuration_id,
        )
        observed = (
            evidence.executor_id,
            evidence.executor_generation,
            evidence.source,
            evidence.capacity_cpu_millis,
            evidence.capacity_memory_bytes,
            evidence.capacity_max_processes,
            evidence.backend_configuration_id,
        )
        if observed != expected:
            raise FleetPlanInvalid(
                "Fleet executor capacity/backend configuration changed after RUN_ADMITTED"
            )
        if expected_source is ExecutorCapacitySource.LOCAL_VERIFIED:
            _verify_local_inventory(settings)

    @staticmethod
    def require_bound_in(
        connection: sqlite3.Connection,
        evidence: PlanExecutorCapacityEvidence,
    ) -> None:
        row = connection.execute(
            "SELECT reservation_id, evidence_json FROM executor_capacity_reservations WHERE fleet_run_id = ?",
            (evidence.fleet_run_id,),
        ).fetchone()
        if row is None:
            raise FleetPlanInvalid(
                "Fleet executor-capacity reservation disappeared after RUN_ADMITTED"
            )
        stored = _decode_evidence(str(row["evidence_json"]))
        if str(row["reservation_id"]) != evidence.reservation_id or stored != evidence:
            raise FleetPlanInvalid("Fleet executor-capacity reservation changed after RUN_ADMITTED")

    def require_bound(
        self,
        settings: ExecutorCapacitySettings,
        evidence: PlanExecutorCapacityEvidence,
        backend: PlanIsolationBackendEvidence,
    ) -> None:
        """Revalidate the exact executor inventory/reservation before target execution."""
        self.validate_bound_candidate(settings, evidence, backend)
        with self._database.transaction() as connection:
            self.require_bound_in(connection, evidence)

    def release_in(
        self,
        connection: sqlite3.Connection,
        evidence: PlanExecutorCapacityEvidence,
    ) -> None:
        row = connection.execute(
            "SELECT reservation_id FROM executor_capacity_reservations WHERE fleet_run_id = ?",
            (evidence.fleet_run_id,),
        ).fetchone()
        if row is None:
            return
        if str(row["reservation_id"]) != evidence.reservation_id:
            raise FleetPlanInvalid(
                "Fleet executor-capacity reservation identity changed before release"
            )
        connection.execute(
            "DELETE FROM executor_capacity_reservations WHERE fleet_run_id = ?",
            (evidence.fleet_run_id,),
        )

    @staticmethod
    def release_run_in(connection: sqlite3.Connection, fleet_run_id: str) -> None:
        connection.execute(
            "DELETE FROM executor_capacity_reservations WHERE fleet_run_id = ?",
            (fleet_run_id,),
        )

    def active_fleet_run_ids(self, executor_id: str) -> tuple[str, ...]:
        """Return admitted reservations for one logical executor."""
        with self._database.transaction() as connection:
            return self.active_fleet_run_ids_in(connection, executor_id)

    @staticmethod
    def active_fleet_run_ids_in(
        connection: sqlite3.Connection, executor_id: str
    ) -> tuple[str, ...]:
        """Read admitted reservations inside a caller-owned durable cut."""
        rows = connection.execute(
            """
            SELECT fleet_run_id
            FROM executor_capacity_reservations
            WHERE executor_id = ?
            ORDER BY fleet_run_id
            """,
            (executor_id,),
        ).fetchall()
        return tuple(str(row["fleet_run_id"]) for row in rows)

    @staticmethod
    def _active_in(connection: sqlite3.Connection) -> list[PlanExecutorCapacityEvidence]:
        rows = connection.execute(
            "SELECT reservation_id, executor_id, evidence_json FROM executor_capacity_reservations"
        ).fetchall()
        active: list[PlanExecutorCapacityEvidence] = []
        for row in rows:
            evidence = _decode_evidence(str(row["evidence_json"]))
            if (
                str(row["reservation_id"]) != evidence.reservation_id
                or str(row["executor_id"]) != evidence.executor_id
            ):
                raise FleetPlanInvalid(
                    "Fleet executor-capacity reservation metadata is inconsistent"
                )
            active.append(evidence)
        return active


def _decode_evidence(payload: str) -> PlanExecutorCapacityEvidence:
    try:
        return PlanExecutorCapacityEvidence.model_validate_json(payload)
    except (ValidationError, ValueError) as exc:
        raise FleetPlanInvalid("Fleet executor-capacity reservation evidence is invalid") from exc


def _capacity_source(backend: PlanIsolationBackendEvidence) -> ExecutorCapacitySource:
    if backend.remote_execution:
        return ExecutorCapacitySource.REMOTE_OPERATOR_ATTESTED
    return ExecutorCapacitySource.LOCAL_VERIFIED


def _require_process_inventory(
    settings: ExecutorCapacitySettings, execution: CicdExecutionPolicy
) -> None:
    if execution.resources.max_processes is not None and settings.max_processes is None:
        raise FleetPlanInvalid(
            "Fleet plan requests max_processes but executor inventory has no max_processes capacity"
        )


def _reject_existing_reservation(
    active: list[PlanExecutorCapacityEvidence],
    *,
    fleet_run_id: str,
    backend: PlanIsolationBackendEvidence,
) -> None:
    evidence = next(
        (current for current in active if current.fleet_run_id == fleet_run_id),
        None,
    )
    if evidence is None:
        return
    if evidence.backend_configuration_id != backend.configuration_id:
        raise FleetPlanInvalid(
            "Fleet run identity is already bound to another executor reservation"
        )
    raise FleetPlanInvalid(
        "Fleet run already owns a live capacity reservation; "
        "reservation acquisition is single-owner and cannot be shared by a duplicate start"
    )


def _available_parallelism(
    *,
    settings: ExecutorCapacitySettings,
    execution: CicdExecutionPolicy,
    active: list[PlanExecutorCapacityEvidence],
    requested: int,
) -> int:
    same_executor = [
        evidence for evidence in active if evidence.executor_id == settings.executor.executor_id
    ]
    used_cpu = sum(evidence.reserved_cpu_millis for evidence in same_executor)
    used_memory = sum(evidence.reserved_memory_bytes for evidence in same_executor)
    used_pids = sum(evidence.reserved_max_processes or 0 for evidence in same_executor)
    limits = [requested]
    if execution.resources.cpu_millis is not None:
        limits.append((settings.cpu_millis - used_cpu) // execution.resources.cpu_millis)
    if execution.resources.memory_bytes is not None:
        limits.append((settings.memory_bytes - used_memory) // execution.resources.memory_bytes)
    if execution.resources.max_processes is not None:
        assert settings.max_processes is not None
        limits.append((settings.max_processes - used_pids) // execution.resources.max_processes)
    admitted = min(requested, *limits)
    if admitted < 1:
        raise ExecutorCapacityBusy(
            "Fleet executor has insufficient unreserved aggregate capacity "
            "for one admitted CI/CD attempt"
        )
    return admitted


def _create_capacity_evidence(
    *,
    settings: ExecutorCapacitySettings,
    execution: CicdExecutionPolicy,
    backend: PlanIsolationBackendEvidence,
    source: ExecutorCapacitySource,
    fleet_run_id: str,
    requested: int,
    admitted: int,
    pool_id: str,
    placement_policy: str,
    placement_rank: int,
    executor_priority: int,
) -> PlanExecutorCapacityEvidence:
    return PlanExecutorCapacityEvidence.create(
        ExecutorCapacityEvidenceInput(
            fleet_run_id=fleet_run_id,
            executor=settings.executor,
            source=source.value,
            backend_configuration_id=backend.configuration_id,
            capacity_cpu_millis=settings.cpu_millis,
            capacity_memory_bytes=settings.memory_bytes,
            capacity_max_processes=settings.max_processes,
            requested_parallelism=requested,
            admitted_parallelism=admitted,
            per_attempt_cpu_millis=execution.resources.cpu_millis,
            per_attempt_memory_bytes=execution.resources.memory_bytes,
            per_attempt_max_processes=execution.resources.max_processes,
            pool_id=pool_id,
            placement_policy=placement_policy,
            placement_rank=placement_rank,
            executor_priority=executor_priority,
        )
    )


def _requested_parallelism(execution: CicdExecutionPolicy, target_count: int) -> int:
    repositories = execution.repositories
    if isinstance(repositories, ParallelRepositoryExecution):
        return min(repositories.max_parallel, target_count)
    return 1


def _verify_local_inventory(settings: ExecutorCapacitySettings) -> None:
    observed = observe_local_executor_capacity(settings.cgroup_root)
    if settings.cpu_millis > observed.cpu_millis:
        raise FleetPlanInvalid(
            "Fleet executor CPU inventory overclaims the local executor: "
            f"configured={settings.cpu_millis}m observed<={observed.cpu_millis}m"
        )
    configured_memory = settings.memory_bytes
    if configured_memory > observed.memory_bytes:
        raise FleetPlanInvalid(
            "Fleet executor memory inventory overclaims the local executor: "
            f"configured={configured_memory} observed<={observed.memory_bytes}"
        )
    if (
        settings.max_processes is not None
        and observed.max_processes is not None
        and settings.max_processes > observed.max_processes
    ):
        raise FleetPlanInvalid(
            "Fleet executor PID inventory overclaims the local executor: "
            f"configured={settings.max_processes} observed<={observed.max_processes}"
        )


def _validate_inventory_consistency(
    settings: ExecutorCapacitySettings,
    active: list[PlanExecutorCapacityEvidence],
) -> None:
    for evidence in active:
        if evidence.executor_id != settings.executor.executor_id:
            continue
        expected = (
            settings.executor.generation,
            settings.cpu_millis,
            settings.memory_bytes,
            settings.max_processes,
        )
        actual = (
            evidence.executor_generation,
            evidence.capacity_cpu_millis,
            evidence.capacity_memory_bytes,
            evidence.capacity_max_processes,
        )
        if actual != expected:
            raise FleetPlanInvalid(
                "Fleet executor capacity inventory changed while an admitted reservation is active"
            )
