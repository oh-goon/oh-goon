from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
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
from oh_goon.core.models.execution_materialization import ExecutionMaterialization
from oh_goon.core.models.fleet_plan_evidence import PlanWorkspaceWriterScopeEvidence
from oh_goon.core.models.workspace import WorkspaceRef, WorkspaceState
from oh_goon.core.models.workspace_lease import WorkspaceLease, WriterQuiescenceEvidence
from oh_goon.core.product_identity import new_workspace_lease_id
from oh_goon.core.time import utc_now
from oh_goon.core.vocabulary.workspace import WorkspaceLifecycleState
from oh_goon.runtime.database import LocalDatabase
from oh_goon.runtime.execution_assignments import ExecutionAssignmentAuthority
from oh_goon.runtime.workspaces import RepositoryWorkspaceAuthority

QuiescenceProver = Callable[[WorkspaceLease], WriterQuiescenceEvidence]


@dataclass(frozen=True, slots=True)
class NativeWorkspaceQuiescence:
    """Complete actual namespace observations from the original execution owner."""

    scopes: tuple[PlanWorkspaceWriterScopeEvidence, ...]
    physical_execution_ids: tuple[str, ...]
    backend_configuration_id: str

    def payload(self) -> dict[str, object]:
        return {
            "kind": "native_namespace_scope",
            "physical_execution_ids": list(self.physical_execution_ids),
            "backend_configuration_id": self.backend_configuration_id,
            "scopes": [scope.model_dump(mode="json") for scope in self.scopes],
        }


def require_native_workspace_quiescence(
    materialization: ExecutionMaterialization,
    *,
    scopes: tuple[PlanWorkspaceWriterScopeEvidence, ...],
    physical_execution_ids: tuple[str, ...],
    backend_configuration_id: str,
) -> None:
    """Require an exact original-launch partition and actual closed PID namespaces."""
    observed = tuple(scope.physical_execution_id for scope in scopes)
    if (
        materialization.writer_scope_kind != "native_namespace_scope"
        or not backend_configuration_id
        or not physical_execution_ids
        or len(set(physical_execution_ids)) != len(physical_execution_ids)
        or len(set(observed)) != len(observed)
        or set(observed) != set(physical_execution_ids)
        or any(
            scope.materialization != materialization
            or scope.workspace_device != materialization.workspace_device
            or scope.workspace_inode != materialization.workspace_inode
            or scope.workspace_mode != materialization.workspace_mode
            or scope.backend_configuration_id != backend_configuration_id
            or not scope.namespace_distinct
            or not scope.init_death_observed
            or scope.observed_at < materialization.created_at
            for scope in scopes
        )
    ):
        raise WorkspaceFenceUnproven(
            "Native candidate requires complete original namespace-writer closure"
        )


