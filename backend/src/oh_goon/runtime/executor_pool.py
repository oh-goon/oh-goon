from __future__ import annotations

import sqlite3
from collections.abc import Callable
from typing import TypeVar

from oh_goon.core.errors import (
    ExecutorGenerationSuperseded,
    ExecutorPlacementLost,
    ExecutorTrustRevoked,
    FleetPlanInvalid,
    StateConflict,
)
from oh_goon.core.models.fleet_executor import (
    FleetExecutorPlacement,
    PlanExecutorCapacityEvidence,
)
from oh_goon.core.models.fleet_executor_operations import (
    ExecutorTrustState,
    FleetExecutorPoolSnapshot,
    FleetExecutorStatus,
    PlacementQueueEntry,
    PlacementQueueSnapshot,
)
from oh_goon.core.models.fleet_plan import ResolvedFleetPlan
from oh_goon.core.models.fleet_plan_evidence import PlanIsolationBackendEvidence
from oh_goon.core.models.repository import RepositoryIdentity
from oh_goon.core.models.run_admission import PlanRepositoryAdmissionEvidence
from oh_goon.core.models.scheduling import SchedulingEnvelope, SchedulingPriority
from oh_goon.core.strict_executor_authority import EXECUTORS_FILE_ENV
from oh_goon.integrations.fleet_plan_process_isolation import (
    ProcessIsolationBackend,
    isolation_backend_evidence,
)
from oh_goon.runtime._executor_configuration import (
    ConfiguredFleetExecutor,
    ExecutorPoolConfiguration,
)
from oh_goon.runtime._executor_operations import ExecutorOperatorControls
from oh_goon.runtime._executor_placement import ExecutorPlacementAdmission
from oh_goon.runtime._executor_placement_queue import ExecutorPlacementQueue
from oh_goon.runtime.database import LocalDatabase
from oh_goon.runtime.executor_capacity import FleetExecutorCapacityReservations

T = TypeVar("T")


