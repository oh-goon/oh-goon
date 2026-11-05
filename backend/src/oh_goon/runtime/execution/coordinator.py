from __future__ import annotations

import sqlite3
from collections.abc import Callable, Generator
from contextlib import contextmanager
from dataclasses import dataclass, field
from threading import Lock
from time import monotonic
from typing import TypeVar

from pydantic import JsonValue

from oh_goon.core.errors import OwnershipLost
from oh_goon.runtime.database import LocalDatabase
from oh_goon.runtime.execution.lease_environment import LeaseEnvironment
from oh_goon.runtime.execution.sqlite_lease import (
    ExecutionCustody,
    ExecutionLease,
    SQLiteExecutionLeaseStore,
)

T = TypeVar("T")

PREPARATION_CANCELLATION_OBSERVATION_INTERVAL_SECONDS = 0.25


class _BoundedCancellationProbe:
    """Bound hot-loop cancellation reads without becoming ownership authority.

    Immutable input preparation can cross tens of thousands of file/chunk checkpoints.
    Each checkpoint still calls this probe, but a negative durable SQLite observation is
    reused only for one short monotonic interval. Positive cancellation is sticky.
    Durable ownership/effect fences continue to use ``ExecutionSession`` transactions
    directly, so this process-local cache can delay cancellation observation slightly but
    can never grant mutation or external-effect authority.
    """

    def __init__(self, observe: Callable[[], bool], *, interval_seconds: float) -> None:
        if interval_seconds <= 0:
            raise ValueError("cancellation observation interval must be positive")
        self._observe = observe
        self._interval_seconds = interval_seconds
        self._last_observed_at: float | None = None
        self._cancelled = False
        self._lock = Lock()

    def __call__(self) -> bool:
        if self._cancelled:
            return True
        now = monotonic()
        with self._lock:
            if self._cancelled:
                return True
            last = self._last_observed_at
            if last is not None and now - last < self._interval_seconds:
                return False
            # Update the cadence only after a successful durable observation. A SQLite
            # identity/storage failure must propagate and must not be hidden by the cache.
            observed = self._observe()
            self._last_observed_at = now
            if observed:
                self._cancelled = True
            return observed


@dataclass(frozen=True, slots=True)
class ExecutionTicket:
    """Opaque admission token passed from request admission to the worker."""

    _lease: ExecutionLease = field(repr=False)
    recovery_only: bool = False

    def require_fleet_run_id(self) -> str:
        """Read the ticket's exact Run binding without granting execution authority."""
        if self._lease.kind != "FLEET" or self._lease.fleet_run_id is None:
            raise OwnershipLost("Execution ticket is not a Fleet run admission")
        return self._lease.fleet_run_id

    def export_worker_handoff(self) -> dict[str, JsonValue]:
        """Export one unclaimed capability only for the protected worker transport.

        This representation is secret capability material, never diagnostic output.
        Export does not grant execution: the receiver must still win the original
        SQLite custody claim before acquiring an executable session.
        """
        custody = self._lease.custody
        if (
            type(self) is not ExecutionTicket
            or self.recovery_only is not False
            or self._lease.kind != "FLEET"
            or self._lease.fleet_run_id is None
            or custody is None
            or custody.phase != "ADMITTED_UNCLAIMED"
            or custody.owner_identity is None
        ):
            raise OwnershipLost("exact unclaimed Fleet capability required for worker handoff")
        return {
            "version": 1,
            "fleet_run_id": self._lease.fleet_run_id,
            "lease_token": self._lease.token,
            "predecessor": {
                "phase": custody.phase,
                "owner_session_id": custody.owner_session_id,
                "owner_pid": custody.owner_pid,
                "owner_identity": custody.owner_identity,
            },
        }

    @classmethod
    def from_worker_handoff(cls, payload: dict[str, JsonValue]) -> ExecutionTicket:
        """Decode only the original admission shape, without widening its authority."""
        invalid = "invalid protected Fleet worker capability"
        if (
            type(payload) is not dict
            or set(payload) != {"version", "fleet_run_id", "lease_token", "predecessor"}
            or type(payload.get("version")) is not int
            or payload["version"] != 1
        ):
            raise OwnershipLost(invalid)
        run_id = payload["fleet_run_id"]
        token = payload["lease_token"]
        predecessor = payload["predecessor"]
        if (
            not isinstance(run_id, str)
            or not run_id
            or not isinstance(token, str)
            or not token
            or not isinstance(predecessor, dict)
            or set(predecessor) != {"phase", "owner_session_id", "owner_pid", "owner_identity"}
            or predecessor["phase"] != "ADMITTED_UNCLAIMED"
        ):
            raise OwnershipLost(invalid)
        session = predecessor["owner_session_id"]
        pid = predecessor["owner_pid"]
        birth = predecessor["owner_identity"]
        if (
            not isinstance(session, str)
            or not session
            or session.startswith("recovery:")
            or type(pid) is not int
            or pid < 1
            or not isinstance(birth, str)
            or not birth
        ):
            raise OwnershipLost(invalid)
        return ExecutionTicket(
            ExecutionLease(
                token=token,
                kind="FLEET",
                fleet_run_id=run_id,
                custody=ExecutionCustody("ADMITTED_UNCLAIMED", session, pid, birth),
            )
        )


