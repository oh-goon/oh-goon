from __future__ import annotations

import json
import sqlite3

from oh_goon.core.errors import ExecutionAssignmentNotFound, ExecutionAssignmentStale
from oh_goon.core.models.described_work import ProducerNeutralWorkDescription
from oh_goon.core.models.execution_assignment import AttemptBudget, ExecutionAssignment
from oh_goon.core.models.recovery_lifecycle import RecoveryAdmission
from oh_goon.core.models.workspace import CandidateCheckpoint, WorkspaceRef
from oh_goon.core.product_identity import new_assignment_id, stable_id
from oh_goon.core.time import utc_now
from oh_goon.runtime.database import LocalDatabase
from oh_goon.runtime.workspaces import RepositoryWorkspaceAuthority


class ExecutionAssignmentAuthority:
    """Single durable owner that resolves immutable execution authority before effects."""

    def __init__(
        self, database: LocalDatabase, workspaces: RepositoryWorkspaceAuthority | None = None
    ) -> None:
        self._database = database
        self._workspaces = workspaces or RepositoryWorkspaceAuthority(database)

    def current_candidate_checkpoint(
        self, workspace: WorkspaceRef, *, reprove: bool = True
    ) -> CandidateCheckpoint:
        """Expose the exact current checkpoint for admission translators without authority leakage."""
        return self._workspaces.current_candidate_checkpoint(workspace, reprove=reprove)

    def issue_candidate_mutation(
        self,
        workspace: WorkspaceRef,
        *,
        attempt_id: str,
        expected_checkpoint_id: str,
        wall_time_seconds: int,
        max_processes: int | None = None,
        network_authority: str = "none",
        secret_grants: tuple[str, ...] = (),
        git_authority: str = "none",
        publish_authority: bool = False,
        tool_ids: tuple[str, ...] = (),
        delegation_limit: int = 0,
        isolation_requirements: tuple[str, ...] = ("private_filesystem",),
        input_artifact_ids: tuple[str, ...] = (),
    ) -> ExecutionAssignment:
        if publish_authority:
            raise ValueError("Candidate-mutation assignments cannot carry publish authority")
        checkpoint = self._workspaces.current_candidate_checkpoint(workspace, reprove=True)
        if checkpoint.checkpoint_id != expected_checkpoint_id:
            raise ExecutionAssignmentStale(
                "Execution assignment input checkpoint changed before authority resolution: "
                f"expected {expected_checkpoint_id}, observed {checkpoint.checkpoint_id}"
            )
        if not attempt_id:
            raise ValueError("Execution assignment requires attempt_id")
        budget = AttemptBudget(
            wall_time_seconds=wall_time_seconds,
            max_processes=max_processes,
        )
        assignment_id = new_assignment_id()
        created_at = utc_now()
        payload = {
            "attempt_id": attempt_id,
            "workspace_id": workspace.workspace_id,
            "input_checkpoint_id": checkpoint.checkpoint_id,
            "filesystem_authority": "candidate_mutation",
            "process_authority": "isolated",
            "network_authority": network_authority,
            "secret_grants": list(secret_grants),
            "git_authority": git_authority,
            "publish_authority": publish_authority,
            "tool_ids": list(tool_ids),
            "delegation_limit": delegation_limit,
            "isolation_requirements": list(isolation_requirements),
            "input_artifact_ids": list(input_artifact_ids),
            "attempt_budget": budget.model_dump(mode="json", exclude_computed_fields=True),
        }
        fingerprint = stable_id("execution-assignment", payload)
        assignment = ExecutionAssignment.model_validate(
            {
                "assignment_id": assignment_id,
                "attempt_id": attempt_id,
                "workspace_id": workspace.workspace_id,
                "input_checkpoint_id": checkpoint.checkpoint_id,
                "filesystem_authority": "candidate_mutation",
                "process_authority": "isolated",
                "network_authority": network_authority,
                "secret_grants": secret_grants,
                "git_authority": git_authority,
                "publish_authority": publish_authority,
                "tool_ids": tool_ids,
                "delegation_limit": delegation_limit,
                "isolation_requirements": isolation_requirements,
                "input_artifact_ids": input_artifact_ids,
                "attempt_budget": budget,
                "assignment_fingerprint": fingerprint,
                "created_at": created_at,
            }
        )
        with self._database.transaction(write=True) as connection:
            try:
                connection.execute(
                    """
                    INSERT INTO execution_assignments(
                        assignment_id, attempt_id, workspace_id, input_checkpoint_id,
                        assignment_fingerprint, payload_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        assignment.assignment_id,
                        assignment.attempt_id,
                        assignment.workspace_id,
                        assignment.input_checkpoint_id,
                        assignment.assignment_fingerprint,
                        json.dumps(
                            assignment.model_dump(mode="json", exclude_computed_fields=True),
                            sort_keys=True,
                            separators=(",", ":"),
                        ),
                        assignment.created_at.isoformat(),
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise ExecutionAssignmentStale(
                    "Execution assignment could not be durably bound"
                ) from exc
        return assignment

    def issue_described_work_candidate_mutation(
        self,
        workspace: WorkspaceRef,
        *,
        work: ProducerNeutralWorkDescription,
        attempt_id: str,
        expected_checkpoint_id: str,
        wall_time_seconds: int,
        max_processes: int | None = None,
        network_authority: str = "none",
        secret_grants: tuple[str, ...] = (),
        git_authority: str = "none",
        tool_ids: tuple[str, ...] = (),
        delegation_limit: int = 0,
        isolation_requirements: tuple[str, ...] = ("private_filesystem",),
    ) -> ExecutionAssignment:
        """Resolve explicit Oh-Goon authority for verified producer-neutral described work."""
        checkpoint = self.current_candidate_checkpoint(workspace, reprove=True)
        if checkpoint.checkpoint_id != expected_checkpoint_id:
            raise ExecutionAssignmentStale(
                "Described-work checkpoint changed before authority resolution"
            )
        if checkpoint.source_sha256 != work.repository_generation_identity.removeprefix("sha256:"):
            raise ExecutionAssignmentStale(
                "Described-work repository generation does not match current checkpoint"
            )
        return self.issue_candidate_mutation(
            workspace,
            attempt_id=attempt_id,
            expected_checkpoint_id=expected_checkpoint_id,
            wall_time_seconds=wall_time_seconds,
            max_processes=max_processes,
            network_authority=network_authority,
            secret_grants=secret_grants,
            git_authority=git_authority,
            publish_authority=False,
            tool_ids=tool_ids,
            delegation_limit=delegation_limit,
            isolation_requirements=isolation_requirements,
            input_artifact_ids=work.immutable_inputs(),
        )

    def issue_recovery_candidate_mutation(
        self,
        workspace: WorkspaceRef,
        *,
        previous_assignment: ExecutionAssignment,
        recovery: RecoveryAdmission,
        attempt_id: str,
    ) -> ExecutionAssignment:
        """Issue one fresh assignment only for an explicitly admitted bounded recovery."""
        if recovery.disposition != "ADMITTED":
            raise ValueError("Blocked recovery cannot receive execution authority")
        if recovery.parent_assignment_id != previous_assignment.assignment_id:
            raise ValueError("Recovery admission does not name the previous assignment")
        if recovery.workspace_id != workspace.workspace_id:
            raise ValueError("Recovery admission belongs to another workspace")
        if recovery.checkpoint_id != previous_assignment.input_checkpoint_id:
            raise ValueError("Recovery admission crossed candidate checkpoint identity")
        return self.issue_candidate_mutation(
            workspace,
            attempt_id=attempt_id,
            expected_checkpoint_id=recovery.checkpoint_id,
            wall_time_seconds=previous_assignment.attempt_budget.wall_time_seconds,
            max_processes=previous_assignment.attempt_budget.max_processes,
            network_authority=previous_assignment.network_authority,
            secret_grants=previous_assignment.secret_grants,
            git_authority=previous_assignment.git_authority,
            publish_authority=False,
            tool_ids=previous_assignment.tool_ids,
            delegation_limit=previous_assignment.delegation_limit,
            isolation_requirements=previous_assignment.isolation_requirements,
            input_artifact_ids=(*previous_assignment.input_artifact_ids, recovery.admission_id),
        )

    def issue_protected_effect(
        self,
        workspace: WorkspaceRef,
        *,
        attempt_id: str,
        expected_checkpoint_id: str,
        proof_artifact_id: str,
        wall_time_seconds: int,
        secret_grants: tuple[str, ...] = (),
        git_authority: str = "none",
        tool_ids: tuple[str, ...] = (),
        isolation_requirements: tuple[str, ...] = ("private_filesystem",),
    ) -> ExecutionAssignment:
        checkpoint = self._workspaces.current_candidate_checkpoint(workspace, reprove=True)
        if checkpoint.checkpoint_id != expected_checkpoint_id:
            raise ExecutionAssignmentStale(
                "Protected-effect checkpoint changed before authority resolution: "
                f"expected {expected_checkpoint_id}, observed {checkpoint.checkpoint_id}"
            )
        if not attempt_id or not proof_artifact_id:
            raise ValueError(
                "Protected-effect assignment requires attempt_id and exact proof artifact"
            )
        budget = AttemptBudget(wall_time_seconds=wall_time_seconds)
        assignment_id = new_assignment_id()
        created_at = utc_now()
        payload = {
            "attempt_id": attempt_id,
            "workspace_id": workspace.workspace_id,
            "input_checkpoint_id": checkpoint.checkpoint_id,
            "filesystem_authority": "read_only",
            "process_authority": "isolated",
            "network_authority": "declared",
            "secret_grants": list(secret_grants),
            "git_authority": git_authority,
            "publish_authority": True,
            "tool_ids": list(tool_ids),
            "delegation_limit": 0,
            "isolation_requirements": list(isolation_requirements),
            "input_artifact_ids": [proof_artifact_id],
            "attempt_budget": budget.model_dump(mode="json", exclude_computed_fields=True),
        }
        assignment = ExecutionAssignment.model_validate(
            {
                "assignment_id": assignment_id,
                "attempt_id": attempt_id,
                "workspace_id": workspace.workspace_id,
                "input_checkpoint_id": checkpoint.checkpoint_id,
                "filesystem_authority": "read_only",
                "process_authority": "isolated",
                "network_authority": "declared",
                "secret_grants": secret_grants,
                "git_authority": git_authority,
                "publish_authority": True,
                "tool_ids": tool_ids,
                "delegation_limit": 0,
                "isolation_requirements": isolation_requirements,
                "input_artifact_ids": (proof_artifact_id,),
                "attempt_budget": budget,
                "assignment_fingerprint": stable_id("execution-assignment", payload),
                "created_at": created_at,
            }
        )
        with self._database.transaction(write=True) as connection:
            connection.execute(
                """INSERT INTO execution_assignments(assignment_id, attempt_id, workspace_id, input_checkpoint_id, assignment_fingerprint, payload_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    assignment.assignment_id,
                    assignment.attempt_id,
                    assignment.workspace_id,
                    assignment.input_checkpoint_id,
                    assignment.assignment_fingerprint,
                    json.dumps(
                        assignment.model_dump(mode="json", exclude_computed_fields=True),
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                    assignment.created_at.isoformat(),
                ),
            )
        return assignment

    def assignment(self, assignment_id: str) -> ExecutionAssignment:
        with self._database.transaction() as connection:
            row = connection.execute(
                "SELECT payload_json FROM execution_assignments WHERE assignment_id=?",
                (assignment_id,),
            ).fetchone()
        if row is None:
            raise ExecutionAssignmentNotFound(f"Execution assignment not found: {assignment_id}")
        return ExecutionAssignment.model_validate(json.loads(row["payload_json"]))
