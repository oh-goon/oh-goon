from __future__ import annotations

import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from threading import Lock
from time import monotonic
from typing import TypeVar

from oh_goon.core.errors import OwnershipLost
from oh_goon.runtime.database import LocalDatabase
from oh_goon.runtime.execution.lease_environment import LeaseEnvironment
from oh_goon.runtime.execution.sqlite_lease import ExecutionLease, SQLiteExecutionLeaseStore

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

    _lease: ExecutionLease
    recovery_only: bool = False


@dataclass(slots=True)
class ExecutionSession:
    """One live execution owner with transaction-scoped mutation authority."""

    _leases: SQLiteExecutionLeaseStore
    _lease: ExecutionLease
    recovery_only: bool = False
    _finished: bool = field(default=False, init=False)

    @property
    def fleet_run_id(self) -> str | None:
        return self._lease.fleet_run_id

    def original_fleet_lease_token(self) -> str:
        """Return only this bound Fleet session token; mutation verifies its live owner."""
        self.require_fleet_run_id()
        if self._finished:
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
    def execution_connection_scope(self) -> Iterator[None]:
        """Own one admitted SQLite handle for this execution thread's durable cuts."""
        if self._finished:
            raise OwnershipLost("Execution session already released its ownership")
        with self._leases.execution_connection_scope():
            yield

    def cancel_requested(self) -> bool:
        try:
            return self._leases.cancellation_requested(self._lease)
        except OwnershipLost:
            return True

    def cancel_requested_in(self, connection: sqlite3.Connection) -> bool:
        """Read cancellation from the caller's already owner-bound SQLite cut."""
        return self._leases.cancellation_requested_in(connection, self._lease)

    @contextmanager
    def preparation_cancellation_observer(self) -> Iterator[Callable[[], bool]]:
        """Own one cadence-bounded query-only cancellation observer for preparation.

        One native read-only handle is retained only for the preparation scope. Each due
        observation starts a fresh read transaction, drops unpinned pager cache, re-proves
        path/schema/lease identity, reads cancellation, and ends the transaction. Durable
        writes/effect fences never use this observer path.
        """

        if self._finished:
            raise OwnershipLost("Execution session already released its ownership")
        with self._leases.preparation_cancellation_observer(self._lease) as observe:
            yield _BoundedCancellationProbe(
                observe,
                interval_seconds=PREPARATION_CANCELLATION_OBSERVATION_INTERVAL_SECONDS,
            )

    def assert_owner(self) -> None:
        self._leases.assert_owner(self._lease)

    def assert_fleet_owner(self, fleet_run_id: str) -> None:
        self._leases.assert_fleet_owner(self._lease, fleet_run_id)

    def observe(self, observe: Callable[[sqlite3.Connection], T]) -> T:
        """Read one durable authority cut only while this exact owner is valid."""
        if self._finished:
            raise OwnershipLost("Execution session already released its ownership")
        return self._leases.observe_as_owner(self._lease, observe)

    def mutate_recovery(self, mutate: Callable[[sqlite3.Connection], T]) -> T:
        """Only recovery-specific owners may request this limited write capability."""
        if not self.recovery_only or self._finished:
            raise OwnershipLost("recovery-only Fleet custody required")
        return self._leases.mutate_recovery_as_owner(self._lease, mutate)

    def mutate(self, mutate: Callable[[sqlite3.Connection], T]) -> T:
        """Run a durable mutation only if this exact owner is valid in that transaction."""
        if self._finished or self.recovery_only:
            raise OwnershipLost("recovery-only custody cannot mutate executable work")
        return self._leases.mutate_as_owner(self._lease, mutate)

    def finish(self, mutate: Callable[[sqlite3.Connection], T]) -> T:
        """Persist terminal state and release the lease atomically."""
        if self._finished or self.recovery_only:
            raise OwnershipLost("recovery-only custody cannot terminalize Fleet work")
        result = self._leases.finish_as_owner(self._lease, mutate)
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
        if ticket.recovery_only:
            raise OwnershipLost("recovery custody cannot activate executable Fleet session")
        session = ExecutionSession(self._leases, ticket._lease)
        # Recovered custody may not be re-labelled as normal Run/Step admission.
        session.assert_owner()
        self._leases.assert_executable_owner(ticket._lease)
        return session

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

    def assert_scope_idle(self, connection: sqlite3.Connection) -> None:
        self._leases.assert_scope_idle(connection)