NativeQuiescenceProver = Callable[[WorkspaceLease], NativeWorkspaceQuiescence]


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
            if self._native_materialization_in(connection, observed) is not None:
                raise WorkspaceLeaseLost(
                    "Native namespace materialization already owns the writer scope"
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
        native_quiescence: NativeWorkspaceQuiescence | None = None,
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
            native_quiescence=native_quiescence,
            next_lifecycle=WorkspaceLifecycleState.READY,
            expiry_requirement="unexpired",
        )

    def release_unmaterialized(
        self, lease: WorkspaceLease, *, expected_state_version: int
    ) -> WorkspaceState:
        """Release failed preparation only if no execution materialization was issued.

        The no-materialization and no-writer facts are read in the same original
        write cut as lease release. A commit-unknown materialization remains a
        potential writer and cannot use this preparation-only route.
        """
        return self._finish(
            lease,
            expected_state_version=expected_state_version,
            status="RELEASED",
            quiescence=None,
            next_lifecycle=WorkspaceLifecycleState.READY,
            expiry_requirement="unexpired",
            require_unmaterialized=True,
        )

    def reconcile_expired(
        self,
        lease_id: str,
        *,
        expected_state_version: int,
        prove_quiescence: QuiescenceProver | None = None,
        prove_native_quiescence: NativeQuiescenceProver | None = None,
    ) -> WorkspaceState:
        lease = self.lease(lease_id)
        if utc_now() < lease.expires_at:
            raise WorkspaceTransitionInvalid("Workspace lease is not expired")
        with self._database.transaction() as connection:
            native = self._native_materialization_in(connection, lease)
        if native is not None:
            native_evidence = (
                prove_native_quiescence(lease) if prove_native_quiescence is not None else None
            )
            if native_evidence is not None:
                try:
                    require_native_workspace_quiescence(
                        native,
                        scopes=native_evidence.scopes,
                        physical_execution_ids=native_evidence.physical_execution_ids,
                        backend_configuration_id=native_evidence.backend_configuration_id,
                    )
                except WorkspaceFenceUnproven:
                    self._finish(
                        lease,
                        expected_state_version=expected_state_version,
                        status="QUARANTINED",
                        quiescence=None,
                        native_quiescence=native_evidence,
                        next_lifecycle=WorkspaceLifecycleState.FAILED,
                        expiry_requirement="expired",
                    )
                    raise
            state = self._finish(
                lease,
                expected_state_version=expected_state_version,
                status="EXPIRED" if native_evidence is not None else "QUARANTINED",
                quiescence=None,
                native_quiescence=native_evidence,
                next_lifecycle=WorkspaceLifecycleState.READY
                if native_evidence is not None
                else WorkspaceLifecycleState.FAILED,
                expiry_requirement="expired",
            )
            if native_evidence is None:
                raise WorkspaceFenceUnproven(
                    "Expired native workspace writer is unproven; workspace quarantined"
                )
            return state
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
        *,
        native_quiescence: NativeWorkspaceQuiescence | None = None,
    ) -> None:
        """Verify exact physical-writer quiescence without changing lease state."""

        current = self.assert_current(lease)
        with self._database.transaction() as connection:
            self.require_native_writer_quiescence_in(connection, current, native_quiescence)
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
        native_quiescence: NativeWorkspaceQuiescence | None = None,
        require_unmaterialized: bool = False,
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
            if require_unmaterialized and (
                observed.writer_authority_kind is not None
                or connection.execute(
                    "SELECT 1 FROM execution_materializations WHERE lease_id=? LIMIT 1",
                    (lease.lease_id,),
                ).fetchone()
                is not None
            ):
                raise WorkspaceFenceUnproven(
                    "Candidate preparation has durable writer or materialization authority"
                )
            if (
                next_lifecycle is WorkspaceLifecycleState.READY
                and observed.writer_authority_kind is not None
            ):
                self._require_quiescence(observed, quiescence)
            if next_lifecycle is WorkspaceLifecycleState.READY:
                self.require_native_writer_quiescence_in(connection, observed, native_quiescence)
            fence = None
            if native_quiescence is not None:
                fence = native_quiescence.payload()
            elif quiescence is not None:
                fence = quiescence.model_dump(mode="json", exclude_computed_fields=True)
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
                            fence,
                            sort_keys=True,
                            separators=(",", ":"),
                        )
                        if fence is not None
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
    def _native_materialization_in(
        connection: sqlite3.Connection, lease: WorkspaceLease
    ) -> ExecutionMaterialization | None:
        rows = connection.execute(
            "SELECT payload_json, resolved_fingerprint FROM execution_materializations "
            "WHERE lease_id=?",
            (lease.lease_id,),
        ).fetchall()
        found = None
        for row in rows:
            value = ExecutionMaterialization.model_validate(json.loads(row["payload_json"]))
            if value.writer_scope_kind is None:
                continue
            if (
                found is not None
                or value.resolved_fingerprint != row["resolved_fingerprint"]
                or value.workspace_id != lease.workspace_id
                or value.lease_id != lease.lease_id
                or value.attempt_id != lease.attempt_id
                or value.assignment_id != lease.assignment_id
                or value.assignment_fingerprint != lease.assignment_fingerprint
                or value.lease_generation != lease.lease_generation
                or value.executor_id != lease.executor_id
                or value.executor_generation != lease.executor_generation
                or value.input_checkpoint_id != lease.input_checkpoint_id
            ):
                raise WorkspaceFenceUnproven("Native workspace materialization authority differs")
            found = value
        return found

    @classmethod
    def require_native_writer_quiescence_in(
        cls,
        connection: sqlite3.Connection,
        lease: WorkspaceLease,
        evidence: NativeWorkspaceQuiescence | None,
    ) -> None:
        materialization = cls._native_materialization_in(connection, lease)
        if materialization is None:
            if evidence is not None:
                raise WorkspaceFenceUnproven("Native closure has no native writer materialization")
            return
        if evidence is None:
            raise WorkspaceFenceUnproven("Native workspace writer absence is unproven")
        require_native_workspace_quiescence(
            materialization,
            scopes=evidence.scopes,
            physical_execution_ids=evidence.physical_execution_ids,
            backend_configuration_id=evidence.backend_configuration_id,
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
