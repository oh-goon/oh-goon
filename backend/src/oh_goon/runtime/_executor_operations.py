from __future__ import annotations

import sqlite3

from oh_goon.core.errors import ExecutorNotFound, StateConflict
from oh_goon.core.models.fleet_executor_operations import (
    ExecutorAdmissionMode,
    ExecutorTrustState,
    FleetExecutorOperationalState,
    FleetExecutorPoolSnapshot,
    FleetExecutorStatus,
)
from oh_goon.runtime._executor_configuration import (
    ConfiguredFleetExecutor,
    ExecutorPoolConfiguration,
)
from oh_goon.runtime.database import LocalDatabase
from oh_goon.runtime.executor_capacity import FleetExecutorCapacityReservations


class ExecutorOperatorControls:
    """Private SQLite-backed operator controls beneath ``FleetExecutorPool``.

    Drain/trust state is coordination authority, not product history, so its writes do
    not advance the product durable revision. Final placement re-reads the state in the
    same SQLite transaction that commits capacity, lease, and ``RUN_ADMITTED``.
    """

    def __init__(
        self,
        database: LocalDatabase,
        configuration: ExecutorPoolConfiguration,
        *,
        defer_operator_generation_publication: bool = False,
    ) -> None:
        self._database = database
        self._configuration = configuration
        self._capacity = FleetExecutorCapacityReservations(database)
        self._defer_operator_generation_publication = defer_operator_generation_publication
        if not defer_operator_generation_publication:
            self._register_configured_generations()

    def _register_configured_generations(self) -> None:
        """Publish monotonic executor generations as placement-fencing authority."""
        with self._database.transaction(write=True, track_revision=False) as connection:
            for candidate in self._configuration.executors:
                self._register_generation_in(connection, candidate)

    @staticmethod
    def _register_generation_in(
        connection: sqlite3.Connection, candidate: ConfiguredFleetExecutor
    ) -> None:
        """The one monotonic generation publisher, shared by placement and admin CAS."""
        identity = candidate.identity
        row = connection.execute(
            "SELECT generation FROM executor_configured_generation WHERE executor_id = ?",
            (identity.executor_id,),
        ).fetchone()
        if row is not None and int(row["generation"]) > identity.generation:
            raise StateConflict(f"Fleet executor generation regressed: {identity.executor_id}")
        connection.execute(
            """
            INSERT INTO executor_configured_generation(executor_id, generation)
            VALUES (?, ?)
            ON CONFLICT(executor_id) DO UPDATE SET generation = excluded.generation
            WHERE excluded.generation > executor_configured_generation.generation
            """,
            (identity.executor_id, identity.generation),
        )

    @staticmethod
    def require_current_generation_in(
        connection: sqlite3.Connection, executor_id: str, expected_generation: int
    ) -> None:
        row = connection.execute(
            "SELECT generation FROM executor_configured_generation WHERE executor_id = ?",
            (executor_id,),
        ).fetchone()
        if row is None or int(row["generation"]) != expected_generation:
            raise StateConflict(f"Fleet executor generation changed: {executor_id}")

    def snapshot(self) -> FleetExecutorPoolSnapshot:
        with self._database.transaction() as connection:
            return FleetExecutorPoolSnapshot(
                executors=tuple(
                    self._status_in(
                        connection,
                        candidate,
                        self.state_in(connection, candidate.identity.executor_id),
                    )
                    for candidate in self._configuration.executors
                )
            )

    def state(self, executor_id: str) -> FleetExecutorOperationalState:
        with self._database.transaction() as connection:
            return self.state_in(connection, executor_id)

    @staticmethod
    def state_in(connection: sqlite3.Connection, executor_id: str) -> FleetExecutorOperationalState:
        row = connection.execute(
            """
            SELECT admission_mode, trust_state, revision, trust_epoch
            FROM executor_operational_state
            WHERE executor_id = ?
            """,
            (executor_id,),
        ).fetchone()
        if row is None:
            return FleetExecutorOperationalState(executor_id=executor_id)
        return FleetExecutorOperationalState(
            executor_id=executor_id,
            admission_mode=ExecutorAdmissionMode(str(row["admission_mode"])),
            trust_state=ExecutorTrustState(str(row["trust_state"])),
            revision=int(row["revision"]),
            trust_epoch=int(row["trust_epoch"]),
        )

    def drain(
        self, executor_id: str, *, expected_generation: int, expected_revision: int
    ) -> FleetExecutorStatus:
        return self._set_status(
            executor_id,
            expected_generation=expected_generation,
            expected_revision=expected_revision,
            admission_mode=ExecutorAdmissionMode.DRAINING,
        )

    def activate(
        self, executor_id: str, *, expected_generation: int, expected_revision: int
    ) -> FleetExecutorStatus:
        return self._set_status(
            executor_id,
            expected_generation=expected_generation,
            expected_revision=expected_revision,
            admission_mode=ExecutorAdmissionMode.ACTIVE,
        )

    def revoke_trust(
        self, executor_id: str, *, expected_generation: int, expected_revision: int
    ) -> FleetExecutorStatus:
        return self._set_status(
            executor_id,
            expected_generation=expected_generation,
            expected_revision=expected_revision,
            trust_state=ExecutorTrustState.REVOKED,
        )

    def restore_trust(
        self, executor_id: str, *, expected_generation: int, expected_revision: int
    ) -> FleetExecutorStatus:
        return self._set_status(
            executor_id,
            expected_generation=expected_generation,
            expected_revision=expected_revision,
            trust_state=ExecutorTrustState.TRUSTED,
        )

    def _set_status(
        self,
        executor_id: str,
        *,
        expected_generation: int,
        expected_revision: int,
        admission_mode: ExecutorAdmissionMode | None = None,
        trust_state: ExecutorTrustState | None = None,
    ) -> FleetExecutorStatus:
        candidate = self._candidate(executor_id)
        if candidate.identity.generation != expected_generation:
            raise StateConflict(f"Fleet executor generation changed: {executor_id}")
        intended: FleetExecutorOperationalState | None = None
        prepared = False
        try:
            with self._database.transaction(write=True, track_revision=False) as connection:
                current = self.state_in(connection, executor_id)
                if current.revision != expected_revision:
                    raise StateConflict(f"Fleet executor operational state is stale: {executor_id}")
                # An administrative denial must not publish an unrelated executor
                # generation. The original generation owner joins this same CAS
                # only after target, expected generation and revision are proven.
                if self._defer_operator_generation_publication:
                    self._register_generation_in(connection, candidate)
                else:
                    self.require_current_generation_in(connection, executor_id, expected_generation)
                intended = current.model_copy(
                    update={
                        "admission_mode": admission_mode or current.admission_mode,
                        "trust_state": trust_state or current.trust_state,
                    }
                )
                if intended != current:
                    trust_changed = intended.trust_state is not current.trust_state
                    intended = intended.model_copy(
                        update={
                            "revision": current.revision + 1,
                            "trust_epoch": current.trust_epoch + (1 if trust_changed else 0),
                        }
                    )
                    connection.execute(
                        """
                        INSERT INTO executor_operational_state(
                            executor_id, admission_mode, trust_state, revision, trust_epoch
                        ) VALUES (?, ?, ?, ?, ?)
                        ON CONFLICT(executor_id) DO UPDATE SET
                            admission_mode = excluded.admission_mode,
                            trust_state = excluded.trust_state,
                            revision = excluded.revision,
                            trust_epoch = excluded.trust_epoch
                        """,
                        (
                            executor_id,
                            intended.admission_mode.value,
                            intended.trust_state.value,
                            intended.revision,
                            intended.trust_epoch,
                        ),
                    )
                status = self._status_in(connection, candidate, intended)
                prepared = True
            return status
        except BaseException:
            if prepared and intended is not None:
                replay = self._replay_status_after_ambiguous_write(candidate, intended)
                if replay is not None:
                    return replay
            raise

    def _replay_status_after_ambiguous_write(
        self,
        candidate: ConfiguredFleetExecutor,
        intended: FleetExecutorOperationalState,
    ) -> FleetExecutorStatus | None:
        with self._database.transaction() as connection:
            current = self.state_in(connection, candidate.identity.executor_id)
            if current != intended:
                return None
            return self._status_in(connection, candidate, current)

    def _candidate(self, executor_id: str) -> ConfiguredFleetExecutor:
        candidate = next(
            (
                item
                for item in self._configuration.executors
                if item.identity.executor_id == executor_id
            ),
            None,
        )
        if candidate is None:
            raise ExecutorNotFound(f"Unknown Fleet executor: {executor_id}")
        return candidate

    def _status_in(
        self,
        connection: sqlite3.Connection,
        candidate: ConfiguredFleetExecutor,
        state: FleetExecutorOperationalState,
    ) -> FleetExecutorStatus:
        active = self._capacity.active_fleet_run_ids_in(connection, candidate.identity.executor_id)
        return FleetExecutorStatus(
            executor_id=candidate.identity.executor_id,
            generation=candidate.identity.generation,
            priority=candidate.priority,
            backend_id=candidate.backend.capabilities.backend_id,
            admission_mode=state.admission_mode,
            trust_state=state.trust_state,
            active_fleet_run_ids=active,
            operational_revision=state.revision,
        )
