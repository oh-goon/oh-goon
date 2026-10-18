from __future__ import annotations

import hashlib
import sqlite3
from collections.abc import Callable
from typing import TypeVar

from oh_goon.core.errors import (
    ExecutorAdmissionBlocked,
    ExecutorCapacityBusy,
    FleetPlanInvalid,
)
from oh_goon.core.models.fleet_executor import FleetExecutorPlacement
from oh_goon.core.models.fleet_executor_operations import (
    ExecutorAdmissionMode,
    ExecutorTrustState,
    PlacementQueueEntry,
    PlacementQueueSnapshot,
)
from oh_goon.core.models.fleet_plan import ResolvedFleetPlan
from oh_goon.core.models.fleet_plan_execution_policy import CicdExecutionPolicy
from oh_goon.core.models.fleet_plan_isolation import isolation_requirements
from oh_goon.core.models.repository import RepositoryIdentity
from oh_goon.core.models.run_admission import PlanRepositoryAdmissionEvidence
from oh_goon.core.models.scheduling import SchedulingEnvelope, SchedulingPriority
from oh_goon.core.product_identity import stable_id
from oh_goon.core.time import monotonic_now, wait_seconds
from oh_goon.integrations._fleet_plan_host_resources import (
    preflight_executor_tool_feasibility,
    preflight_host_environment_feasibility,
)
from oh_goon.integrations.fleet_plan_process_isolation import isolation_backend_evidence
from oh_goon.integrations.fleet_plan_repository_scope import admitted_active_plan
from oh_goon.runtime._executor_configuration import (
    ConfiguredFleetExecutor,
    ExecutorPoolConfiguration,
)
from oh_goon.runtime._executor_operations import ExecutorOperatorControls
from oh_goon.runtime._executor_placement_queue import ExecutorPlacementQueue
from oh_goon.runtime.database import LocalDatabase
from oh_goon.runtime.executor_capacity import FleetExecutorCapacityReservations

T = TypeVar("T")


class _CandidateRejected(Exception):
    def __init__(self, reason: FleetPlanInvalid) -> None:
        self.reason = reason
        super().__init__(str(reason))


class _OperationalRejected(Exception):
    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


_PLACEMENT_POLICY = "priority_rendezvous_fifo_v2"


