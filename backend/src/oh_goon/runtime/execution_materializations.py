from __future__ import annotations

import json
import sqlite3

from oh_goon.core.errors import (
    ExecutionAssignmentStale,
    ExecutionMaterializationNotFound,
    ExecutionMaterializationStale,
    WorkspaceLeaseLost,
)
from oh_goon.core.product_identity import new_execution_materialization_id, stable_id
from oh_goon.core.models.execution_assignment import ExecutionAssignment
from oh_goon.core.models.execution_materialization import ExecutionMaterialization
from oh_goon.core.models.workspace import WorkspaceRef
from oh_goon.core.models.workspace_lease import WorkspaceLease
from oh_goon.core.time import utc_now
from oh_goon.runtime.database import LocalDatabase
from oh_goon.runtime.execution_assignments import ExecutionAssignmentAuthority
from oh_goon.runtime.workspace_leases import WorkspaceLeaseStore
from oh_goon.runtime.workspaces import RepositoryWorkspaceAuthority


class ExecutionMaterializationAuthority:
    """Durable owner that narrows admitted execution authority and binds provenance."""

    def __init__(
        self,
        database: LocalDatabase,
        *,
        workspaces: RepositoryWorkspaceAuthority | None = None,
        assignments: ExecutionAssignmentAuthority | None = None,
        leases: WorkspaceLeaseStore | None = None,
    ) -> None:
        self._database = database
        self._workspaces = workspaces or RepositoryWorkspaceAuthority(database)
        self._assignments = assignments or ExecutionAssignmentAuthority(database, self._workspaces)
        self._leases = leases or WorkspaceLeaseStore(
            database, workspaces=self._workspaces, assignments=self._assignments
        )

    def materialize(
        self,
        workspace: WorkspaceRef,
        assignment: ExecutionAssignment,
        lease: WorkspaceLease,
        *,
        tool_ids: tuple[str, ...] | None = None,
        input_artifact_ids: tuple[str, ...] | None = None,
        producer: str | None = None,
        producer_artifact_identity: str | None = None,
        producer_contract_version: str | None = None,
        producer_contract_identity: str | None = None,
        producer_work_identity: str | None = None,
    ) -> ExecutionMaterialization:
        try:
            durable_assignment = self._assignments.assignment(assignment.assignment_id)
            if durable_assignment != assignment:
                raise ExecutionMaterializationStale("Assignment differs from durable authority")
            current_lease = self._leases.assert_current(lease)
            checkpoint = self._workspaces.current_candidate_checkpoint(workspace, reprove=True)
        except (ExecutionAssignmentStale, WorkspaceLeaseLost) as exc:
            raise ExecutionMaterializationStale(str(exc)) from exc

        if workspace.workspace_id != assignment.workspace_id:
            raise ExecutionMaterializationStale("Materialization workspace differs from assignment")
        if current_lease.assignment_id != assignment.assignment_id:
            raise ExecutionMaterializationStale("Lease is bound to another assignment")
        if current_lease.assignment_fingerprint != assignment.assignment_fingerprint:
            raise ExecutionMaterializationStale("Lease assignment fingerprint changed")
        if checkpoint.checkpoint_id != assignment.input_checkpoint_id:
            raise ExecutionMaterializationStale("Assignment checkpoint is no longer current")
        if current_lease.input_checkpoint_id != checkpoint.checkpoint_id:
            raise ExecutionMaterializationStale("Lease checkpoint differs from current candidate")

        selected_tools = assignment.tool_ids if tool_ids is None else tuple(tool_ids)
        selected_artifacts = (
            assignment.input_artifact_ids if input_artifact_ids is None else tuple(input_artifact_ids)
        )
        if not set(selected_tools).issubset(assignment.tool_ids):
            raise ValueError("Execution materialization cannot widen assignment tool authority")
        if not set(selected_artifacts).issubset(assignment.input_artifact_ids):
            raise ValueError("Execution materialization cannot widen assignment input artifacts")
        if len(set(selected_tools)) != len(selected_tools) or len(set(selected_artifacts)) != len(selected_artifacts):
            raise ValueError("Execution materialization selections must not contain duplicates")

        materialization_id = new_execution_materialization_id()
        created_at = utc_now()
        payload = {
            "attempt_id": assignment.attempt_id,
            "assignment_id": assignment.assignment_id,
            "assignment_fingerprint": assignment.assignment_fingerprint,
            "workspace_id": workspace.workspace_id,
            "input_checkpoint_id": checkpoint.checkpoint_id,
            "lease_id": current_lease.lease_id,
            "lease_generation": current_lease.lease_generation,
            "executor_id": current_lease.executor_id,
            "executor_generation": current_lease.executor_generation,
            "tool_ids": list(selected_tools),
            "input_artifact_ids": list(selected_artifacts),
            "producer": producer,
            "producer_artifact_identity": producer_artifact_identity,
            "producer_contract_version": producer_contract_version,
            "producer_contract_identity": producer_contract_identity,
            "producer_work_identity": producer_work_identity,
        }
        fingerprint = stable_id("execution-materialization", payload)
        value = ExecutionMaterialization(
            materialization_id=materialization_id,
            created_at=created_at,
            resolved_fingerprint=fingerprint,
            **payload,
        )
        with self._database.transaction(write=True) as connection:
            # Rejoin the durable lease/checkpoint at the write cut.  Historical records are
            # not execution authority when current workspace state has moved.
            state_row = connection.execute(
                "SELECT current_lease_id, current_checkpoint_id FROM workspace_state WHERE workspace_id=?",
                (workspace.workspace_id,),
            ).fetchone()
            lease_row = connection.execute(
                "SELECT status, lease_generation, assignment_id, assignment_fingerprint, input_checkpoint_id, executor_id, executor_generation FROM workspace_leases WHERE lease_id=?",
                (current_lease.lease_id,),
            ).fetchone()
            if (
                state_row is None
                or lease_row is None
                or state_row["current_lease_id"] != current_lease.lease_id
                or state_row["current_checkpoint_id"] != checkpoint.checkpoint_id
                or lease_row["status"] != "ACTIVE"
                or int(lease_row["lease_generation"]) != current_lease.lease_generation
                or lease_row["assignment_id"] != assignment.assignment_id
                or lease_row["assignment_fingerprint"] != assignment.assignment_fingerprint
                or lease_row["input_checkpoint_id"] != checkpoint.checkpoint_id
                or lease_row["executor_id"] != current_lease.executor_id
                or int(lease_row["executor_generation"]) != current_lease.executor_generation
            ):
                raise ExecutionMaterializationStale(
                    "Assignment, lease, or checkpoint moved before materialization persistence"
                )
            try:
                connection.execute(
                    """
                    INSERT INTO execution_materializations(
                        materialization_id, attempt_id, assignment_id, assignment_fingerprint,
                        workspace_id, input_checkpoint_id, lease_id, lease_generation,
                        executor_id, executor_generation, resolved_fingerprint, payload_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        value.materialization_id,
                        value.attempt_id,
                        value.assignment_id,
                        value.assignment_fingerprint,
                        value.workspace_id,
                        value.input_checkpoint_id,
                        value.lease_id,
                        value.lease_generation,
                        value.executor_id,
                        value.executor_generation,
                        value.resolved_fingerprint,
                        json.dumps(value.model_dump(mode="json", exclude_computed_fields=True), sort_keys=True, separators=(",", ":")),
                        value.created_at.isoformat(),
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise ExecutionMaterializationStale(
                    "Execution materialization could not be durably bound"
                ) from exc
        return value

    def materialization(self, materialization_id: str) -> ExecutionMaterialization:
        with self._database.transaction() as connection:
            row = connection.execute(
                "SELECT payload_json FROM execution_materializations WHERE materialization_id=?",
                (materialization_id,),
            ).fetchone()
        if row is None:
            raise ExecutionMaterializationNotFound(
                f"Execution materialization not found: {materialization_id}"
            )
        return ExecutionMaterialization.model_validate(json.loads(row["payload_json"]))
