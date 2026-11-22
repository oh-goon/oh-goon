from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable, Generator
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING, Literal, TypeVar, cast
from uuid import uuid4

from pydantic import JsonValue

from oh_goon.core.errors import ExecutionBusy, FleetPlanInvalid, OwnershipLost
from oh_goon.core.models.run_resources import RunConcurrencyAdmission, RunResourceEnvelopeEvidence
from oh_goon.core.product_identity import stable_id
from oh_goon.runtime.database import LocalDatabase
from oh_goon.runtime.durable_revision import require_durable_revision
from oh_goon.runtime.execution.lease_environment import LeaseEnvironment, SystemLeaseEnvironment

T = TypeVar("T")
ExecutionKind = Literal["DIRECT", "FLEET"]
ExecutionCustodyPhase = Literal[
    "DIRECT_OWNED", "ADMITTED_UNCLAIMED", "WORKER_OWNED", "RECOVERY_OWNED", "RESOURCE_RESERVED"
]

if TYPE_CHECKING:
    from oh_goon.integrations.run_resource_envelope import (
        VerifiedWorkerResourceClaim,
        VerifiedWorkerResourceClosure,
    )


@dataclass(frozen=True, slots=True)
class ExecutionCustody:
    """One exact process custodian, carried with the lease it is allowed to use."""

    phase: ExecutionCustodyPhase
    owner_session_id: str
    owner_pid: int
    owner_identity: str | None

    def __post_init__(self) -> None:
        if (
            type(self.phase) is not str
            or self.phase
            not in {
                "DIRECT_OWNED",
                "ADMITTED_UNCLAIMED",
                "WORKER_OWNED",
                "RECOVERY_OWNED",
                "RESOURCE_RESERVED",
            }
            or type(self.owner_session_id) is not str
            or not self.owner_session_id
            or type(self.owner_pid) is not int
            or self.owner_pid < 1
            or self.owner_identity is not None
            and (type(self.owner_identity) is not str or not self.owner_identity)
            or self.phase in {"WORKER_OWNED", "RECOVERY_OWNED", "RESOURCE_RESERVED"}
            and self.owner_identity is None
            or (self.phase == "RECOVERY_OWNED") != self.owner_session_id.startswith("recovery:")
        ):
            raise ValueError("invalid execution custody")


@dataclass(frozen=True, slots=True)
class ExecutionLease:
    token: str = field(repr=False)
    kind: ExecutionKind
    fleet_run_id: str | None
    custody: ExecutionCustody | None = None
    concurrency_identity: str | None = None
    resource_envelope_identity: str | None = None

    def __post_init__(self) -> None:
        if (
            type(self.token) is not str
            or not self.token
            or type(self.kind) is not str
            or self.kind not in {"DIRECT", "FLEET"}
            or (self.kind == "DIRECT") != (self.fleet_run_id is None)
            or self.fleet_run_id is not None
            and (type(self.fleet_run_id) is not str or not self.fleet_run_id)
            or self.custody is not None
            and (self.kind == "DIRECT") != (self.custody.phase == "DIRECT_OWNED")
            or self.concurrency_identity is not None
            and (self.kind != "FLEET" or not self.concurrency_identity)
            or self.resource_envelope_identity is not None
            and (self.concurrency_identity is None or not self.resource_envelope_identity)
        ):
            raise ValueError("invalid execution lease custody")


@dataclass(frozen=True, slots=True)
class WorkerResourceReservation:
    """Original capacity and native scope facts retained past logical completion."""

    lease: ExecutionLease
    admission: RunConcurrencyAdmission
    envelope: RunResourceEnvelopeEvidence | None
    controller: ExecutionCustody
    deferred_resource_scope: dict[str, JsonValue] | None = field(default=None, repr=False)


def _new_lease(kind: ExecutionKind, fleet_run_id: str | None) -> ExecutionLease:
    return ExecutionLease(token=f"lease-{uuid4().hex}", kind=kind, fleet_run_id=fleet_run_id)