class ExecutorPlacementAdmission:
    """Own one queued strict-placement lifecycle and its final durable commit cut."""

    def __init__(
        self,
        database: LocalDatabase,
        configuration: ExecutorPoolConfiguration,
        *,
        queue: ExecutorPlacementQueue | None = None,
        monotonic: Callable[[], float] = monotonic_now,
        wait: Callable[[float], None] = wait_seconds,
    ) -> None:
        self._database = database
        self._configuration = configuration
        self._queue = queue if queue is not None else ExecutorPlacementQueue(database)
        self._controls = ExecutorOperatorControls(database, configuration)
        self._reservations = FleetExecutorCapacityReservations(database)
        self._pool_id = _pool_identity(configuration)
        self._monotonic = monotonic
        self._wait = wait

    def admit_with(
        self,
        *,
        fleet_run_id: str,
        plan: ResolvedFleetPlan,
        targets: tuple[RepositoryIdentity, ...],
        repository_admissions: tuple[PlanRepositoryAdmissionEvidence, ...] | None = None,
        requested_priority: SchedulingPriority = SchedulingPriority.NORMAL,
        commit: Callable[[sqlite3.Connection, FleetExecutorPlacement], T],
    ) -> tuple[FleetExecutorPlacement, T]:
        """Wait/probe outside SQLite, then atomically reserve placement and commit Fleet admission."""
        execution = plan.execution
        if not isinstance(execution, CicdExecutionPolicy):
            raise FleetPlanInvalid("Executor placement is only valid for strict CI/CD plans")
        if repository_admissions is None:
            if plan.imports:
                raise FleetPlanInvalid(
                    "Strict executor admission requires transported repository semantics for imported Goons"
                )
            active_plans = tuple(plan for _target in targets)
        else:
            if {item.repository_id for item in repository_admissions} != {
                item.repository_id for item in targets
            }:
                raise FleetPlanInvalid(
                    "Strict executor admission requires semantic evidence for every selected repository"
                )
            active_plans = tuple(admitted_active_plan(plan, item) for item in repository_admissions)
        preflight_executor_tool_feasibility(active_plans, self._configuration.path_authority)
        preflight_host_environment_feasibility(active_plans)
        _preflight_configured_executor_feasibility(self._configuration.executors, execution)
        target_count = len(targets)
        ticket = self._queue.enter(
            fleet_run_id,
            max_entries=self._configuration.placement_queue_limit,
            requested_priority=requested_priority,
            initially_eligible=False,
        )
        candidates = _placement_order(self._configuration.executors, fleet_run_id)
        deadline = self._monotonic() + self._configuration.placement_wait_seconds
        committed = False
        try:
            while True:
                self._require_not_all_compatible_candidates_blocked(execution, candidates)
                ready = self._currently_eligible(
                    plan=plan, execution=execution, target_count=target_count, candidates=candidates
                )
                self._queue.set_eligibility(ticket, ready=ready)
                if not ready or not self._queue.is_turn(ticket):
                    _wait_for_placement_turn(deadline, monotonic=self._monotonic, wait=self._wait)
                    continue
                result, failures, capacity_busy, operational_blocked = self._try_once(
                    ticket=ticket,
                    fleet_run_id=fleet_run_id,
                    plan=plan,
                    execution=execution,
                    target_count=target_count,
                    candidates=candidates,
                    commit=commit,
                )
                if result is not None:
                    committed = True
                    return result
                detail = " | ".join(failures) if failures else "no configured executors"
                if capacity_busy:
                    self._queue.set_eligibility(ticket, ready=False)
                    remaining = deadline - self._monotonic()
                    if remaining > 0:
                        self._wait(min(0.05, remaining))
                        continue
                    raise ExecutorCapacityBusy(
                        "Fleet executor placement wait expired before aggregate capacity became "
                        "available for one complete attempt: " + detail
                    )
                if operational_blocked:
                    raise ExecutorAdmissionBlocked(
                        "Fleet executor operator state blocks new placement: " + detail
                    )
                raise FleetPlanInvalid(
                    "No Fleet executor can satisfy the admitted CI/CD run: " + detail
                )
        finally:
            if not committed:
                self._queue.leave(ticket)

    def snapshot(self) -> PlacementQueueSnapshot:
        return self._queue.snapshot()

    def scheduling_receipt_in(
        self, connection: sqlite3.Connection, fleet_run_id: str
    ) -> SchedulingEnvelope | None:
        return self._queue.scheduling_receipt_in(connection, fleet_run_id)

    def cancel(self, fleet_run_id: str, *, expected_sequence: int) -> PlacementQueueEntry:
        return self._queue.cancel(fleet_run_id, expected_sequence=expected_sequence)

    def reprioritize(
        self,
        fleet_run_id: str,
        *,
        admitted_priority: SchedulingPriority,
        expected_revision: int,
        reason: str,
    ) -> PlacementQueueEntry:
        return self._queue.reprioritize(
            fleet_run_id,
            admitted_priority=admitted_priority,
            expected_revision=expected_revision,
            reason=reason,
        )

    def _require_not_all_compatible_candidates_blocked(
        self,
        execution: CicdExecutionPolicy,
        candidates: tuple[ConfiguredFleetExecutor, ...],
    ) -> None:
        requirements = isolation_requirements(execution.trust_repository_code, execution.resources)
        compatible = [
            candidate
            for candidate in candidates
            if candidate.backend.capabilities.satisfies(requirements)
        ]
        if not compatible:
            return
        reasons = [
            (
                candidate.identity.executor_id,
                _new_admission_block_reason(self._controls.state(candidate.identity.executor_id)),
            )
            for candidate in compatible
        ]
        if any(reason is None for _, reason in reasons):
            return
        detail = " | ".join(f"{executor_id}: {reason}" for executor_id, reason in reasons)
        raise ExecutorAdmissionBlocked(
            "Fleet executor operator state blocks new placement: " + detail
        )

    def _currently_eligible(
        self,
        *,
        plan: ResolvedFleetPlan,
        execution: CicdExecutionPolicy,
        target_count: int,
        candidates: tuple[ConfiguredFleetExecutor, ...],
    ) -> bool:
        """Project whether this queued request can compete for capacity now.

        Physical/tool/operator checks remain outside the final transaction. The
        result only controls queue competition; final admission re-proves the
        authoritative conditions before consuming the ticket.
        """
        requirements = isolation_requirements(execution.trust_repository_code, execution.resources)
        for candidate in candidates:
            backend = candidate.backend
            if not backend.capabilities.satisfies(requirements):
                continue
            if (
                _new_admission_block_reason(self._controls.state(candidate.identity.executor_id))
                is not None
            ):
                continue
            backend_evidence = isolation_backend_evidence(backend)
            try:
                backend.require_available(plan)
                if self._reservations.capacity_available(
                    settings=candidate.capacity,
                    backend=backend_evidence,
                    execution=execution,
                    target_count=target_count,
                ):
                    return True
            except FleetPlanInvalid:
                continue
        return False

    def _try_once(
        self,
        *,
        ticket,
        fleet_run_id: str,
        plan: ResolvedFleetPlan,
        execution: CicdExecutionPolicy,
        target_count: int,
        candidates: tuple[ConfiguredFleetExecutor, ...],
        commit: Callable[[sqlite3.Connection, FleetExecutorPlacement], T],
    ) -> tuple[tuple[FleetExecutorPlacement, T] | None, list[str], bool, bool]:
        requirements = isolation_requirements(execution.trust_repository_code, execution.resources)
        failures: list[str] = []
        capacity_busy = False
        operational_blocked = False
        for rank, candidate in enumerate(candidates):
            backend = candidate.backend
            if not backend.capabilities.satisfies(requirements):
                failures.append(
                    f"{candidate.identity.executor_id}: isolation requirements not satisfied"
                )
                continue
            backend_evidence = isolation_backend_evidence(backend)
            operational = self._controls.state(candidate.identity.executor_id)
            blocked = _new_admission_block_reason(operational)
            if blocked is not None:
                operational_blocked = True
                failures.append(f"{candidate.identity.executor_id}: {blocked}")
                continue
            try:
                # Physical observations stay outside the final SQLite admission transaction.
                backend.require_available(plan)
                self._reservations.validate_candidate(
                    settings=candidate.capacity,
                    backend=backend_evidence,
                    execution=execution,
                    target_count=target_count,
                )
            except FleetPlanInvalid as exc:
                failures.append(f"{candidate.identity.executor_id}: {exc}")
                continue
            try:
                admitted = self._commit_candidate(
                    ticket=ticket,
                    fleet_run_id=fleet_run_id,
                    candidate=candidate,
                    backend_evidence=backend_evidence,
                    execution=execution,
                    target_count=target_count,
                    rank=rank,
                    commit=commit,
                )
                return admitted, failures, capacity_busy, operational_blocked
            except ExecutorCapacityBusy as exc:
                capacity_busy = True
                failures.append(f"{candidate.identity.executor_id}: {exc}")
            except _OperationalRejected as rejected:
                operational_blocked = True
                failures.append(f"{candidate.identity.executor_id}: {rejected.reason}")
            except _CandidateRejected as rejected:
                failures.append(f"{candidate.identity.executor_id}: {rejected.reason}")
        return None, failures, capacity_busy, operational_blocked

    def _commit_candidate(
        self,
        *,
        ticket,
        fleet_run_id: str,
        candidate: ConfiguredFleetExecutor,
        backend_evidence,
        execution: CicdExecutionPolicy,
        target_count: int,
        rank: int,
        commit: Callable[[sqlite3.Connection, FleetExecutorPlacement], T],
    ) -> tuple[FleetExecutorPlacement, T]:
        """Own the final transactional re-proof and consumption of placement authority."""
        with self._database.transaction(write=True) as connection:
            self._queue.require_turn_in(connection, ticket)
            self._controls.require_current_generation_in(
                connection, candidate.identity.executor_id, candidate.identity.generation
            )
            operational = self._controls.state_in(connection, candidate.identity.executor_id)
            final_blocked = _new_admission_block_reason(operational)
            if final_blocked is not None:
                raise _OperationalRejected(final_blocked)
            try:
                evidence = self._reservations.reserve_in(
                    connection,
                    settings=candidate.capacity,
                    fleet_run_id=fleet_run_id,
                    backend=backend_evidence,
                    execution=execution,
                    target_count=target_count,
                    pool_id=self._pool_id,
                    placement_policy=_PLACEMENT_POLICY,
                    placement_rank=rank,
                    executor_priority=candidate.priority,
                )
            except FleetPlanInvalid as exc:
                raise _CandidateRejected(exc) from exc
            placement = FleetExecutorPlacement(
                isolation_backend=backend_evidence,
                executor_capacity=evidence,
                executor_trust_epoch=operational.trust_epoch,
            )
            self._queue.consume_in(connection, ticket)
            return placement, commit(connection, placement)


