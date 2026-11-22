"""Original workspace settlement facts carried by a canonical Run operation."""

from __future__ import annotations

from typing import Literal

from pydantic import model_validator

from oh_goon.core.models.base import ImmutableModel
from oh_goon.core.models.execution_assignment import ExecutionAssignment
from oh_goon.core.models.execution_evidence_acceptance import AcceptedExecutionEvidence
from oh_goon.core.models.execution_materialization import ExecutionMaterialization
from oh_goon.core.models.workspace import CandidateCheckpoint, WorkspaceRef, WorkspaceState
from oh_goon.core.models.workspace_lease import WorkspaceLease


class CandidateExecutionReceipt(ImmutableModel):
    """Describe settlement; model text and a historical receipt grant no effects."""

    workspace: WorkspaceRef
    state: WorkspaceState
    assignment: ExecutionAssignment
    lease: WorkspaceLease
    materialization: ExecutionMaterialization
    checkpoint: CandidateCheckpoint | None = None
    acceptance: AcceptedExecutionEvidence | None = None
    settlement: Literal["checkpointed", "retained_incomplete"]
    source_cleanup: Literal["retained", "verified", "unverified"] = "retained"
    reason_code: str | None = None

    @model_validator(mode="after")
    def validate_lineage(self) -> CandidateExecutionReceipt:
        if (
            self.state.workspace_id != self.workspace.workspace_id
            or self.assignment.workspace_id != self.workspace.workspace_id
            or self.lease.workspace_id != self.workspace.workspace_id
            or self.materialization.workspace_id != self.workspace.workspace_id
            or self.lease.assignment_id != self.assignment.assignment_id
            or self.lease.assignment_fingerprint != self.assignment.assignment_fingerprint
            or self.materialization.assignment_id != self.assignment.assignment_id
            or self.materialization.assignment_fingerprint != self.assignment.assignment_fingerprint
            or self.materialization.lease_id != self.lease.lease_id
            or self.materialization.lease_generation != self.lease.lease_generation
            or self.materialization.executor_id != self.lease.executor_id
            or self.materialization.executor_generation != self.lease.executor_generation
            or self.materialization.input_checkpoint_id != self.assignment.input_checkpoint_id
            or self.lease.input_checkpoint_id != self.assignment.input_checkpoint_id
        ):
            raise ValueError("Candidate receipt differs from its original materialization")
        if self.checkpoint is not None and (
            self.checkpoint.workspace_id != self.workspace.workspace_id
            or self.checkpoint.assignment_id != self.assignment.assignment_id
            or self.checkpoint.attempt_id != self.assignment.attempt_id
        ):
            raise ValueError("Candidate receipt checkpoint differs from its writer")
        if self.settlement == "checkpointed" and (
            self.checkpoint is None
            or self.acceptance is None
            or self.state.current_lease_id is not None
            or self.state.current_checkpoint_id != self.checkpoint.checkpoint_id
        ):
            raise ValueError("Checkpointed settlement requires accepted original evidence")
        if self.acceptance is not None and (
            self.checkpoint is None
            or self.acceptance.workspace_id != self.workspace.workspace_id
            or self.acceptance.assignment_id != self.assignment.assignment_id
            or self.acceptance.assignment_fingerprint != self.assignment.assignment_fingerprint
            or self.acceptance.attempt_id != self.assignment.attempt_id
            or self.acceptance.lease_id != self.lease.lease_id
            or self.acceptance.lease_generation != self.lease.lease_generation
            or self.acceptance.executor_id != self.lease.executor_id
            or self.acceptance.executor_generation != self.lease.executor_generation
            or self.acceptance.materialization_id != self.materialization.materialization_id
            or self.acceptance.materialization_fingerprint
            != self.materialization.resolved_fingerprint
            or self.acceptance.input_checkpoint_id != self.assignment.input_checkpoint_id
            or self.acceptance.output_checkpoint_id != self.checkpoint.checkpoint_id
        ):
            raise ValueError("Candidate receipt acceptance differs from its settled authority")
        return self