class SQLiteExecutionLeaseStore:
    """SQLite-backed cross-process execution authority.

    A live process owner is never expired because wall-clock time elapsed.  Dead-owner
    reclamation is deliberately conservative: only a PID proven not alive may be
    removed automatically.  PID reuse can therefore fail closed, but it cannot create
    two external-effect owners.
    """

    def __init__(
        self,
        database: LocalDatabase,
        *,
        environment: LeaseEnvironment | None = None,
    ) -> None:
        self._database = database
        self._environment = environment or SystemLeaseEnvironment()

    @contextmanager
    def execution_connection_scope(self) -> Generator[None, None, None]:
        """Reuse one admitted native SQLite handle in the current execution thread."""
        with self._database.execution_connection_scope():
            yield

    def acquire_direct_with(
        self,
        *,
        expected_durable_revision: int,
        mutate: Callable[[sqlite3.Connection], T],
    ) -> tuple[ExecutionLease, T]:
        lease = _new_lease("DIRECT", None)
        with self._database.transaction(write=True) as connection:
            require_durable_revision(connection, expected_durable_revision)
            lease = self._insert_lease(connection, lease)
            result = mutate(connection)
        return lease, result

    def acquire_fleet_with(
        self,
        fleet_run_id: str,
        *,
        expected_durable_revision: int,
        mutate: Callable[[sqlite3.Connection], T],
        concurrency: RunConcurrencyAdmission | None = None,
        deferred_worker_resource_scope: dict[str, JsonValue] | None = None,
    ) -> tuple[ExecutionLease, T]:
        lease = _new_lease("FLEET", fleet_run_id)
        with self._database.transaction(write=True) as connection:
            require_durable_revision(connection, expected_durable_revision)
            lease = self._insert_lease(
                connection,
                lease,
                concurrency=concurrency,
                deferred_worker_resource_scope=deferred_worker_resource_scope,
            )
            result = mutate(connection)
        return lease, result

    def acquire_fleet_in(
        self,
        connection: sqlite3.Connection,
        fleet_run_id: str,
        *,
        expected_durable_revision: int,
        concurrency: RunConcurrencyAdmission | None = None,
        deferred_worker_resource_scope: dict[str, JsonValue] | None = None,
    ) -> ExecutionLease:
        """Create the Fleet lease inside a caller-owned admission transaction."""
        require_durable_revision(connection, expected_durable_revision)
        lease = _new_lease("FLEET", fleet_run_id)
        return self._insert_lease(
            connection,
            lease,
            concurrency=concurrency,
            deferred_worker_resource_scope=deferred_worker_resource_scope,
        )

    def claim_fleet_worker(
        self,
        lease: ExecutionLease,
        require_admitted: Callable[[sqlite3.Connection], None],
        *,
        resource_envelope: RunResourceEnvelopeEvidence | None = None,
        resource_claim: VerifiedWorkerResourceClaim | None = None,
    ) -> ExecutionLease:
        """Consume one admission capability in the original SQLite writer cut.

        The stable token remains the signed admission identity. Replacing its exact
        custodian and phase revokes the predecessor's mutation authority instead.
        """
        expected = lease.custody
        if (
            lease.kind != "FLEET"
            or lease.fleet_run_id is None
            or expected is None
            or expected.phase != "ADMITTED_UNCLAIMED"
            or expected.owner_identity is None
        ):
            raise OwnershipLost("exact unclaimed Fleet admission required")
        pid = self._environment.process_id()
        if type(pid) is not int or pid < 1:
            raise OwnershipLost("worker process identity is unproven")
        birth = self._environment.process_identity(pid)
        if (
            pid == expected.owner_pid
            or self._database.session_id == expected.owner_session_id
            or type(birth) is not str
            or not birth
            or self._environment.process_alive(pid) is not True
        ):
            raise OwnershipLost("fresh worker process and database session required")
        custody = ExecutionCustody("WORKER_OWNED", self._database.session_id, pid, birth)
        with self._database.transaction(write=True) as connection:
            self._database.require_atomic_write_cut(connection)
            row = _lease_row_in(connection, lease.token)
            if (
                row is None
                or _lease_from_row(row) != lease
                or row["cancel_requested_at"] is not None
            ):
                raise OwnershipLost("Fleet admission changed before worker claim")
            admission = _concurrency_from_row(row)
            if admission is not None:
                self._require_worker_resource_envelope(admission, resource_envelope, pid, birth)
                from oh_goon.integrations.run_resource_envelope import VerifiedWorkerResourceClaim

                if type(resource_claim) is not VerifiedWorkerResourceClaim:
                    raise OwnershipLost("Native worker resource claim has no original verifier")
                assert resource_envelope is not None
                resource_claim.require_matches(admission, resource_envelope)
            elif resource_envelope is not None and resource_envelope.subject == "worker":
                raise OwnershipLost("Worker resource scope has no concurrent admission")
            require_admitted(connection)
            # Reobserve native claimant identity immediately before the durable CAS.
            if (
                self._environment.process_id() != pid
                or self._environment.process_identity(pid) != birth
                or self._environment.process_alive(pid) is not True
            ):
                raise OwnershipLost("worker process identity changed before claim")
            cursor = connection.execute(
                """UPDATE execution_lease
                   SET custody_phase='WORKER_OWNED', owner_session_id=?, owner_pid=?,
                       owner_identity=?, worker_resource_envelope_json=?
                   WHERE lease_token=? AND kind='FLEET'
                     AND fleet_run_id=? AND custody_phase='ADMITTED_UNCLAIMED'
                     AND owner_session_id=? AND owner_pid=? AND owner_identity=?
                     AND cancel_requested_at IS NULL""",
                (
                    custody.owner_session_id,
                    custody.owner_pid,
                    custody.owner_identity,
                    resource_envelope.model_dump_json(exclude_computed_fields=True)
                    if admission is not None and resource_envelope is not None
                    else None,
                    lease.token,
                    lease.fleet_run_id,
                    expected.owner_session_id,
                    expected.owner_pid,
                    expected.owner_identity,
                ),
            )
            if cursor.rowcount != 1:
                raise OwnershipLost("Fleet admission was already claimed")
        return replace(
            lease,
            custody=custody,
            resource_envelope_identity=_resource_envelope_identity(resource_envelope)
            if admission is not None and resource_envelope is not None
            else None,
        )

    def observe_as_owner(
        self, lease: ExecutionLease, observe: Callable[[sqlite3.Connection], T]
    ) -> T:
        """Read one authority cut only while this exact lease is current."""
        with self._database.transaction() as connection:
            self.assert_owner_in(connection, lease)
            return observe(connection)

    def mutate_as_owner(
        self,
        lease: ExecutionLease,
        mutate: Callable[[sqlite3.Connection], T],
    ) -> T:
        """Run one ownership-critical durable mutation in the ownership-check transaction."""
        with self._database.transaction(write=True) as connection:
            self._assert_executable_owner_in(connection, lease)
            return mutate(connection)

    def mutate_recovery_as_owner(
        self,
        lease: ExecutionLease,
        mutate: Callable[[sqlite3.Connection], T],
    ) -> T:
        """One original SQLite recovery write cut, never an execution mutation."""
        with self._database.transaction(write=True) as connection:
            self.require_recovery_owner_in(connection, lease)
            return mutate(connection)

    def assert_recovery_owner(self, lease: ExecutionLease) -> None:
        with self._database.transaction() as connection:
            self.require_recovery_owner_in(connection, lease)

    def finish_as_owner(
        self,
        lease: ExecutionLease,
        mutate: Callable[[sqlite3.Connection], T],
    ) -> T:
        """Persist terminal lifecycle state and release ownership in one transaction."""
        with self._database.transaction(write=True) as connection:
            self._assert_executable_owner_in(connection, lease)
            result = mutate(connection)
            self._settle_reservation_in(connection, lease)
            return result

    def assert_submission_owner(self, lease: ExecutionLease) -> None:
        with self._database.transaction() as connection:
            self._require_fleet_custody_in(connection, lease, "ADMITTED_UNCLAIMED")

    def finish_submission_as_owner(
        self,
        lease: ExecutionLease,
        mutate: Callable[[sqlite3.Connection], T],
    ) -> T:
        """Settle submission only while the original admission remains unclaimed."""
        with self._database.transaction(write=True) as connection:
            self._require_fleet_custody_in(connection, lease, "ADMITTED_UNCLAIMED")
            result = mutate(connection)
            self._settle_reservation_in(connection, lease)
            return result

    def active_lease(self) -> ExecutionLease | None:
        with self._database.transaction() as connection:
            return self.active_lease_in(connection)

    def active_lease_in(self, connection: sqlite3.Connection) -> ExecutionLease | None:
        """Read only an unambiguous live owner; callers may select a Run explicitly."""
        leases = self.active_leases_in(connection)
        if len(leases) > 1:
            raise ExecutionBusy("Multiple Fleet owners require an explicit Run identity")
        return leases[0] if leases else None

    def active_leases_in(self, connection: sqlite3.Connection) -> tuple[ExecutionLease, ...]:
        return tuple(
            _lease_from_row(row)
            for row in self._retained_rows(connection)
            if row["custody_phase"] != "RESOURCE_RESERVED" and self._owner_is_live(row)
        )

    def retained_leases_in(self, connection: sqlite3.Connection) -> tuple[ExecutionLease, ...]:
        return tuple(_lease_from_row(row) for row in self._retained_rows(connection))

    def active_fleet_lease_in(
        self, connection: sqlite3.Connection, fleet_run_id: str
    ) -> ExecutionLease | None:
        row = self._active_lease_row(connection, remove_dead=False, fleet_run_id=fleet_run_id)
        return _lease_from_row(row) if row is not None else None

    def retained_fleet_lease_in(
        self, connection: sqlite3.Connection, fleet_run_id: str
    ) -> ExecutionLease | None:
        row = connection.execute(
            "SELECT * FROM execution_lease WHERE kind='FLEET' AND fleet_run_id=?",
            (fleet_run_id,),
        ).fetchone()
        return _lease_from_row(row) if row is not None else None

    def concurrency_in(
        self, connection: sqlite3.Connection, fleet_run_id: str
    ) -> RunConcurrencyAdmission | None:
        row = connection.execute(
            "SELECT * FROM execution_lease WHERE kind='FLEET' AND fleet_run_id=?", (fleet_run_id,)
        ).fetchone()
        return _concurrency_from_row(row) if row is not None else None

    def worker_resource_reservation_in(
        self, connection: sqlite3.Connection, fleet_run_id: str
    ) -> WorkerResourceReservation | None:
        row = connection.execute(
            "SELECT * FROM execution_lease WHERE kind='FLEET' AND fleet_run_id=?", (fleet_run_id,)
        ).fetchone()
        if row is None:
            return None
        admission = _concurrency_from_row(row)
        if admission is None:
            return None
        envelope = (
            RunResourceEnvelopeEvidence.model_validate_json(row["worker_resource_envelope_json"])
            if row["worker_resource_envelope_json"] is not None
            else None
        )
        controller = ExecutionCustody(
            "ADMITTED_UNCLAIMED",
            row["controller_session_id"],
            row["controller_pid"],
            row["controller_identity"],
        )
        return WorkerResourceReservation(
            _lease_from_row(row),
            admission,
            envelope,
            controller,
            _deferred_from_row(row, admission),
        )

    @staticmethod
    def _retained_rows(connection: sqlite3.Connection) -> tuple[sqlite3.Row, ...]:
        return tuple(
            connection.execute("SELECT * FROM execution_lease ORDER BY singleton").fetchall()
        )

    def _owner_is_live(self, row: sqlite3.Row) -> bool:
        _lease_from_row(row)
        pid = int(row["owner_pid"])
        if self._environment.process_alive(pid) is False:
            return False
        expected = row["owner_identity"]
        observed = self._environment.process_identity(pid)
        return expected is None or observed is None or expected == observed

    @staticmethod
    def _require_worker_resource_envelope(
        admission: RunConcurrencyAdmission,
        envelope: RunResourceEnvelopeEvidence | None,
        pid: int,
        birth: str,
    ) -> None:
        if (
            envelope is None
            or envelope.subject != "worker"
            or envelope.fleet_run_id != admission.fleet_run_id
            or envelope.unit_name != admission.worker_unit_name
            or envelope.resources != admission.policy.worker_resources
            or envelope.process_birth.pid != pid
            or f"{envelope.process_birth.provider}:{envelope.process_birth.boot_id}:{envelope.process_birth.start_ticks}"
            != birth
        ):
            raise OwnershipLost("Claim lacks its exact observed native worker resource scope")

    def _settle_reservation_in(self, connection: sqlite3.Connection, lease: ExecutionLease) -> None:
        row = _lease_row_in(connection, lease.token)
        if row is None or _lease_from_row(row) != lease:
            raise OwnershipLost("Execution ownership changed before terminal settlement")
        if _concurrency_from_row(row) is not None:
            cursor = connection.execute(
                "UPDATE execution_lease SET custody_phase='RESOURCE_RESERVED' WHERE lease_token=? AND custody_phase=?",
                (lease.token, lease.custody.phase if lease.custody else "DIRECT_OWNED"),
            )
        else:
            cursor = connection.execute(
                "DELETE FROM execution_lease WHERE lease_token=?", (lease.token,)
            )
        if cursor.rowcount != 1:
            raise OwnershipLost("Execution reservation changed before terminal settlement")

    def release_worker_reservation_with(
        self,
        lease: ExecutionLease,
        proof: VerifiedWorkerResourceClosure,
        mutate: Callable[[sqlite3.Connection, WorkerResourceReservation | None], T],
    ) -> T:
        """Original controller joins native closure, canonical receipt and slot release."""
        from oh_goon.integrations.run_resource_envelope import VerifiedWorkerResourceClosure

        custody = lease.custody
        if (
            type(proof) is not VerifiedWorkerResourceClosure
            or lease.kind != "FLEET"
            or lease.fleet_run_id is None
            or lease.concurrency_identity != proof.admission.identity
            or custody is None
            or custody.phase != "ADMITTED_UNCLAIMED"
            or custody.owner_session_id != self._database.session_id
            or custody.owner_pid != self._environment.process_id()
            or custody.owner_identity != self._environment.process_identity(custody.owner_pid)
            or self._environment.process_alive(custody.owner_pid) is not True
        ):
            raise OwnershipLost("Original concurrent controller capability required for closure")
        with self._database.transaction(write=True) as connection:
            self._database.require_atomic_write_cut(connection)
            reservation = self.worker_resource_reservation_in(connection, lease.fleet_run_id)
            if reservation is None:
                proof.require_matches(proof.admission, proof.envelope)
                # Only a matching already-canonical observation can replay a lost ACK.
                return mutate(connection, None)
            if (
                reservation.lease.token != lease.token
                or reservation.lease.concurrency_identity != lease.concurrency_identity
                or reservation.controller != custody
                or reservation.admission != proof.admission
                or reservation.lease.custody is None
                or reservation.lease.custody.phase != "RESOURCE_RESERVED"
            ):
                raise OwnershipLost("Worker closure differs from its retained original reservation")
            envelope = reservation.envelope or proof.envelope
            proof.require_matches(reservation.admission, envelope)
            if reservation.envelope is None and envelope is not None:
                # An observed worker may exit before winning its DB claim. The
                # original opaque closure proof can publish that exact observation;
                # it never grants executable custody to an unclaimed process.
                cursor = connection.execute(
                    "UPDATE execution_lease SET worker_resource_envelope_json=? "
                    "WHERE lease_token=? AND custody_phase='RESOURCE_RESERVED' "
                    "AND worker_resource_envelope_json IS NULL",
                    (envelope.model_dump_json(exclude_computed_fields=True), lease.token),
                )
                if cursor.rowcount != 1:
                    raise OwnershipLost("Unclaimed worker resource observation changed")
                reservation = self.worker_resource_reservation_in(connection, lease.fleet_run_id)
                assert reservation is not None
            result = mutate(connection, reservation)
            self._delete_worker_reservation_in(connection, reservation)
            return result

    def release_recovered_worker_reservation_with(
        self,
        fleet_run_id: str,
        proof: VerifiedWorkerResourceClosure,
        mutate: Callable[[sqlite3.Connection, WorkerResourceReservation | None], T],
    ) -> T:
        """A new runtime may close only the positively dead controller's exact scope."""
        from oh_goon.integrations.run_resource_envelope import VerifiedWorkerResourceClosure

        if (
            type(proof) is not VerifiedWorkerResourceClosure
            or proof.admission.fleet_run_id != fleet_run_id
        ):
            raise OwnershipLost("Original native worker closure proof required for reconciliation")
        with self._database.transaction(write=True) as connection:
            self._database.require_atomic_write_cut(connection)
            reservation = self.worker_resource_reservation_in(connection, fleet_run_id)
            if reservation is None:
                proof.require_matches(proof.admission, proof.envelope)
                return mutate(connection, None)
            custody = reservation.lease.custody
            if (
                custody is None
                or custody.phase != "RESOURCE_RESERVED"
                or reservation.envelope is None
                or not self._process_generation_gone(reservation.controller)
                or not self._process_generation_gone(custody)
            ):
                raise ExecutionBusy(
                    "Original controller/worker death and closing custody are required"
                )
            proof.require_matches(reservation.admission, reservation.envelope)
            result = mutate(connection, reservation)
            self._delete_worker_reservation_in(connection, reservation)
            return result

    def _process_generation_gone(self, custody: ExecutionCustody) -> bool:
        alive = self._environment.process_alive(custody.owner_pid)
        if alive is False:
            return True
        observed = self._environment.process_identity(custody.owner_pid)
        # This grants no signal or execution authority over the replacement PID.
        # Native scope closure remains independently required before release.
        return (
            custody.owner_identity is not None
            and observed is not None
            and observed != custody.owner_identity
        )

    @staticmethod
    def _delete_worker_reservation_in(
        connection: sqlite3.Connection, reservation: WorkerResourceReservation
    ) -> None:
        custody = reservation.lease.custody
        assert custody is not None
        cursor = connection.execute(
            "DELETE FROM execution_lease WHERE lease_token=? AND fleet_run_id=? "
            "AND custody_phase='RESOURCE_RESERVED' AND concurrency_json=? "
            "AND worker_resource_envelope_json IS ? AND controller_session_id=? "
            "AND controller_pid=? AND controller_identity=? AND owner_session_id=? "
            "AND owner_pid=? AND owner_identity IS ?",
            (
                reservation.lease.token,
                reservation.lease.fleet_run_id,
                reservation.admission.model_dump_json(exclude_computed_fields=True),
                reservation.envelope.model_dump_json(exclude_computed_fields=True)
                if reservation.envelope
                else None,
                reservation.controller.owner_session_id,
                reservation.controller.owner_pid,
                reservation.controller.owner_identity,
                custody.owner_session_id,
                custody.owner_pid,
                custody.owner_identity,
            ),
        )
        if cursor.rowcount != 1:
            raise OwnershipLost("Worker reservation changed before exact closure release")

    def reserve_dead_fleet_resources_in(
        self, connection: sqlite3.Connection, fleet_run_id: str
    ) -> None:
        """Reconciliation revokes executable custody but retains native occupancy."""
        self._database.require_atomic_write_cut(connection)
        reservation = self.worker_resource_reservation_in(connection, fleet_run_id)
        if reservation is None:
            return
        custody = reservation.lease.custody
        if custody is None:
            raise OwnershipLost("Concurrent resource predecessor lacks custody")
        if custody.phase == "RESOURCE_RESERVED":
            return
        if self._environment.process_alive(custody.owner_pid) is not False:
            raise ExecutionBusy("Original concurrent worker death is not proven")
        cursor = connection.execute(
            "UPDATE execution_lease SET custody_phase='RESOURCE_RESERVED' "
            "WHERE lease_token=? AND fleet_run_id=? AND custody_phase=?",
            (reservation.lease.token, fleet_run_id, custody.phase),
        )
        if cursor.rowcount != 1:
            raise OwnershipLost("Concurrent recovery predecessor changed")

    def assert_fleet_idle_in(self, connection: sqlite3.Connection, fleet_run_id: str) -> None:
        """Scoped recovery cannot be blocked or authorized by a sibling's lease."""
        row = self._active_lease_row(connection, remove_dead=True, fleet_run_id=fleet_run_id)
        if row is not None:
            raise ExecutionBusy("Original Fleet worker is still live")

    def owned_fleet_lease_in(
        self, connection: sqlite3.Connection, fleet_run_id: str
    ) -> ExecutionLease | None:
        """Recover only this runtime session's exact admitted Fleet owner."""
        pid = self._environment.process_id()
        lease = _owned_fleet_lease_in(
            connection,
            fleet_run_id,
            self._database.session_id,
            pid,
            self._environment.process_identity(pid),
        )
        return lease

    def transfer_dead_fleet_to_recovery_in(
        self,
        connection: sqlite3.Connection,
        *,
        run_id: str,
        predecessor_token: str,
        predecessor_epoch: int,
    ) -> tuple[ExecutionLease, dict[str, str | int]]:
        """Original scheduler's one-row CAS; caller owns BEGIN IMMEDIATE and journal cut.

        This cannot run through _active_lease_row(remove_dead=True): automatic
        reclamation would erase the predecessor necessary for signed reconciliation.
        The new owner is marked recovery-only, never a Fleet execution admission.
        """
        if (
            not connection.in_transaction
            or type(run_id) is not str
            or not run_id
            or type(predecessor_token) is not str
            or not predecessor_token
            or type(predecessor_epoch) is not int
            or predecessor_epoch < 1
        ):
            raise OwnershipLost("exact predecessor custody and writer transaction required")
        try:
            paths = connection.execute("PRAGMA database_list").fetchall()
            main = next(str(row[2]) for row in paths if row[1] == "main")
            if not main or not Path(main).samefile(self._database.path):
                raise OwnershipLost("recovery is not on the original SQLite authority")
            row = connection.execute(
                "SELECT * FROM execution_lease WHERE kind='FLEET' AND fleet_run_id=?", (run_id,)
            ).fetchone()
        except (OSError, sqlite3.Error, StopIteration) as exc:
            raise OwnershipLost("recovery original SQLite authority unavailable") from exc
        if (
            row is None
            or row["lease_token"] != predecessor_token
            or row["kind"] != "FLEET"
            or row["fleet_run_id"] != run_id
            or row["cancel_requested_at"] is not None
        ):
            raise OwnershipLost("predecessor lease missing, cancelled or already transferred")
        predecessor_custody = _lease_from_row(row).custody
        if predecessor_custody is None:
            raise OwnershipLost("predecessor custody is unproven")
        old_session, old_pid, old_birth = (
            row["owner_session_id"],
            row["owner_pid"],
            row["owner_identity"],
        )
        if (
            type(old_session) is not str
            or not old_session
            or type(old_pid) is not int
            or old_pid < 1
            or type(old_birth) is not str
            or not old_birth
            or old_session == self._database.session_id
            or old_session.startswith("recovery:")
            and old_session == f"recovery:{self._database.session_id}"
        ):
            raise OwnershipLost("unprovable or same-session predecessor")
        # Conservative positive proof. PID reuse and inability to observe liveness
        # are not evidence of terminated predecessor; reject either case.
        if self._environment.process_alive(old_pid) is not False:
            raise OwnershipLost("predecessor still live or termination unproven")
        pid = self._environment.process_id()
        birth = self._environment.process_identity(pid)
        if (
            type(pid) is not int
            or pid < 1
            or type(birth) is not str
            or not birth
            or self._environment.process_alive(pid) is not True
        ):
            raise OwnershipLost("replacement process birth/liveness not proven")
        successor = replace(
            _new_lease("FLEET", run_id),
            custody=ExecutionCustody(
                "RECOVERY_OWNED", f"recovery:{self._database.session_id}", pid, birth
            ),
        )
        now = self._environment.now().isoformat()
        updated = connection.execute(
            """UPDATE execution_lease
               SET lease_token=?, owner_session_id=?, owner_pid=?, owner_identity=?,
                   acquired_at=?, cancel_requested_at=NULL, custody_phase='RECOVERY_OWNED'
               WHERE lease_token=? AND fleet_run_id=?
                 AND cancel_requested_at IS NULL AND owner_session_id=?
                 AND owner_pid=? AND owner_identity=? AND custody_phase=?""",
            (
                successor.token,
                f"recovery:{self._database.session_id}",
                pid,
                birth,
                now,
                predecessor_token,
                run_id,
                old_session,
                old_pid,
                old_birth,
                predecessor_custody.phase,
            ),
        )
        if updated.rowcount != 1:
            raise OwnershipLost("concurrent Fleet recovery transfer won the CAS")
        return successor, {
            "predecessor_session": old_session,
            "predecessor_pid": old_pid,
            "predecessor_birth": old_birth,
            "successor_session": self._database.session_id,
            "successor_pid": pid,
            "successor_birth": birth,
            "predecessor_epoch": predecessor_epoch,
        }

    def require_recovery_owner_in(
        self,
        connection: sqlite3.Connection,
        lease: ExecutionLease,
    ) -> None:
        self._require_fleet_custody_in(connection, lease, "RECOVERY_OWNED")
        row = connection.execute(
            "SELECT cancel_requested_at FROM execution_lease WHERE lease_token=?", (lease.token,)
        ).fetchone()
        if row is None or row[0] is not None:
            raise OwnershipLost("recovery-only Fleet custody is cancelled")

    def request_fleet_cancel_in(
        self,
        connection: sqlite3.Connection,
        fleet_run_id: str,
    ) -> bool:
        """Mark one live Fleet owner cancelled inside the caller-owned transaction.

        Returns ``False`` when no live owner remains. Dead-owner row reclamation is
        performed in this same transaction so the caller can atomically resolve the
        orphaned FleetRun before committing.
        """
        row = self._active_lease_row(connection, remove_dead=True, fleet_run_id=fleet_run_id)
        if row is None:
            return False
        if str(row["kind"]) != "FLEET" or row["fleet_run_id"] != fleet_run_id:
            raise OwnershipLost("Fleet run no longer owns the active execution lease")
        cursor = connection.execute(
            """
            UPDATE execution_lease
            SET cancel_requested_at = ?
            WHERE lease_token = ?
            """,
            (self._environment.now().isoformat(), str(row["lease_token"])),
        )
        if cursor.rowcount != 1:
            raise OwnershipLost("Execution lease changed before cancellation request")
        return True

    def cancellation_requested(self, lease: ExecutionLease) -> bool:
        with self._database.transaction() as connection:
            return self.cancellation_requested_in(connection, lease)

    @contextmanager
    def preparation_cancellation_observer(
        self, lease: ExecutionLease
    ) -> Generator[Callable[[], bool], None, None]:
        """Observe cancellation through one query-only connection during preparation.

        The observer connection is not mutation authority. Every callback re-proves the
        admitted path/schema and exact execution lease before reading cancellation.
        Each callback owns a short read transaction, so no snapshot/SHARED lock is pinned
        between callbacks and an external cancellation writer remains visible.
        """

        # Keep exactly one read-only native handle for this bounded preparation scope.
        # Native certification evidence showed that repeatedly opening the same admitted
        # DELETE-journal database can itself become the dominant NOTADB observation
        # surface on some host/storage combinations.  The database owner already drops
        # unpinned pager pages before every admitted_observer_cut, and every callback
        # begins a fresh SQLite read transaction that re-proves schema, journal,
        # application identity and durable revision.  Reuse therefore removes reconnect
        # churn without retaining a SQLite snapshot or weakening cancellation visibility.
        with self._database.admitted_observer_connection() as connection:

            def observe_once() -> bool:
                with self._database.admitted_observer_cut(
                    connection, context="preparation cancellation observation"
                ):
                    return self.cancellation_requested_in(connection, lease)

            # Prove exact lease ownership before handing the callback to preparation.
            assert observe_once() is False
            yield observe_once

    def cancellation_requested_in(
        self, connection: sqlite3.Connection, lease: ExecutionLease
    ) -> bool:
        self.assert_owner_in(connection, lease)
        row = connection.execute(
            "SELECT cancel_requested_at FROM execution_lease WHERE lease_token=?", (lease.token,)
        ).fetchone()
        assert row is not None
        return row["cancel_requested_at"] is not None

    def assert_owner(self, lease: ExecutionLease) -> None:
        with self._database.transaction() as connection:
            self.assert_owner_in(connection, lease)

    def assert_owner_in(
        self,
        connection: sqlite3.Connection,
        lease: ExecutionLease,
    ) -> None:
        if lease.kind == "FLEET":
            custody = lease.custody
            if custody is None or custody.phase not in {"WORKER_OWNED", "RECOVERY_OWNED"}:
                raise OwnershipLost("claimed Fleet process custody required")
            self._require_fleet_custody_in(connection, lease, custody.phase)
            return
        row = connection.execute(
            "SELECT lease_token, kind, fleet_run_id, custody_phase"
            " FROM execution_lease WHERE lease_token=?",
            (lease.token,),
        ).fetchone()
        if row is None or str(row["lease_token"]) != lease.token:
            raise OwnershipLost("Execution lease was lost")
        if (
            str(row["kind"]) != lease.kind
            or row["fleet_run_id"] != lease.fleet_run_id
            or row["custody_phase"] != "DIRECT_OWNED"
        ):
            raise OwnershipLost("Execution lease identity changed")

    def _require_fleet_custody_in(
        self,
        connection: sqlite3.Connection,
        lease: ExecutionLease,
        phase: ExecutionCustodyPhase,
    ) -> None:
        custody = lease.custody
        if lease.kind != "FLEET" or custody is None or custody.phase != phase:
            raise OwnershipLost("exact Fleet custody required")
        row = _lease_row_in(connection, lease.token)
        pid = self._environment.process_id()
        birth = self._environment.process_identity(pid)
        session_id = (
            f"recovery:{self._database.session_id}"
            if phase == "RECOVERY_OWNED"
            else self._database.session_id
        )
        if (
            row is None
            or _lease_from_row(row) != lease
            or custody.owner_session_id != session_id
            or custody.owner_pid != pid
            or not birth
            or custody.owner_identity != birth
            or self._environment.process_alive(pid) is not True
        ):
            raise OwnershipLost("Fleet owner session/process custody changed")

    def _assert_executable_owner_in(
        self, connection: sqlite3.Connection, lease: ExecutionLease
    ) -> None:
        if lease.kind == "FLEET":
            if lease.custody is not None and lease.custody.phase == "RECOVERY_OWNED":
                raise OwnershipLost("recovery custody is not executable admission")
            self._require_fleet_custody_in(connection, lease, "WORKER_OWNED")
        else:
            self.assert_owner_in(connection, lease)

    def require_initial_fleet_control_in(
        self,
        connection: sqlite3.Connection,
        *,
        run_id: str,
        control_epoch: int,
        lease_identity: str,
    ) -> None:
        """Prove the actual SQLite execution owner at the Enforcer admission cut.

        Only initial epoch 1 is issued by the existing execution-lease owner.
        Later control transfer/renewal requires a separate authoritative protocol;
        inferring an epoch or expiry from wall-clock/PID alone is forbidden.
        This is a *read-only* proof on the already-admitted SQLite database.
        It is not a new lease store, admission selector or LACE binding.
        """
        if (
            type(run_id) is not str
            or not run_id
            or type(control_epoch) is not int
            or control_epoch != 1
            or type(lease_identity) is not str
            or not lease_identity
        ):
            raise OwnershipLost("Initial Fleet control requires exact run, epoch 1 and token")

        # An arbitrary caller-created SQLite connection must not replace the
        # durable owner with a lookalike execution_lease table (e.g. :memory:).
        try:
            paths = connection.execute("PRAGMA database_list").fetchall()
            main = next(str(row[2]) for row in paths if row[1] == "main")
            if not main or not Path(main).samefile(self._database.path):
                raise OwnershipLost("Control proof is not on the owner database")
            row = connection.execute(
                """SELECT lease_token, kind, fleet_run_id, owner_session_id,
                          owner_pid, owner_identity, cancel_requested_at, custody_phase
                   FROM execution_lease WHERE lease_token=? AND fleet_run_id=?""",
                (lease_identity, run_id),
            ).fetchone()
        except (OSError, sqlite3.Error, StopIteration) as exc:
            raise OwnershipLost("Durable Fleet control lease cannot be proven") from exc
        if (
            row is None
            or row[0] != lease_identity
            or row[1] != "FLEET"
            or row[2] != run_id
            or row[3] != self._database.session_id
            or row[6] is not None
            or row[7] != "ADMITTED_UNCLAIMED"
        ):
            raise OwnershipLost("Fleet control lease missing, replaced or revoked")

        # A live PID without a *positive* birth identity is not sufficient for
        # an authenticated admission, even though it conservatively retains an
        # existing legacy lease for ordinary operational cleanup.
        pid = row[4]
        birth = row[5]
        if (
            type(pid) is not int
            or pid != self._environment.process_id()
            or not isinstance(birth, str)
            or not birth
        ):
            raise OwnershipLost("Fleet control lease owner identity unproven")
        if (
            self._environment.process_alive(pid) is not True
            or self._environment.process_identity(pid) != birth
        ):
            raise OwnershipLost("Fleet control lease process birth changed or is unproven")

    def initial_fleet_control_is_current(
        self, run_id: str, control_epoch: int, lease_identity: str
    ) -> bool:
        """Effect-free precheck; the same proof MUST be repeated at commit."""
        try:
            with self._database.transaction() as connection:
                self.require_initial_fleet_control_in(
                    connection,
                    run_id=run_id,
                    control_epoch=control_epoch,
                    lease_identity=lease_identity,
                )
        except (OwnershipLost, sqlite3.Error, OSError):
            return False
        return True

    def assert_executable_owner(self, lease: ExecutionLease) -> None:
        with self._database.transaction() as connection:
            self._assert_executable_owner_in(connection, lease)

    def assert_fleet_owner(self, lease: ExecutionLease, fleet_run_id: str) -> None:
        if lease.kind != "FLEET" or lease.fleet_run_id != fleet_run_id:
            raise OwnershipLost("Execution lease does not own this fleet run")
        self.assert_owner(lease)

    def assert_idle(self, connection: sqlite3.Connection) -> None:
        for observed in self._retained_rows(connection):
            row = self._active_row_or_reclaim_in(connection, observed, remove_dead=True)
            if row is None and _concurrency_from_row(observed) is None:
                continue
            owner = str(observed["kind"]).lower()
            run = observed["fleet_run_id"]
            suffix = f" {run}" if run else ""
            raise ExecutionBusy(
                f"Cannot change fleet scope while {owner} execution{suffix} is active"
            )

    def assert_dead_fleet_worker_in(
        self, connection: sqlite3.Connection, fleet_run_id: str
    ) -> None:
        """Prove a retained worker predecessor dead before scoped recovery mutates it.

        Absence of an active lease also includes missing/foreign authority and
        cannot establish this proof. Reuse the native positive-death condition
        of the original explicit recovery transfer; a live reused PID remains
        conservatively blocked rather than becoming a signal/cleanup target.
        """
        self._database.require_atomic_write_cut(connection)
        lease = self.retained_fleet_lease_in(connection, fleet_run_id)
        if lease is None:
            raise OwnershipLost("Worker-exit recovery lacks its retained execution custody")
        if (
            lease.kind != "FLEET"
            or lease.fleet_run_id != fleet_run_id
            or lease.custody is None
            or lease.custody.phase != "WORKER_OWNED"
        ):
            raise OwnershipLost("Worker-exit recovery differs from retained worker custody")
        if self._environment.process_alive(lease.custody.owner_pid) is not False:
            raise ExecutionBusy("Worker predecessor death has not been positively established")

    def retained_lease_in(self, connection: sqlite3.Connection) -> ExecutionLease | None:
        """Observe retained custody in its canonical writer; grants no executable authority."""
        self._database.require_atomic_write_cut(connection)
        leases = self.retained_leases_in(connection)
        if len(leases) > 1:
            raise ExecutionBusy("Multiple retained Fleet owners require an explicit Run identity")
        return leases[0] if leases else None

    def assert_scope_idle(self, connection: sqlite3.Connection) -> None:
        """Scope edits never reclaim recorded execution ownership as a side effect."""
        row = connection.execute(
            "SELECT kind, fleet_run_id FROM execution_lease ORDER BY singleton LIMIT 1"
        ).fetchone()
        if row is None:
            return
        owner = str(row["kind"]).lower()
        run = row["fleet_run_id"]
        suffix = f" {run}" if run else ""
        raise ExecutionBusy(
            f"Cannot change fleet scope while {owner} execution{suffix} ownership is retained"
        )

    def require_capacity_in(
        self, connection: sqlite3.Connection, concurrency: RunConcurrencyAdmission | None = None
    ) -> None:
        """The admission planner's effect-free hint; insertion repeats this same rule."""
        rows = self._retained_rows(connection)
        if concurrency is None:
            rows = tuple(
                row
                for row in rows
                if _concurrency_from_row(row) is not None or self._owner_is_live(row)
            )
        pid = self._environment.process_id()
        self._capacity_slot(
            rows,
            kind="FLEET",
            fleet_run_id=concurrency.fleet_run_id if concurrency else None,
            concurrency=concurrency,
            pid=pid,
            birth=self._environment.process_identity(pid),
        )

    def _capacity_slot(
        self,
        retained: tuple[sqlite3.Row, ...],
        *,
        kind: ExecutionKind,
        fleet_run_id: str | None,
        concurrency: RunConcurrencyAdmission | None,
        pid: int,
        birth: str | None,
    ) -> int:
        if concurrency is not None:
            concurrency = RunConcurrencyAdmission.model_validate(
                concurrency.model_dump(mode="json", exclude_computed_fields=True)
            )
            controller = concurrency.aggregate.controller_birth
            if (
                kind != "FLEET"
                or concurrency.fleet_run_id != fleet_run_id
                or controller.pid != pid
                or f"{controller.provider}:{controller.boot_id}:{controller.start_ticks}" != birth
                or self._environment.process_alive(pid) is not True
            ):
                raise OwnershipLost("Concurrent admission lacks its exact original controller")
        for row in retained:
            existing = _concurrency_from_row(row)
            if (
                concurrency is None
                or existing is None
                or row["kind"] != "FLEET"
                or row["controller_session_id"] != self._database.session_id
                or row["controller_pid"] != pid
                or row["controller_identity"] != birth
                or existing.budget_identity != concurrency.budget_identity
            ):
                owner = str(row["kind"]).lower()
                run = row["fleet_run_id"]
                suffix = f" {run}" if run else ""
                raise ExecutionBusy(f"{owner.capitalize()} execution{suffix} is already active")
            if existing.fleet_run_id == concurrency.fleet_run_id:
                raise ExecutionBusy("Fleet run already owns its original reservation")
            if (
                existing.worker_unit_name == concurrency.worker_unit_name
                or existing.worker_scope_identity == concurrency.worker_scope_identity
            ):
                raise OwnershipLost("Concurrent Runs require distinct original native scopes")
        limit = concurrency.policy.max_active_runs if concurrency is not None else 1
        if len(retained) >= limit:
            raise ExecutionBusy("Original runtime execution capacity is full")
        used = {int(row["singleton"]) for row in retained}
        slot = next(index for index in range(1, limit + 1) if index not in used)
        return slot

    def _insert_lease(
        self,
        connection: sqlite3.Connection,
        lease: ExecutionLease,
        *,
        concurrency: RunConcurrencyAdmission | None = None,
        deferred_worker_resource_scope: dict[str, JsonValue] | None = None,
    ) -> ExecutionLease:
        self._database.require_atomic_write_cut(connection)
        deferred = _validated_deferred_payload(deferred_worker_resource_scope, concurrency)
        # Serial dead-owner reclamation keeps its old exact negative fences.
        # Concurrent resource occupancy is released only by native closure proof.
        for row in self._retained_rows(connection):
            self._active_row_or_reclaim_in(connection, row, remove_dead=True)
        retained = self._retained_rows(connection)
        now = self._environment.now().isoformat()
        pid = self._environment.process_id()
        birth = self._environment.process_identity(pid)
        slot = self._capacity_slot(
            retained,
            kind=lease.kind,
            fleet_run_id=lease.fleet_run_id,
            concurrency=concurrency,
            pid=pid,
            birth=birth,
        )
        custody = ExecutionCustody(
            "DIRECT_OWNED" if lease.kind == "DIRECT" else "ADMITTED_UNCLAIMED",
            self._database.session_id,
            pid,
            birth,
        )
        connection.execute(
            """
            INSERT INTO execution_lease(
                singleton, lease_token, owner_session_id, owner_pid, owner_identity, kind,
                fleet_run_id, acquired_at, custody_phase, concurrency_json,
                controller_session_id, controller_pid, controller_identity,
                deferred_worker_resource_scope_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                slot,
                lease.token,
                custody.owner_session_id,
                custody.owner_pid,
                custody.owner_identity,
                lease.kind,
                lease.fleet_run_id,
                now,
                custody.phase,
                concurrency.model_dump_json(exclude_computed_fields=True) if concurrency else None,
                self._database.session_id if concurrency else None,
                pid if concurrency else None,
                birth if concurrency else None,
                json.dumps(deferred, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
                if deferred is not None
                else None,
            ),
        )
        return (
            replace(
                lease,
                custody=custody,
                concurrency_identity=concurrency.identity if concurrency else None,
            )
            if lease.kind == "FLEET"
            else lease
        )

    def _active_lease_row(
        self,
        connection: sqlite3.Connection,
        *,
        remove_dead: bool,
        fleet_run_id: str | None = None,
    ) -> sqlite3.Row | None:
        if fleet_run_id is None:
            rows = self._retained_rows(connection)
            if len(rows) > 1:
                raise ExecutionBusy("Multiple execution owners require an explicit Run identity")
            row = rows[0] if rows else None
        else:
            row = connection.execute(
                "SELECT * FROM execution_lease WHERE kind='FLEET' AND fleet_run_id=?",
                (fleet_run_id,),
            ).fetchone()
        if row is None:
            return None
        return self._active_row_or_reclaim_in(connection, row, remove_dead=remove_dead)

    def _active_row_or_reclaim_in(
        self, connection: sqlite3.Connection, row: sqlite3.Row, *, remove_dead: bool
    ) -> sqlite3.Row | None:
        if row["custody_phase"] == "RESOURCE_RESERVED":
            return None
        if self._owner_is_live(row):
            return row
        if remove_dead and _concurrency_from_row(row) is None:
            # B7: this shared lease-store path is ALSO used by normal Direct
            # admission and other capacity writers. Without the original
            # negative fence they could bypass protected startup/cancel checks
            # and silently erase the only signed Fleet predecessor lease.
            if row["kind"] == "FLEET":
                run_id = row["fleet_run_id"]
                if type(run_id) is not str or not run_id:
                    raise OwnershipLost("dead Fleet lease has unprovable Run identity")
                from oh_goon.runtime.enforcer_lace_restart_reclaim_fence import (
                    require_no_original_lace_intent_before_reclamation_in,
                )

                require_no_original_lace_intent_before_reclamation_in(connection, run_id)
            cursor = connection.execute(
                "DELETE FROM execution_lease WHERE lease_token=?"
                " AND custody_phase=? AND owner_session_id=? AND owner_pid=?"
                " AND owner_identity IS ?",
                (
                    row["lease_token"],
                    row["custody_phase"],
                    row["owner_session_id"],
                    row["owner_pid"],
                    row["owner_identity"],
                ),
            )
            if cursor.rowcount != 1:
                raise OwnershipLost("dead execution predecessor changed before reclamation")
        return None


def _owned_fleet_lease_in(
    connection: sqlite3.Connection,
    fleet_run_id: str,
    session_id: str,
    process_id: int,
    process_identity: str | None,
) -> ExecutionLease | None:
    row = connection.execute(
        "SELECT * FROM execution_lease WHERE kind='FLEET' AND fleet_run_id=?", (fleet_run_id,)
    ).fetchone()
    if row is None or (
        str(row["owner_session_id"]) != session_id
        or int(row["owner_pid"]) != process_id
        or str(row["kind"]) != "FLEET"
        or str(row["fleet_run_id"]) != fleet_run_id
        or row["cancel_requested_at"] is not None
        or process_identity is None
        or row["owner_identity"] is None
        or str(row["owner_identity"]) != process_identity
    ):
        return None
    return _lease_from_row(row)


def _lease_row_in(connection: sqlite3.Connection, token: str) -> sqlite3.Row | None:
    return cast(
        sqlite3.Row | None,
        connection.execute(
            "SELECT * FROM execution_lease WHERE lease_token=?", (token,)
        ).fetchone(),
    )


def _concurrency_from_row(row: sqlite3.Row) -> RunConcurrencyAdmission | None:
    try:
        payload = row["concurrency_json"]
        admission = (
            RunConcurrencyAdmission.model_validate_json(payload) if payload is not None else None
        )
        if admission is not None and admission.fleet_run_id != row["fleet_run_id"]:
            raise ValueError("Concurrent resource admission differs from its original Run")
        return admission
    except (ValueError, TypeError, IndexError) as exc:
        raise OwnershipLost("stored execution capacity admission is invalid") from exc


def _resource_envelope_identity(envelope: RunResourceEnvelopeEvidence) -> str:
    return stable_id(
        "worker-resource-envelope", envelope.model_dump(mode="json", exclude_computed_fields=True)
    )


def _validated_deferred_payload(
    payload: dict[str, JsonValue] | None, admission: RunConcurrencyAdmission | None
) -> dict[str, JsonValue] | None:
    if admission is None:
        if payload is not None:
            raise OwnershipLost("Private worker resource intent has no concurrent admission")
        return None
    from oh_goon.integrations.run_resource_envelope import DeferredWorkerResourceScope

    if payload is None:
        raise OwnershipLost(
            "Concurrent admission requires its exact private worker resource intent"
        )
    deferred = DeferredWorkerResourceScope.from_payload(payload)
    if deferred.admission != admission:
        raise OwnershipLost("Private worker resource intent differs from its original admission")
    return cast(dict[str, JsonValue], deferred.to_payload())


def _deferred_from_row(
    row: sqlite3.Row, admission: RunConcurrencyAdmission | None
) -> dict[str, JsonValue] | None:
    try:
        raw = row["deferred_worker_resource_scope_json"]
        if raw is not None and (not isinstance(raw, str) or len(raw) > 512 * 1024):
            raise ValueError("Private worker resource intent exceeds its bounded transport")
        return _validated_deferred_payload(json.loads(raw) if raw is not None else None, admission)
    except (ValueError, TypeError, IndexError, FleetPlanInvalid) as exc:
        raise OwnershipLost("stored private worker resource intent is invalid") from exc


def _lease_from_row(row: sqlite3.Row) -> ExecutionLease:
    """Reject contradictory stored custody at its one decoding boundary."""
    try:
        admission = _concurrency_from_row(row)
        _deferred_from_row(row, admission)
        custody = ExecutionCustody(
            phase=cast(ExecutionCustodyPhase, row["custody_phase"]),
            owner_session_id=row["owner_session_id"],
            owner_pid=row["owner_pid"],
            owner_identity=row["owner_identity"],
        )
        lease = ExecutionLease(
            token=row["lease_token"],
            kind=row["kind"],
            fleet_run_id=row["fleet_run_id"],
            custody=custody,
            concurrency_identity=admission.identity if admission is not None else None,
            resource_envelope_identity=(
                _resource_envelope_identity(
                    RunResourceEnvelopeEvidence.model_validate_json(
                        row["worker_resource_envelope_json"]
                    )
                )
                if row["worker_resource_envelope_json"] is not None
                else None
            ),
        )
        return replace(lease, custody=None) if lease.kind == "DIRECT" else lease
    except (ValueError, TypeError, IndexError) as exc:
        raise OwnershipLost("stored execution custody is invalid") from exc