def _preflight_configured_executor_feasibility(
    executors: tuple[ConfiguredFleetExecutor, ...], execution: CicdExecutionPolicy
) -> None:
    """Reject immutable executor-inventory impossibility before entering the queue."""

    requirements = isolation_requirements(execution.trust_repository_code, execution.resources)
    failures: list[str] = []
    for candidate in executors:
        reasons: list[str] = []
        if not candidate.backend.capabilities.satisfies(requirements):
            reasons.append("isolation requirements not satisfied")
        if (
            execution.resources.cpu_millis is not None
            and candidate.capacity.cpu_millis < execution.resources.cpu_millis
        ):
            reasons.append(
                "configured CPU capacity "
                f"{candidate.capacity.cpu_millis}m < required {execution.resources.cpu_millis}m"
            )
        if (
            execution.resources.memory_bytes is not None
            and candidate.capacity.memory_bytes < execution.resources.memory_bytes
        ):
            reasons.append(
                "configured memory capacity "
                f"{candidate.capacity.memory_mib} MiB < required {execution.resources.memory_mib} MiB"
            )
        required_processes = execution.resources.max_processes
        if required_processes is not None:
            if candidate.capacity.max_processes is None:
                reasons.append("configured process capacity is absent")
            elif candidate.capacity.max_processes < required_processes:
                reasons.append(
                    "configured process capacity "
                    f"{candidate.capacity.max_processes} < required {required_processes}"
                )
        if not reasons:
            return
        failures.append(f"{candidate.identity.executor_id}: " + "; ".join(reasons))
    detail = " | ".join(failures) if failures else "no configured executors"
    raise FleetPlanInvalid(
        "No Fleet executor can satisfy the configured strict CI/CD requirements before queueing: "
        + detail
    )


