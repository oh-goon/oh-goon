from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from datetime import timedelta
from typing import Literal

from oh_goon.core.errors import (
    ExecutionAssignmentStale,
    WorkspaceFenceUnproven,
    WorkspaceLeaseBusy,
    WorkspaceLeaseExpired,
    WorkspaceLeaseLost,
    WorkspaceLeaseNotFound,
    WorkspaceStateStale,
    WorkspaceTransitionInvalid,
)
from oh_goon.core.models.execution_assignment import ExecutionAssignment
from oh_goon.core.models.workspace import WorkspaceRef, WorkspaceState
from oh_goon.core.models.workspace_lease import WorkspaceLease, WriterQuiescenceEvidence
from oh_goon.core.product_identity import new_workspace_lease_id
from oh_goon.core.time import utc_now
from oh_goon.core.vocabulary.workspace import WorkspaceLifecycleState
from oh_goon.runtime.database import LocalDatabase
from oh_goon.runtime.execution_assignments import ExecutionAssignmentAuthority
from oh_goon.runtime.workspaces import RepositoryWorkspaceAuthority

QuiescenceProver = Callable[[WorkspaceLease], WriterQuiescenceEvidence]


class WorkspaceLeaseStore:
    """Durable mutation-possession owner with generation fencing and explicit quiescence proof."""

    def __init__(
        self,
        database: LocalDatabase,
        *,
        workspaces: RepositoryWorkspaceAuthority | None = None,
        assignments: ExecutionAssignmentAuthority | None = None,
    ) -> None:
        self._database = database
        self._workspaces = workspaces or RepositoryWorkspaceAuthority(database)
        self._assignments = assignments or ExecutionAssignmentAuthority(database, self._workspaces)

    def acquire(
        self,
        workspace: WorkspaceRef,
        assignment: ExecutionAssignment,
        *,
        executor_id: str,
        executor_generation: int,
        expected_state_version: int,
        ttl_seconds: int = 300,
    ) -> tuple[WorkspaceState, WorkspaceLease]:
        if ttl_seconds < 1 or ttl_seconds > 86_400:
            raise ValueError("workspace lease ttl_seconds must be between 1 and 86400")
        durable_assignment = self._assignments.assignment(assignment.assignment_id)
        if durable_assignment != assignment:
            raise ExecutionAssignmentStale("Execution assignment differs from durable authority")
        if assignment.filesystem_authority != "candidate_mutation":
            raise ExecutionAssignmentStale(
                "Workspace mutation lease requires candidate_mutation authority"
            )
        if assignment.workspace_id != workspace.workspace_id:
            raise ExecutionAssignmentStale("Execution assignment names another workspace")
        checkpoint = self._workspaces.current_candidate_checkpoint(workspace, reprove=True)
        if checkpoint.checkpoint_id != assignment.input_checkpoint_id:
            raise ExecutionAssignmentStale("Execution assignment checkpoint is no longer current")

        issued_at = utc_now()
        expires_at = issued_at + timedelta(seconds=ttl_seconds)
        with self._database.transaction(write=True) as connection:
            state = self._state_for_update(connection, workspace.workspace_id)
            if state.state_version != expected_state_version:
                raise WorkspaceStateStale(
                    f"Workspace state changed concurrently: expected {expected_state_version}, observed {state.state_version}"
                )
            if state.current_lease_id is not None:
                raise WorkspaceLeaseBusy(
                    f"Workspace already has current lease: {state.current_lease_id}"
                )
            if state.lifecycle_state is not WorkspaceLifecycleState.READY:
                raise WorkspaceTransitionInvalid(
                    f"Workspace mutation lease requires READY state, observed {state.lifecycle_state.value}"
                )
            row = connection.execute(
                "SELECT COALESCE(MAX(lease_generation), 0) AS generation FROM workspace_leases WHERE workspace_id=?",
                (workspace.workspace_id,),
            ).fetchone()
            lease_generation = int(row["generation"]) + 1
            lease = WorkspaceLease(
                lease_id=new_workspace_lease_id(),
                workspace_id=workspace.workspace_id,
                attempt_id=assignment.attempt_id,
                assignment_id=assignment.assignment_id,
                assignment_fingerprint=assignment.assignment_fingerprint,
                executor_id=executor_id,
                executor_generation=executor_generation,
                input_checkpoint_id=assignment.input_checkpoint_id,
                lease_generation=lease_generation,
                issued_at=issued_at,
                expires_at=expires_at,
            )
            connection.execute(
                """
                INSERT INTO workspace_leases(
                    lease_id, workspace_id, attempt_id, assignment_id, assignment_fingerprint,
                    executor_id, executor_generation, input_checkpoint_id, lease_generation,
                    issued_at, expires_at, status, writer_authority_kind, writer_authority_id,
                    fence_evidence_json, finished_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'ACTIVE', NULL, NULL, NULL, NULL)
                """,
                (
                    lease.lease_id,
                    lease.workspace_id,
                    lease.attempt_id,
                    lease.assignment_id,
                    lease.assignment_fingerprint,
                    lease.executor_id,
                    lease.executor_generation,
                    lease.input_checkpoint_id,
                    lease.lease_generation,
                    lease.issued_at.isoformat(),
                    lease.expires_at.isoformat(),
                ),
            )
            next_state = self._write_workspace_lease_state(
                connection,
                state,
                lifecycle_state=WorkspaceLifecycleState.IN_USE,
                current_lease_id=lease.lease_id,
            )
        return next_state, lease

    def lease(self, lease_id: str) -> WorkspaceLease:
        with self._database.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM workspace_leases WHERE lease_id=?", (lease_id,)
            ).fetchone()
        if row is None:
            raise WorkspaceLeaseNotFound(f"Workspace lease not found: {lease_id}")
        return _workspace_lease(row)

    def assert_current(self, lease: WorkspaceLease) -> WorkspaceLease:
        with self._database.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM workspace_leases WHERE lease_id=?", (lease.lease_id,)
            ).fetchone()
            state_row = connection.execute(
                "SELECT * FROM workspace_state WHERE workspace_id=?", (lease.workspace_id,)
            ).fetchone()
            # Sample time only after durable reads have completed.  A caller that waited
            # for SQLite must not be authorized using a clock value sampled before it waited.
            now = utc_now()
            if row is None or state_row is None:
                raise WorkspaceLeaseLost(f"Workspace lease is no longer durable: {lease.lease_id}")
            observed = _workspace_lease(row)
            if row["status"] != "ACTIVE" or state_row["current_lease_id"] != lease.lease_id:
                raise WorkspaceLeaseLost(f"Workspace lease is no longer current: {lease.lease_id}")
            if observed != lease:
                raise WorkspaceLeaseLost(f"Workspace lease authority changed: {lease.lease_id}")
            if now >= observed.expires_at:
                raise WorkspaceLeaseExpired(
                    f"Workspace lease expired and requires reconciliation before reuse: {lease.lease_id}"
                )
        return observed

    def mark_writer_started(
        self,
        lease: WorkspaceLease,
        *,
        authority_kind: str,
        authority_id: str,
    ) -> WorkspaceLease:
        if authority_kind not in {"cgroup", "process_group", "remote_backend"}:
            raise ValueError(
                "writer authority must be cgroup, process_group, or remote_backend; PID-only fencing is forbidden"
            )
        with self._database.transaction(write=True) as connection:
            row = connection.execute(
                "SELECT * FROM workspace_leases WHERE lease_id=?", (lease.lease_id,)
            ).fetchone()
            state_row = connection.execute(
                "SELECT * FROM workspace_state WHERE workspace_id=?", (lease.workspace_id,)
            ).fetchone()
            now = utc_now()
            if row is None or state_row is None:
                raise WorkspaceLeaseLost(f"Workspace lease is no longer durable: {lease.lease_id}")
            observed = _workspace_lease(row)
            if row["status"] != "ACTIVE" or state_row["current_lease_id"] != lease.lease_id:
                raise WorkspaceLeaseLost(f"Workspace lease is no longer current: {lease.lease_id}")
            if observed != lease:
                raise WorkspaceLeaseLost(f"Workspace lease authority changed: {lease.lease_id}")
            if now >= observed.expires_at:
                raise WorkspaceLeaseExpired(
                    f"Workspace lease expired before writer authority could be bound: {lease.lease_id}"
                )
            result = connection.execute(
                """
                UPDATE workspace_leases
                SET writer_authority_kind=?, writer_authority_id=?
                WHERE lease_id=? AND status='ACTIVE' AND writer_authority_kind IS NULL
                """,
                (authority_kind, authority_id, lease.lease_id),
            )
            if result.rowcount != 1:
                raise WorkspaceLeaseLost(
                    "Workspace writer authority was already bound or lease was lost"
                )
        return self.lease(lease.lease_id)

    def release(
        self,
        lease: WorkspaceLease,
        *,
        expected_state_version: int,
        quiescence: WriterQuiescenceEvidence | None = None,
    ) -> WorkspaceState:
        # Release is one durable authority cut.  Do not validate the lease in one
        # transaction and finish it in another: writer authority could otherwise be
        # bound in between and a formerly writer-free lease could be released without
        # proving that new writer quiescent.
        return self._finish(
            lease,
            expected_state_version=expected_state_version,
            status="RELEASED",
            quiescence=quiescence,
            next_lifecycle=WorkspaceLifecycleState.READY,
            expiry_requirement="unexpired",
        )

    def reconcile_expired(
        self,
        lease_id: str,
        *,
        expected_state_version: int,
        prove_quiescence: QuiescenceProver | None = None,
    ) -> WorkspaceState:
        lease = self.lease(lease_id)
        if utc_now() < lease.expires_at:
            raise WorkspaceTransitionInvalid("Workspace lease is not expired")
        if lease.writer_authority_kind is None:
            return self._finish(
                lease,
                expected_state_version=expected_state_version,
                status="EXPIRED",
                quiescence=None,
                next_lifecycle=WorkspaceLifecycleState.READY,
                expiry_requirement="expired",
            )
        evidence = prove_quiescence(lease) if prove_quiescence is not None else None
        if evidence is None:
            self._finish(
                lease,
                expected_state_version=expected_state_version,
                status="QUARANTINED",
                quiescence=None,
                next_lifecycle=WorkspaceLifecycleState.FAILED,
                expiry_requirement="expired",
            )
            raise WorkspaceFenceUnproven(
                "Expired workspace writer cannot be proven quiescent; workspace quarantined"
            )
        try:
            self._require_quiescence(lease, evidence)
        except WorkspaceFenceUnproven:
            self._finish(
                lease,
                expected_state_version=expected_state_version,
                status="QUARANTINED",
                quiescence=evidence,
                next_lifecycle=WorkspaceLifecycleState.FAILED,
                expiry_requirement="expired",
            )
            raise
        return self._finish(
            lease,
            expected_state_version=expected_state_version,
            status="EXPIRED",
            quiescence=evidence,
            next_lifecycle=WorkspaceLifecycleState.READY,
            expiry_requirement="expired",
        )

    def require_writer_quiescence(
        self,
        lease: WorkspaceLease,
        evidence: WriterQuiescenceEvidence | None,
    ) -> None:
        """Verify exact physical-writer quiescence without changing lease state."""

        current = self.assert_current(lease)
        if current.writer_authority_kind is not None:
            self._require_quiescence(current, evidence)

    @staticmethod
    def _require_quiescence(
        lease: WorkspaceLease,
        evidence: WriterQuiescenceEvidence | None,
    ) -> None:
        if evidence is None or not evidence.quiescent:
            raise WorkspaceFenceUnproven("Current workspace writer is not proven quiescent")
        if (
            evidence.lease_id != lease.lease_id
            or evidence.authority_kind != lease.writer_authority_kind
            or evidence.authority_id != lease.writer_authority_id
            or evidence.executor_id != lease.executor_id
            or evidence.executor_generation != lease.executor_generation
        ):
            raise WorkspaceFenceUnproven(
                "Writer quiescence evidence does not match current lease authority"
            )

    def _finish(
        self,
        lease: WorkspaceLease,
        *,
        expected_state_version: int,
        status: str,
        quiescence: WriterQuiescenceEvidence | None,
        next_lifecycle: WorkspaceLifecycleState,
        expiry_requirement: Literal["unexpired", "expired"],
    ) -> WorkspaceState:
        with self._database.transaction(write=True) as connection:
            state = self._state_for_update(connection, lease.workspace_id)
            row = connection.execute(
                "SELECT * FROM workspace_leases WHERE lease_id=?", (lease.lease_id,)
            ).fetchone()
            # Time is sampled only after the durable state/lease reads, in the same
            # write transaction that will retire the lease.
            finished_at = utc_now()
            if state.state_version != expected_state_version:
                raise WorkspaceStateStale(
                    f"Workspace state changed concurrently: expected {expected_state_version}, observed {state.state_version}"
                )
            if state.current_lease_id != lease.lease_id:
                raise WorkspaceLeaseLost("Workspace lease is no longer current")
            if row is None or row["status"] != "ACTIVE":
                raise WorkspaceLeaseLost("Workspace lease is no longer active")
            observed = _workspace_lease(row)
            if observed != lease:
                raise WorkspaceLeaseLost("Workspace lease authority changed before finish")
            if expiry_requirement == "unexpired" and finished_at >= observed.expires_at:
                raise WorkspaceLeaseExpired(
                    f"Workspace lease expired before release: {lease.lease_id}"
                )
            if expiry_requirement == "expired" and finished_at < observed.expires_at:
                raise WorkspaceTransitionInvalid("Workspace lease is not expired")
            if (
                next_lifecycle is WorkspaceLifecycleState.READY
                and observed.writer_authority_kind is not None
            ):
                self._require_quiescence(observed, quiescence)
            connection.execute(
                """
                UPDATE workspace_leases
                SET status=?, fence_evidence_json=?, finished_at=?
                WHERE lease_id=? AND status='ACTIVE'
                """,
                (
                    status,
                    (
                        json.dumps(
                            quiescence.model_dump(mode="json", exclude_computed_fields=True),
                            sort_keys=True,
                            separators=(",", ":"),
                        )
                        if quiescence is not None
                        else None
                    ),
                    finished_at.isoformat(),
                    lease.lease_id,
                ),
            )
            return self._write_workspace_lease_state(
                connection,
                state,
                lifecycle_state=next_lifecycle,
                current_lease_id=None,
            )

    @staticmethod
    def _state_for_update(connection: sqlite3.Connection, workspace_id: str) -> WorkspaceState:
        row = connection.execute(
            "SELECT * FROM workspace_state WHERE workspace_id=?", (workspace_id,)
        ).fetchone()
        if row is None:
            raise WorkspaceStateStale(f"Workspace state not found: {workspace_id}")
        return WorkspaceState(
            workspace_id=row["workspace_id"],
            lifecycle_state=WorkspaceLifecycleState(row["lifecycle_state"]),
            state_version=int(row["state_version"]),
            current_lease_id=row["current_lease_id"],
            current_checkpoint_id=row["current_checkpoint_id"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _write_workspace_lease_state(
        connection: sqlite3.Connection,
        current: WorkspaceState,
        *,
        lifecycle_state: WorkspaceLifecycleState,
        current_lease_id: str | None,
    ) -> WorkspaceState:
        now = utc_now()
        version = current.state_version + 1
        result = connection.execute(
            """
            UPDATE workspace_state
            SET lifecycle_state=?, state_version=?, current_lease_id=?, updated_at=?
            WHERE workspace_id=? AND state_version=?
            """,
            (
                lifecycle_state.value,
                version,
                current_lease_id,
                now.isoformat(),
                current.workspace_id,
                current.state_version,
            ),
        )
        if result.rowcount != 1:
            raise WorkspaceStateStale("Workspace state changed concurrently while changing lease")
        return WorkspaceState(
            workspace_id=current.workspace_id,
            lifecycle_state=lifecycle_state,
            state_version=version,
            current_lease_id=current_lease_id,
            current_checkpoint_id=current.current_checkpoint_id,
            updated_at=now,
        )


def _workspace_lease(row: sqlite3.Row) -> WorkspaceLease:
    return WorkspaceLease(
        lease_id=row["lease_id"],
        workspace_id=row["workspace_id"],
        attempt_id=row["attempt_id"],
        assignment_id=row["assignment_id"],
        assignment_fingerprint=row["assignment_fingerprint"],
        executor_id=row["executor_id"],
        executor_generation=int(row["executor_generation"]),
        input_checkpoint_id=row["input_checkpoint_id"],
        lease_generation=int(row["lease_generation"]),
        issued_at=row["issued_at"],
        expires_at=row["expires_at"],
        writer_authority_kind=row["writer_authority_kind"],
        writer_authority_id=row["writer_authority_id"],
    )
