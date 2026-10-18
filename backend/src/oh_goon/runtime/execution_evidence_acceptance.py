from __future__ import annotations

import json
import sqlite3
from datetime import datetime

from oh_goon.core.errors import (
    ExecutionEvidenceAcceptanceNotFound,
    ExecutionEvidenceRejected,
)
from oh_goon.core.product_identity import new_execution_evidence_acceptance_id, stable_id
from oh_goon.core.models.execution_assignment import ExecutionAssignment
from oh_goon.core.models.execution_evidence_acceptance import AcceptedExecutionEvidence
from oh_goon.core.models.execution_materialization import ExecutionMaterialization
from oh_goon.core.models.workspace import WorkspaceRef
from oh_goon.core.models.workspace_lease import WorkspaceLease, WriterQuiescenceEvidence
from oh_goon.core.time import utc_now
from oh_goon.runtime.database import LocalDatabase
from oh_goon.runtime.execution_assignments import ExecutionAssignmentAuthority
from oh_goon.runtime.execution_materializations import ExecutionMaterializationAuthority
from oh_goon.runtime.workspace_leases import WorkspaceLeaseStore
from oh_goon.runtime.workspaces import RepositoryWorkspaceAuthority


class ExecutionEvidenceAcceptanceAuthority:
    """Accept raw executor evidence only after exact current authority is rejoined."""

    def __init__(
        self,
        database: LocalDatabase,
        *,
        workspaces: RepositoryWorkspaceAuthority | None = None,
        assignments: ExecutionAssignmentAuthority | None = None,
        leases: WorkspaceLeaseStore | None = None,
        materializations: ExecutionMaterializationAuthority | None = None,
    ) -> None:
        self._database = database
        self._workspaces = workspaces or RepositoryWorkspaceAuthority(database)
        self._assignments = assignments or ExecutionAssignmentAuthority(database, self._workspaces)
        self._leases = leases or WorkspaceLeaseStore(
            database, workspaces=self._workspaces, assignments=self._assignments
        )
        self._materializations = materializations or ExecutionMaterializationAuthority(
            database,
            workspaces=self._workspaces,
            assignments=self._assignments,
            leases=self._leases,
        )

    def accept(
        self,
        workspace: WorkspaceRef,
        assignment: ExecutionAssignment,
        lease: WorkspaceLease,
        materialization: ExecutionMaterialization,
        *,
        output_checkpoint_id: str,
        result_identity: str,
        evidence_artifact_ids: tuple[str, ...] = (),
        producer_work_identity: str | None = None,
        quiescence: WriterQuiescenceEvidence | None = None,
    ) -> AcceptedExecutionEvidence:
        self._validate_request(output_checkpoint_id, result_identity, evidence_artifact_ids)
        self._validate_live_authority(
            workspace, assignment, lease, materialization,
            output_checkpoint_id=output_checkpoint_id,
            producer_work_identity=producer_work_identity,
            quiescence=quiescence,
        )
        with self._database.transaction(write=True) as connection:
            rows = self._load_durable_rows(
                connection, workspace, assignment, lease, materialization, output_checkpoint_id
            )
            # The lease's persisted expiry, not the preflight snapshot, decides
            # acceptance. The original write cut also owns the receipt timestamp.
            accepted_at = self._validate_durable_rows(
                connection, rows, workspace, assignment, lease, materialization,
                output_checkpoint_id=output_checkpoint_id,
                producer_work_identity=producer_work_identity,
            )
            value = self._new_acceptance(
                workspace, assignment, lease, materialization,
                output_checkpoint_id=output_checkpoint_id,
                result_identity=result_identity,
                evidence_artifact_ids=evidence_artifact_ids,
                producer_work_identity=producer_work_identity,
                accepted_at=accepted_at,
            )
            self._persist_acceptance(connection, value)
        return value

    @staticmethod
    def _validate_request(
        output_checkpoint_id: str, result_identity: str, evidence_artifact_ids: tuple[str, ...]
    ) -> None:
        if not result_identity:
            raise ValueError("result_identity is required")
        if not output_checkpoint_id:
            raise ValueError("output_checkpoint_id is required")
        if len(set(evidence_artifact_ids)) != len(evidence_artifact_ids):
            raise ValueError("evidence_artifact_ids must not contain duplicates")

    def _validate_live_authority(
        self, workspace, assignment, lease, materialization, *, output_checkpoint_id,
        producer_work_identity, quiescence,
    ) -> None:
        if self._assignments.assignment(assignment.assignment_id) != assignment:
            raise ExecutionEvidenceRejected("Assignment differs from durable authority")
        if self._materializations.materialization(materialization.materialization_id) != materialization:
            raise ExecutionEvidenceRejected("Materialization differs from durable authority")
        self._leases.require_writer_quiescence(lease, quiescence)
        current_output = self._workspaces.current_candidate_checkpoint(workspace, reprove=True)
        if current_output.checkpoint_id != output_checkpoint_id:
            raise ExecutionEvidenceRejected("Output checkpoint is no longer the current candidate")
        self._validate_materialized_scope(
            workspace, assignment, lease, materialization, producer_work_identity
        )

    @staticmethod
    def _validate_materialized_scope(workspace, assignment, lease, materialization, producer_work_identity) -> None:
        if assignment.attempt_id != materialization.attempt_id or lease.attempt_id != assignment.attempt_id:
            raise ExecutionEvidenceRejected("Attempt identity differs across assignment/lease/materialization")
        if assignment.workspace_id != workspace.workspace_id or materialization.workspace_id != workspace.workspace_id:
            raise ExecutionEvidenceRejected("Workspace identity differs across admitted authorities")
        if materialization.assignment_id != assignment.assignment_id:
            raise ExecutionEvidenceRejected("Materialization belongs to another assignment")
        if materialization.assignment_fingerprint != assignment.assignment_fingerprint:
            raise ExecutionEvidenceRejected("Materialization assignment fingerprint differs")
        if materialization.lease_id != lease.lease_id or materialization.lease_generation != lease.lease_generation:
            raise ExecutionEvidenceRejected("Materialization lease identity differs")
        if materialization.executor_id != lease.executor_id or materialization.executor_generation != lease.executor_generation:
            raise ExecutionEvidenceRejected("Materialization executor generation differs")
        if materialization.input_checkpoint_id != assignment.input_checkpoint_id:
            raise ExecutionEvidenceRejected("Materialization input checkpoint differs")
        if producer_work_identity != materialization.producer_work_identity:
            raise ExecutionEvidenceRejected("Producer work identity differs from materialization provenance")

    @staticmethod
    def _new_acceptance(
        workspace, assignment, lease, materialization, *, output_checkpoint_id, result_identity,
        evidence_artifact_ids, producer_work_identity, accepted_at,
    ) -> AcceptedExecutionEvidence:
        payload = {
            "attempt_id": assignment.attempt_id, "assignment_id": assignment.assignment_id,
            "assignment_fingerprint": assignment.assignment_fingerprint, "workspace_id": workspace.workspace_id,
            "input_checkpoint_id": assignment.input_checkpoint_id, "output_checkpoint_id": output_checkpoint_id,
            "lease_id": lease.lease_id, "lease_generation": lease.lease_generation,
            "executor_id": lease.executor_id, "executor_generation": lease.executor_generation,
            "materialization_id": materialization.materialization_id,
            "materialization_fingerprint": materialization.resolved_fingerprint,
            "producer_work_identity": producer_work_identity, "result_identity": result_identity,
            "evidence_artifact_ids": list(evidence_artifact_ids),
        }
        return AcceptedExecutionEvidence(
            acceptance_id=new_execution_evidence_acceptance_id(),
            acceptance_fingerprint=stable_id("execution-evidence-acceptance", payload),
            accepted_at=accepted_at, **payload,
        )

    @staticmethod
    def _load_durable_rows(connection, workspace, assignment, lease, materialization, output_checkpoint_id):
        state = connection.execute(
            "SELECT current_checkpoint_id, current_lease_id FROM workspace_state WHERE workspace_id=?",
            (workspace.workspace_id,),
        ).fetchone()
        lease_row = connection.execute(
            """SELECT status, attempt_id, assignment_id, assignment_fingerprint, input_checkpoint_id,
                      lease_generation, executor_id, executor_generation, writer_authority_kind, writer_authority_id,
                      issued_at, expires_at
               FROM workspace_leases WHERE lease_id=?""", (lease.lease_id,),
        ).fetchone()
        assignment_row = connection.execute(
            "SELECT assignment_fingerprint, payload_json FROM execution_assignments WHERE assignment_id=?",
            (assignment.assignment_id,),
        ).fetchone()
        materialization_row = connection.execute(
            """SELECT assignment_id, assignment_fingerprint, workspace_id, input_checkpoint_id, lease_id,
                      lease_generation, executor_id, executor_generation, resolved_fingerprint, payload_json
               FROM execution_materializations WHERE materialization_id=?""", (materialization.materialization_id,),
        ).fetchone()
        output_row = connection.execute(
            "SELECT workspace_id, parent_checkpoint_id, attempt_id, assignment_id FROM candidate_checkpoints WHERE checkpoint_id=?",
            (output_checkpoint_id,),
        ).fetchone()
        return state, lease_row, assignment_row, materialization_row, output_row

    def _validate_durable_rows(
        self, connection, rows, workspace, assignment, lease, materialization, *, output_checkpoint_id, producer_work_identity
    ) -> datetime:
        state, lease_row, assignment_row, materialization_row, output_row = rows
        self._validate_durable_workspace_and_lease(state, lease_row, assignment, lease, output_checkpoint_id)
        self._validate_durable_assignment_and_materialization(
            assignment_row, materialization_row, workspace, assignment, lease, materialization
        )
        if output_row is None or output_row["workspace_id"] != workspace.workspace_id:
            raise ExecutionEvidenceRejected("Output checkpoint belongs to another workspace")
        if not self._checkpoint_descends_from(
            connection, output_checkpoint_id=output_checkpoint_id, input_checkpoint_id=assignment.input_checkpoint_id,
            attempt_id=assignment.attempt_id, assignment_id=assignment.assignment_id,
        ):
            raise ExecutionEvidenceRejected("Output checkpoint does not descend from the admitted input")
        self._validate_durable_producer(materialization_row, materialization, producer_work_identity)
        # Sample only after all durable authority reads/lineage checks, immediately
        # before original-owner acceptance INSERT. This is the single write-cut
        # clock for both rejection and the eventual receipt.
        accepted_at = utc_now()
        try:
            persisted_expires_at = datetime.fromisoformat(lease_row["expires_at"])
            persisted_issued_at = datetime.fromisoformat(lease_row["issued_at"])
            if persisted_expires_at.tzinfo is None or persisted_issued_at.tzinfo is None:
                raise ValueError("naive lease timestamp")
        except (TypeError, ValueError) as exc:
            raise ExecutionEvidenceRejected("Durable workspace lease timing is invalid") from exc
        if persisted_expires_at != lease.expires_at or persisted_issued_at != lease.issued_at:
            raise ExecutionEvidenceRejected("Durable workspace lease timing changed before acceptance")
        if accepted_at >= persisted_expires_at:
            raise ExecutionEvidenceRejected("Workspace lease expired before evidence acceptance")
        return accepted_at

    @staticmethod
    def _validate_durable_workspace_and_lease(state, lease_row, assignment, lease, output_checkpoint_id) -> None:
        if state is None or state["current_lease_id"] != lease.lease_id:
            raise ExecutionEvidenceRejected("Workspace lease moved before result acceptance")
        if state["current_checkpoint_id"] != output_checkpoint_id:
            raise ExecutionEvidenceRejected("Workspace checkpoint moved before result acceptance")
        if lease_row is None or lease_row["status"] != "ACTIVE":
            raise ExecutionEvidenceRejected("Workspace lease is no longer active")
        expected = (assignment.attempt_id, assignment.assignment_id, assignment.assignment_fingerprint,
                    assignment.input_checkpoint_id, lease.lease_generation, lease.executor_id, lease.executor_generation)
        observed = (lease_row["attempt_id"], lease_row["assignment_id"], lease_row["assignment_fingerprint"],
                    lease_row["input_checkpoint_id"], int(lease_row["lease_generation"]),
                    lease_row["executor_id"], int(lease_row["executor_generation"]))
        if observed != expected:
            raise ExecutionEvidenceRejected("Lease authority changed before result acceptance")

    @staticmethod
    def _validate_durable_assignment_and_materialization(
        assignment_row, materialization_row, workspace, assignment, lease, materialization
    ) -> None:
        if assignment_row is None or assignment_row["assignment_fingerprint"] != assignment.assignment_fingerprint:
            raise ExecutionEvidenceRejected("Assignment authority changed before result acceptance")
        if materialization_row is None:
            raise ExecutionEvidenceRejected("Materialization authority changed before result acceptance")
        expected = (assignment.assignment_id, assignment.assignment_fingerprint, workspace.workspace_id,
                    assignment.input_checkpoint_id, lease.lease_id, lease.lease_generation,
                    lease.executor_id, lease.executor_generation, materialization.resolved_fingerprint)
        observed = (materialization_row["assignment_id"], materialization_row["assignment_fingerprint"],
                    materialization_row["workspace_id"], materialization_row["input_checkpoint_id"],
                    materialization_row["lease_id"], int(materialization_row["lease_generation"]),
                    materialization_row["executor_id"], int(materialization_row["executor_generation"]),
                    materialization_row["resolved_fingerprint"])
        if observed != expected:
            raise ExecutionEvidenceRejected("Materialization authority changed before result acceptance")

    @staticmethod
    def _validate_durable_producer(materialization_row, materialization, producer_work_identity) -> None:
        payload = json.loads(materialization_row["payload_json"])
        durable = tuple(payload.get(key) for key in (
            "producer", "producer_artifact_identity", "producer_contract_version",
            "producer_contract_identity", "producer_work_identity",
        ))
        materialized = (materialization.producer, materialization.producer_artifact_identity,
                        materialization.producer_contract_version, materialization.producer_contract_identity,
                        materialization.producer_work_identity)
        if durable != materialized:
            raise ExecutionEvidenceRejected("Producer provenance changed before result acceptance")
        if materialization.producer_work_identity != producer_work_identity:
            raise ExecutionEvidenceRejected("Producer work identity changed before result acceptance")

    @staticmethod
    def _persist_acceptance(connection, value: AcceptedExecutionEvidence) -> None:
        try:
            connection.execute(
                """INSERT INTO execution_evidence_acceptances(
                    acceptance_id, attempt_id, assignment_id, assignment_fingerprint, workspace_id,
                    input_checkpoint_id, output_checkpoint_id, lease_id, lease_generation, executor_id,
                    executor_generation, materialization_id, materialization_fingerprint, result_identity,
                    acceptance_fingerprint, payload_json, accepted_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (value.acceptance_id, value.attempt_id, value.assignment_id, value.assignment_fingerprint,
                 value.workspace_id, value.input_checkpoint_id, value.output_checkpoint_id, value.lease_id,
                 value.lease_generation, value.executor_id, value.executor_generation, value.materialization_id,
                 value.materialization_fingerprint, value.result_identity, value.acceptance_fingerprint,
                 json.dumps(value.model_dump(mode="json", exclude_computed_fields=True), sort_keys=True, separators=(",", ":")),
                 value.accepted_at.isoformat()),
            )
        except sqlite3.IntegrityError as exc:
            raise ExecutionEvidenceRejected(
                "Execution evidence was already accepted or could not be durably bound"
            ) from exc

    def acceptance(self, acceptance_id: str) -> AcceptedExecutionEvidence:
        with self._database.transaction() as connection:
            row = connection.execute(
                "SELECT payload_json FROM execution_evidence_acceptances WHERE acceptance_id=?",
                (acceptance_id,),
            ).fetchone()
        if row is None:
            raise ExecutionEvidenceAcceptanceNotFound(
                f"Execution evidence acceptance not found: {acceptance_id}"
            )
        return AcceptedExecutionEvidence.model_validate(json.loads(row["payload_json"]))

    @staticmethod
    def _checkpoint_descends_from(
        connection,
        *,
        output_checkpoint_id: str,
        input_checkpoint_id: str,
        attempt_id: str,
        assignment_id: str,
    ) -> bool:
        if output_checkpoint_id == input_checkpoint_id:
            return True
        current = output_checkpoint_id
        visited: set[str] = set()
        while current != input_checkpoint_id:
            if current in visited:
                return False
            visited.add(current)
            row = connection.execute(
                "SELECT parent_checkpoint_id, attempt_id, assignment_id FROM candidate_checkpoints WHERE checkpoint_id=?",
                (current,),
            ).fetchone()
            if row is None or row["parent_checkpoint_id"] is None:
                return False
            if row["attempt_id"] != attempt_id or row["assignment_id"] != assignment_id:
                return False
            current = row["parent_checkpoint_id"]
        return True