@dataclass(slots=True)
class ExecutionSession:
    """One live execution owner with transaction-scoped mutation authority."""

    _leases: SQLiteExecutionLeaseStore
    _lease: ExecutionLease
    recovery_only: bool = False
    _submission_only: bool = False
    _finished: bool = field(default=False, init=False)

    def __post_init__(self) -> None:
        if self.recovery_only and self._submission_only:
            raise ValueError("execution session cannot combine custody capabilities")

    @property
    def fleet_run_id(self) -> str | None:
        return self._lease.fleet_run_id

    def original_fleet_lease_token(self) -> str:
        """Return only this bound Fleet session token; mutation verifies its live owner."""
        self.require_fleet_run_id()
        if self._finished or self._submission_only:
            raise OwnershipLost("Fleet execution session has already finished")
        return self._lease.token

    def require_fleet_run_id(self) -> str:
        """Return the Fleet identity carried by this exact owner or fail closed."""
        if self._lease.kind != "FLEET" or self._lease.fleet_run_id is None:
            raise OwnershipLost("Execution session is not a Fleet run owner")
        return self._lease.fleet_run_id

    @property
    def finished(self) -> bool:
        return self._finished

    @contextmanager
    def execution_connection_scope(self) -> Generator[None, None, None]:
        """Own one admitted SQLite handle for this execution thread's durable cuts."""
        if self._finished or self._submission_only:
            raise OwnershipLost("Execution session already released its ownership")
        with self._leases.execution_connection_scope():
            yield

    def cancel_requested(self) -> bool:
        if self._submission_only:
            raise OwnershipLost("submission settlement has no execution observation authority")
        try:
            return self._leases.cancellation_requested(self._lease)
        except OwnershipLost:
            return True

    def cancel_requested_in(self, connection: sqlite3.Connection) -> bool:
        """Read cancellation from the caller's already owner-bound SQLite cut."""
        if self._submission_only:
            raise OwnershipLost("submission settlement has no execution observation authority")
        return self._leases.cancellation_requested_in(connection, self._lease)

    @contextmanager
    def preparation_cancellation_observer(self) -> Generator[Callable[[], bool], None, None]:
        """Own one cadence-bounded query-only cancellation observer for preparation.

        One native read-only handle is retained only for the preparation scope. Each due
        observation starts a fresh read transaction, drops unpinned pager cache, re-proves
        path/schema/lease identity, reads cancellation, and ends the transaction. Durable
        writes/effect fences never use this observer path.
        """

        if self._finished or self._submission_only:
            raise OwnershipLost("Execution session already released its ownership")
        with self._leases.preparation_cancellation_observer(self._lease) as observe:
            yield _BoundedCancellationProbe(
                observe,
                interval_seconds=PREPARATION_CANCELLATION_OBSERVATION_INTERVAL_SECONDS,
            )

    def assert_owner(self) -> None:
        if self._submission_only:
            raise OwnershipLost("submission settlement has no execution authority")
        self._leases.assert_owner(self._lease)

    def assert_fleet_owner(self, fleet_run_id: str) -> None:
        if self._submission_only:
            raise OwnershipLost("submission settlement has no execution authority")
        self._leases.assert_fleet_owner(self._lease, fleet_run_id)

    def observe(self, observe: Callable[[sqlite3.Connection], T]) -> T:
        """Read one durable authority cut only while this exact owner is valid."""
        if self._finished or self._submission_only:
            raise OwnershipLost("Execution session already released its ownership")
        return self._leases.observe_as_owner(self._lease, observe)

    def mutate_recovery(self, mutate: Callable[[sqlite3.Connection], T]) -> T:
        """Only recovery-specific owners may request this limited write capability."""
        if not self.recovery_only or self._finished:
            raise OwnershipLost("recovery-only Fleet custody required")
        return self._leases.mutate_recovery_as_owner(self._lease, mutate)

    def mutate(self, mutate: Callable[[sqlite3.Connection], T]) -> T:
        """Run a durable mutation only if this exact owner is valid in that transaction."""
        if self._finished or self.recovery_only or self._submission_only:
            raise OwnershipLost("recovery-only custody cannot mutate executable work")
        return self._leases.mutate_as_owner(self._lease, mutate)

    def finish(self, mutate: Callable[[sqlite3.Connection], T]) -> T:
        """Persist terminal state and release the lease atomically."""
        if self._finished or self.recovery_only:
            raise OwnershipLost("recovery-only custody cannot terminalize Fleet work")
        finish = (
            self._leases.finish_submission_as_owner
            if self._submission_only
            else self._leases.finish_as_owner
        )
        result = finish(self._lease, mutate)
        self._finished = True
        return result