class FleetExecutorPool:
    """Sole public owner of deployment-wide executor placement and operator controls."""

    def __init__(
        self,
        database: LocalDatabase,
        configuration: ExecutorPoolConfiguration | None,
    ) -> None:
        self._database = database
        self._configuration = configuration
        self._placement_authority: ExecutorPlacementAdmission | None = None
        self._placement_queue_authority: ExecutorPlacementQueue | None = None

    def _configured(self) -> ExecutorPoolConfiguration:
        if self._configuration is None:
            raise FleetPlanInvalid(
                f"This Fleet process started without {EXECUTORS_FILE_ENV}; restart after "
                "configuring the executor inventory before admitting strict CI/CD"
            )
        return self._configuration

    def _placement(self) -> ExecutorPlacementAdmission:
        # One placement authority owns this pool lifetime. Reconstructing it from
        # inside an admission transaction would re-run generation publication and
        # attempt a nested SQLite write while projecting the scheduling receipt.
        if self._placement_authority is None:
            self._placement_authority = ExecutorPlacementAdmission(
                self._database, self._configured(), queue=self._placement_queue()
            )
        return self._placement_authority

    def _placement_queue(self) -> ExecutorPlacementQueue:
        """Return the one durable queue owner without publishing executor generations.

        Admin queue reads/mutations do not establish a strict placement session.
        Constructing placement would otherwise publish *all* configured executor
        generations before a queue sequence/revision had been checked.
        """
        self._configured()
        if self._placement_queue_authority is None:
            self._placement_queue_authority = ExecutorPlacementQueue(self._database)
        return self._placement_queue_authority

    def admit_strict_with(
        self,
        *,
        fleet_run_id: str,
        plan: ResolvedFleetPlan,
        targets: tuple[RepositoryIdentity, ...],
        repository_admissions: tuple[PlanRepositoryAdmissionEvidence, ...] | None = None,
        requested_priority: SchedulingPriority = SchedulingPriority.NORMAL,
        commit: Callable[[sqlite3.Connection, FleetExecutorPlacement], T],
    ) -> tuple[FleetExecutorPlacement, T]:
        """Commit strict placement only through the caller's Fleet admission mutation."""
        return self._placement().admit_with(
            fleet_run_id=fleet_run_id,
            plan=plan,
            targets=targets,
            repository_admissions=repository_admissions,
            requested_priority=requested_priority,
            commit=commit,
        )

    def placement_queue(self) -> PlacementQueueSnapshot:
        return self._placement_queue().snapshot()

    def scheduling_receipt_in(
        self, connection: sqlite3.Connection, fleet_run_id: str
    ) -> SchedulingEnvelope | None:
        """Project immutable admitted scheduling evidence without transferring ownership."""
        return self._placement().scheduling_receipt_in(connection, fleet_run_id)

    def cancel_queued_admission(
        self, fleet_run_id: str, *, expected_sequence: int
    ) -> PlacementQueueEntry:
        return self._placement_queue().cancel(fleet_run_id, expected_sequence=expected_sequence)

    def reprioritize_queued_admission(
        self,
        fleet_run_id: str,
        *,
        admitted_priority: SchedulingPriority,
        expected_revision: int,
        reason: str,
    ) -> PlacementQueueEntry:
        """Mutate queued scheduling metadata only through Scheduling Authority CAS."""
        return self._placement_queue().reprioritize(
            fleet_run_id,
            admitted_priority=admitted_priority,
            expected_revision=expected_revision,
            reason=reason,
        )

    def executor_snapshot(self) -> FleetExecutorPoolSnapshot:
        return ExecutorOperatorControls(self._database, self._configured()).snapshot()

    def _operator_mutation_controls(self) -> ExecutorOperatorControls:
        """Never publish executor generations before an admin mutation's CAS."""
        return ExecutorOperatorControls(
            self._database, self._configured(), defer_operator_generation_publication=True
        )

    def drain_executor(
        self, executor_id: str, *, expected_generation: int, expected_revision: int
    ) -> FleetExecutorStatus:
        return self._operator_mutation_controls().drain(
            executor_id,
            expected_generation=expected_generation,
            expected_revision=expected_revision,
        )

    def activate_executor(
        self, executor_id: str, *, expected_generation: int, expected_revision: int
    ) -> FleetExecutorStatus:
        return self._operator_mutation_controls().activate(
            executor_id,
            expected_generation=expected_generation,
            expected_revision=expected_revision,
        )

    def revoke_executor_trust(
        self, executor_id: str, *, expected_generation: int, expected_revision: int
    ) -> FleetExecutorStatus:
        return self._operator_mutation_controls().revoke_trust(
            executor_id,
            expected_generation=expected_generation,
            expected_revision=expected_revision,
        )

    def restore_executor_trust(
        self, executor_id: str, *, expected_generation: int, expected_revision: int
    ) -> FleetExecutorStatus:
        return self._operator_mutation_controls().restore_trust(
            executor_id,
            expected_generation=expected_generation,
            expected_revision=expected_revision,
        )

    def original_local_lace_recovery_backend(
        self,
        placement: FleetExecutorPlacement,
    ) -> ProcessIsolationBackend:
        """Read the exact configured local backend; never mint recovery authority.

        The original Fleet session and placement lease remain independently
        checked inside their SQLite owner. This method supplies only immutable
        deployment observer configuration, never an arbitrary caller path.
        """
        configured = self._configured()
        candidate = _exact_candidate(configured.executors, placement.executor_capacity)
        if (
            candidate is None
            or isolation_backend_evidence(candidate.backend) != placement.isolation_backend
        ):
            raise ExecutorPlacementLost(
                "Exact admitted local-LACE observer backend is no longer configured"
            )
        return candidate.backend

    def require_bound(
        self,
        placement: FleetExecutorPlacement,
        plan: ResolvedFleetPlan,
    ) -> ProcessIsolationBackend:
        """Resolve only the exact admitted placement; drain never relocates or invalidates it."""
        with self._database.transaction() as connection:
            return self.require_bound_in(connection, placement, plan)

    def require_bound_in(
        self,
        connection: sqlite3.Connection,
        placement: FleetExecutorPlacement,
        plan: ResolvedFleetPlan,
    ) -> ProcessIsolationBackend:
        """Revalidate one exact placement inside a caller-owned durable read cut."""
        configuration = self._configured()
        evidence = placement.executor_capacity
        backend = placement.isolation_backend
        candidate = _exact_candidate(configuration.executors, evidence)
        if candidate is None:
            raise ExecutorPlacementLost(
                "The exact Fleet executor admitted before RUN_ADMITTED is no longer configured; "
                "automatic relocation is forbidden"
            )
        reservations = FleetExecutorCapacityReservations(self._database)
        _validate_exact_binding_candidate(
            candidate, evidence, backend, plan, reservations=reservations
        )
        try:
            ExecutorOperatorControls.require_current_generation_in(
                connection,
                evidence.executor_id,
                evidence.executor_generation,
            )
        except StateConflict as exc:
            raise ExecutorGenerationSuperseded(
                "The exact Fleet executor generation admitted before RUN_ADMITTED is no longer "
                "current; automatic relocation is forbidden"
            ) from exc
        operational = ExecutorOperatorControls.state_in(connection, candidate.identity.executor_id)
        if operational.trust_state is ExecutorTrustState.REVOKED:
            raise ExecutorTrustRevoked(
                "Trust for the exact Fleet executor admitted before RUN_ADMITTED was revoked; "
                "automatic relocation is forbidden"
            )
        if operational.trust_epoch != placement.executor_trust_epoch:
            raise ExecutorTrustRevoked(
                "Trust authority for the exact Fleet executor admitted before RUN_ADMITTED "
                "changed after admission; restoring trust authorizes new work only"
            )
        try:
            reservations.require_bound_in(connection, evidence)
        except FleetPlanInvalid as exc:
            raise ExecutorPlacementLost(
                "The exact Fleet executor admitted before RUN_ADMITTED lost its "
                "capacity reservation; automatic relocation is forbidden"
            ) from exc
        return candidate.backend

    @staticmethod
    def reservation_matches_in(
        connection: sqlite3.Connection, placement: FleetExecutorPlacement
    ) -> bool:
        """Return whether the exact admitted capacity reservation still exists in this cut."""
        try:
            FleetExecutorCapacityReservations.require_bound_in(
                connection, placement.executor_capacity
            )
        except FleetPlanInvalid:
            return False
        return True

    def release_in(
        self,
        connection: sqlite3.Connection,
        placement: FleetExecutorPlacement,
    ) -> None:
        FleetExecutorCapacityReservations(self._database).release_in(
            connection, placement.executor_capacity
        )

    @staticmethod
    def release_run_in(connection: sqlite3.Connection, fleet_run_id: str) -> None:
        FleetExecutorCapacityReservations.release_run_in(connection, fleet_run_id)