def _new_admission_block_reason(operational) -> str | None:
    if operational.admission_mode is ExecutorAdmissionMode.DRAINING:
        return "draining; new placement disabled"
    if operational.trust_state is ExecutorTrustState.REVOKED:
        return "trust revoked; new placement disabled"
    return None


def _placement_order(
    executors: tuple[ConfiguredFleetExecutor, ...], fleet_run_id: str
) -> tuple[ConfiguredFleetExecutor, ...]:
    return tuple(
        sorted(
            executors,
            key=lambda item: (
                item.priority,
                -_rendezvous_score(fleet_run_id, item.identity.executor_id),
                item.identity.executor_id,
            ),
        )
    )


def _rendezvous_score(fleet_run_id: str, executor_id: str) -> int:
    digest = hashlib.sha256(
        f"oh-goon-executor-placement-v1\0{fleet_run_id}\0{executor_id}".encode()
    ).digest()
    return int.from_bytes(digest[:8], "big", signed=False)


def _pool_identity(configuration: ExecutorPoolConfiguration) -> str:
    payload = {
        "placement_policy": _PLACEMENT_POLICY,
        "placement_wait_seconds": configuration.placement_wait_seconds,
        "placement_queue_limit": configuration.placement_queue_limit,
        "executors": [
            {
                "executor_id": item.identity.executor_id,
                "executor_generation": item.identity.generation,
                "priority": item.priority,
                "backend_configuration_id": item.backend.configuration_identity(),
                "cpu_millis": item.capacity.cpu_millis,
                "memory_mib": item.capacity.memory_mib,
                "max_processes": item.capacity.max_processes,
            }
            for item in sorted(
                configuration.executors,
                key=lambda candidate: candidate.identity.executor_id,
            )
        ],
    }
    return stable_id("fleet-executor-pool", payload)


def _wait_for_placement_turn(
    deadline: float,
    *,
    monotonic: Callable[[], float],
    wait: Callable[[float], None],
) -> None:
    remaining = deadline - monotonic()
    if remaining <= 0:
        raise ExecutorCapacityBusy(
            "Fleet executor placement wait expired behind an earlier admission request"
        )
    wait(min(0.05, remaining))
