from __future__ import annotations

import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, TypeVar
from uuid import uuid4

from oh_goon.core.errors import ExecutionBusy, OwnershipLost
from oh_goon.runtime.database import LocalDatabase
from oh_goon.runtime.durable_revision import require_durable_revision
from oh_goon.runtime.execution.lease_environment import LeaseEnvironment, SystemLeaseEnvironment

T = TypeVar("T")
ExecutionKind = Literal["DIRECT", "FLEET"]


@dataclass(frozen=True, slots=True)
class ExecutionLease:
    token: str
    kind: ExecutionKind
    fleet_run_id: str | None


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
    def execution_connection_scope(self) -> Iterator[None]:
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
            self._insert_lease(connection, lease)
            result = mutate(connection)
        return lease, result

    def acquire_fleet_with(
        self,
        fleet_run_id: str,
        *,
        expected_durable_revision: int,
        mutate: Callable[[sqlite3.Connection], T],
    ) -> tuple[ExecutionLease, T]:
        lease = _new_lease("FLEET", fleet_run_id)
        with self._database.transaction(write=True) as connection:
            require_durable_revision(connection, expected_durable_revision)
            self._insert_lease(connection, lease)
            result = mutate(connection)
        return lease, result

    def acquire_fleet_in(
        self,
        connection: sqlite3.Connection,
        fleet_run_id: str,
        *,
        expected_durable_revision: int,
    ) -> ExecutionLease:
        """Create the Fleet lease inside a caller-owned admission transaction."""
        require_durable_revision(connection, expected_durable_revision)
        lease = _new_lease("FLEET", fleet_run_id)
        self._insert_lease(connection, lease)
        return lease

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
            self.assert_owner_in(connection, lease)
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
            self.assert_owner_in(connection, lease)
            result = mutate(connection)
            cursor = connection.execute(
                "DELETE FROM execution_lease WHERE singleton = 1 AND lease_token = ?",
                (lease.token,),
            )
            if cursor.rowcount != 1:
                raise OwnershipLost("Execution lease was lost before terminal release")
            return result

    def active_lease(self) -> ExecutionLease | None:
        with self._database.transaction() as connection:
            return self.active_lease_in(connection)

    def active_lease_in(self, connection: sqlite3.Connection) -> ExecutionLease | None:
        """Read the live lease inside a caller-owned durable cut."""
        row = self._active_lease_row(connection, remove_dead=False)
        if row is None:
            return None
        kind = str(row["kind"])
        return ExecutionLease(
            token=str(row["lease_token"]),
            kind="FLEET" if kind == "FLEET" else "DIRECT",
            fleet_run_id=str(row["fleet_run_id"]) if row["fleet_run_id"] is not None else None,
        )

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
            row = connection.execute("SELECT * FROM execution_lease WHERE singleton=1").fetchone()
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
        successor = _new_lease("FLEET", run_id)
        now = self._environment.now().isoformat()
        updated = connection.execute(
            """UPDATE execution_lease
               SET lease_token=?, owner_session_id=?, owner_pid=?, owner_identity=?,
                   acquired_at=?, cancel_requested_at=NULL
               WHERE singleton=1 AND lease_token=? AND fleet_run_id=?
                 AND cancel_requested_at IS NULL AND owner_session_id=?
                 AND owner_pid=? AND owner_identity=?""",
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
        self.assert_owner_in(connection, lease)
        row = connection.execute(
            "SELECT owner_session_id,owner_pid,owner_identity,cancel_requested_at"
            " FROM execution_lease WHERE singleton=1"
        ).fetchone()
        pid = self._environment.process_id()
        birth = self._environment.process_identity(pid)
        if (
            row is None
            or row[0] != f"recovery:{self._database.session_id}"
            or row[1] != pid
            or type(birth) is not str
            or not birth
            or row[2] != birth
            or row[3] is not None
            or self._environment.process_alive(pid) is not True
        ):
            raise OwnershipLost("recovery-only Fleet custody is not current")

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
        row = self._active_lease_row(connection, remove_dead=True)
        if row is None:
            return False
        if str(row["kind"]) != "FLEET" or row["fleet_run_id"] != fleet_run_id:
            raise OwnershipLost("Fleet run no longer owns the active execution lease")
        cursor = connection.execute(
            """
            UPDATE execution_lease
            SET cancel_requested_at = ?
            WHERE singleton = 1 AND lease_token = ?
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
    ) -> Iterator[Callable[[], bool]]:
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
            "SELECT cancel_requested_at FROM execution_lease WHERE singleton = 1"
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
        row = connection.execute(
            "SELECT lease_token, kind, fleet_run_id FROM execution_lease WHERE singleton = 1"
        ).fetchone()
        if row is None or str(row["lease_token"]) != lease.token:
            raise OwnershipLost("Execution lease was lost")
        if str(row["kind"]) != lease.kind or row["fleet_run_id"] != lease.fleet_run_id:
            raise OwnershipLost("Execution lease identity changed")
        custody = connection.execute(
            "SELECT owner_session_id,owner_pid,owner_identity FROM execution_lease WHERE singleton=1"
        ).fetchone()
        if custody is not None and str(custody[0]).startswith("recovery:"):
            pid = self._environment.process_id()
            birth = self._environment.process_identity(pid)
            if (
                custody[0] != f"recovery:{self._database.session_id}"
                or custody[1] != pid
                or not birth
                or custody[2] != birth
                or self._environment.process_alive(pid) is not True
            ):
                raise OwnershipLost("recovery owner session/process identity changed")

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
                          owner_pid, owner_identity, cancel_requested_at
                   FROM execution_lease WHERE singleton = 1"""
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
            self.assert_owner_in(connection, lease)
            row = connection.execute(
                "SELECT owner_session_id FROM execution_lease WHERE singleton=1"
            ).fetchone()
            if row is None or str(row[0]).startswith("recovery:"):
                raise OwnershipLost("recovery custody is not executable admission")

    def assert_fleet_owner(self, lease: ExecutionLease, fleet_run_id: str) -> None:
        if lease.kind != "FLEET" or lease.fleet_run_id != fleet_run_id:
            raise OwnershipLost("Execution lease does not own this fleet run")
        self.assert_owner(lease)

    def assert_idle(self, connection: sqlite3.Connection) -> None:
        row = self._active_lease_row(connection, remove_dead=True)
        if row is None:
            return
        owner = str(row["kind"]).lower()
        run = row["fleet_run_id"]
        suffix = f" {run}" if run else ""
        raise ExecutionBusy(f"Cannot change fleet scope while {owner} execution{suffix} is active")

    def assert_scope_idle(self, connection: sqlite3.Connection) -> None:
        """Scope edits never reclaim recorded execution ownership as a side effect."""
        row = connection.execute(
            "SELECT kind, fleet_run_id FROM execution_lease WHERE singleton = 1"
        ).fetchone()
        if row is None:
            return
        owner = str(row["kind"]).lower()
        run = row["fleet_run_id"]
        suffix = f" {run}" if run else ""
        raise ExecutionBusy(
            f"Cannot change fleet scope while {owner} execution{suffix} ownership is retained"
        )

    def _insert_lease(self, connection: sqlite3.Connection, lease: ExecutionLease) -> None:
        row = self._active_lease_row(connection, remove_dead=True)
        if row is not None:
            owner = str(row["kind"]).lower()
            run = row["fleet_run_id"]
            suffix = f" {run}" if run else ""
            raise ExecutionBusy(f"{owner.capitalize()} execution{suffix} is already active")
        now = self._environment.now().isoformat()
        connection.execute(
            """
            INSERT INTO execution_lease(
                singleton, lease_token, owner_session_id, owner_pid, owner_identity, kind,
                fleet_run_id, acquired_at
            ) VALUES (1, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                lease.token,
                self._database.session_id,
                self._environment.process_id(),
                self._environment.process_identity(self._environment.process_id()),
                lease.kind,
                lease.fleet_run_id,
                now,
            ),
        )

    def _active_lease_row(
        self,
        connection: sqlite3.Connection,
        *,
        remove_dead: bool,
    ) -> sqlite3.Row | None:
        row: sqlite3.Row | None = connection.execute(
            "SELECT * FROM execution_lease WHERE singleton = 1"
        ).fetchone()
        if row is None:
            return None
        owner_pid = int(row["owner_pid"])
        owner_identity = str(row["owner_identity"]) if row["owner_identity"] is not None else None
        if self._environment.process_alive(owner_pid):
            if owner_identity is None:
                # Owner without positive birth identity: PID liveness is conservative. PID reuse can
                # retain a stale lease, but can never create two owners.
                return row
            observed_identity = self._environment.process_identity(owner_pid)
            if observed_identity is None or observed_identity == owner_identity:
                # If the host temporarily cannot prove birth identity, fail closed by
                # retaining ownership. A positively different birth identity proves
                # PID reuse and allows stale-row reclamation.
                return row
        if remove_dead:
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
            connection.execute(
                "DELETE FROM execution_lease WHERE singleton = 1 AND lease_token = ?",
                (str(row["lease_token"]),),
            )
        return None


def _owned_fleet_lease_in(
    connection: sqlite3.Connection,
    fleet_run_id: str,
    session_id: str,
    process_id: int,
    process_identity: str | None,
) -> ExecutionLease | None:
    row = connection.execute("SELECT * FROM execution_lease WHERE singleton = 1").fetchone()
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
    return ExecutionLease(token=str(row["lease_token"]), kind="FLEET", fleet_run_id=fleet_run_id)