def _validate_exact_binding_candidate(
    candidate: ConfiguredFleetExecutor,
    evidence: PlanExecutorCapacityEvidence,
    backend: PlanIsolationBackendEvidence,
    plan: ResolvedFleetPlan,
    *,
    reservations: FleetExecutorCapacityReservations,
) -> None:
    observed_backend = isolation_backend_evidence(candidate.backend)
    if observed_backend != backend:
        raise ExecutorPlacementLost(
            "Fleet executor backend configuration changed after RUN_ADMITTED"
        )
    try:
        candidate.backend.require_available(plan)
        reservations.validate_bound_candidate(candidate.capacity, evidence, backend)
    except FleetPlanInvalid as exc:
        raise ExecutorPlacementLost(
            "The exact Fleet executor admitted before RUN_ADMITTED can no longer prove its "
            "backend/capacity binding; automatic relocation is forbidden"
        ) from exc


def _exact_candidate(
    executors: tuple[ConfiguredFleetExecutor, ...],
    evidence: PlanExecutorCapacityEvidence,
) -> ConfiguredFleetExecutor | None:
    for candidate in executors:
        if candidate.identity.executor_id != evidence.executor_id:
            continue
        if candidate.identity.generation != evidence.executor_generation:
            continue
        return candidate
    return None