class ExecutionCoordinator:
    """Public runtime boundary for admission, cancellation and execution ownership."""

    def __init__(
        self,
        database: LocalDatabase,
        *,
        lease_environment: LeaseEnvironment | None = None,
    ) -> None:
        self._database = database
        self._leases = SQLiteExecutionLeaseStore(database, environment=lease_environment)

    def reserve_direct_with(
        self,
        *,
        expected_durable_revision: int,
        mutate: Callable[[sqlite3.Connection], T],
    ) -> tuple[ExecutionTicket, T]:
        lease, result = self._leases.acquire_direct_with(
            expected_durable_revision=expected_durable_revision,
            mutate=mutate,
        )
        return ExecutionTicket(lease), result

    def reserve_fleet_with(
        self,
        fleet_run_id: str,
        *,
        expected_durable_revision: int,
        mutate: Callable[[sqlite3.Connection], T],
    ) -> tuple[ExecutionTicket, T]:
        lease, result = self._leases.acquire_fleet_with(
            fleet_run_id,
            expected_durable_revision=expected_durable_revision,
            mutate=mutate,
        )
        return ExecutionTicket(lease), result

    def reserve_fleet_in(
        self,
        connection: sqlite3.Connection,
        fleet_run_id: str,
        *,
        expected_durable_revision: int,
    ) -> ExecutionTicket:
        """Create one Fleet execution owner inside a caller-owned admission transaction."""
        lease = self._leases.acquire_fleet_in(
            connection,
            fleet_run_id,
            expected_durable_revision=expected_durable_revision,
        )
        return ExecutionTicket(lease)

    def activate(self, ticket: ExecutionTicket) -> ExecutionSession:
        """Activate an admitted owner without adding hidden release semantics.

        Once durable work has been admitted, only ``ExecutionSession.finish`` may
        release its lease.  An unexpected process failure therefore leaves durable
        ownership for restart recovery instead of manufacturing an ownerless active
        lifecycle state.
        """
        if type(ticket) is not ExecutionTicket or ticket.recovery_only:
            raise OwnershipLost("recovery custody cannot activate executable Fleet session")
        # Recovered custody may not be re-labelled as normal Run/Step admission.
        self._leases.assert_executable_owner(ticket._lease)
        return ExecutionSession(self._leases, ticket._lease)

    def require_claimed_fleet_worker(self, ticket: ExecutionTicket) -> None:
        """Verify current worker custody without creating an executable session."""
        if type(ticket) is not ExecutionTicket or ticket.recovery_only:
            raise OwnershipLost("claimed Fleet worker ticket required")
        ticket.require_fleet_run_id()
        self._leases.assert_executable_owner(ticket._lease)

    def claim_fleet_worker(self, ticket: ExecutionTicket) -> ExecutionTicket:
        """Claim an already admitted Run; never create or rediscover its work."""
        from oh_goon.core.vocabulary import FleetRunStatus
        from oh_goon.runtime.journal import Journal

        if type(ticket) is not ExecutionTicket or ticket.recovery_only:
            raise OwnershipLost("original unclaimed Fleet ticket required")
        fleet_run_id = ticket._lease.fleet_run_id
        if fleet_run_id is None:
            raise OwnershipLost("worker claim requires an admitted Fleet run")
        journal = Journal(self._database)

        def require_admitted(connection: sqlite3.Connection) -> None:
            detail = journal.read_view_from_connection(connection).run_detail(fleet_run_id)
            if detail is None or detail.run.status is not FleetRunStatus.QUEUED:
                raise OwnershipLost("Fleet run is no longer awaiting worker claim")

        return ExecutionTicket(self._leases.claim_fleet_worker(ticket._lease, require_admitted))

    def activate_submission_failure(self, ticket: ExecutionTicket) -> ExecutionSession:
        """Give the original unclaimed admission only atomic terminal settlement."""
        if type(ticket) is not ExecutionTicket or ticket.recovery_only:
            raise OwnershipLost("original unclaimed Fleet ticket required")
        self._leases.assert_submission_owner(ticket._lease)
        return ExecutionSession(self._leases, ticket._lease, _submission_only=True)

    def transfer_dead_fleet_to_recovery_in(
        self,
        connection: sqlite3.Connection,
        *,
        run_id: str,
        predecessor_token: str,
        predecessor_epoch: int,
    ) -> tuple[ExecutionTicket, dict[str, str | int]]:
        lease, predecessor = self._leases.transfer_dead_fleet_to_recovery_in(
            connection,
            run_id=run_id,
            predecessor_token=predecessor_token,
            predecessor_epoch=predecessor_epoch,
        )
        return ExecutionTicket(lease, recovery_only=True), predecessor

    def activate_recovery(self, ticket: ExecutionTicket) -> ExecutionSession:
        if type(ticket) is not ExecutionTicket or ticket.recovery_only is not True:
            raise OwnershipLost("original recovery-only ticket required")
        self._leases.assert_recovery_owner(ticket._lease)
        return ExecutionSession(self._leases, ticket._lease, recovery_only=True)

    def request_fleet_cancel_in(
        self,
        connection: sqlite3.Connection,
        fleet_run_id: str,
    ) -> bool:
        """Mark cancellation or reclaim a dead owner in one caller write transaction."""
        return self._leases.request_fleet_cancel_in(connection, fleet_run_id)

    def active_lease(self) -> ExecutionLease | None:
        return self._leases.active_lease()

    def active_lease_in(self, transaction: sqlite3.Connection) -> ExecutionLease | None:
        return self._leases.active_lease_in(transaction)

    def owned_fleet_ticket_in(
        self, connection: sqlite3.Connection, fleet_run_id: str
    ) -> ExecutionTicket | None:
        """Recover only this process/session's exact durable Fleet admission ticket."""
        lease = self._leases.owned_fleet_lease_in(connection, fleet_run_id)
        return ExecutionTicket(lease) if lease is not None else None

    def assert_idle(self, connection: sqlite3.Connection) -> None:
        self._leases.assert_idle(connection)

    def assert_dead_fleet_worker_in(
        self, connection: sqlite3.Connection, fleet_run_id: str
    ) -> None:
        """Require exact retained worker custody and native positive-death proof."""
        self._leases.assert_dead_fleet_worker_in(connection, fleet_run_id)

    def retained_lease_in(self, connection: sqlite3.Connection) -> ExecutionLease | None:
        """Observe retained custody for recovery, never activate a stale owner."""
        return self._leases.retained_lease_in(connection)

    def assert_scope_idle(self, connection: sqlite3.Connection) -> None:
        self._leases.assert_scope_idle(connection)
