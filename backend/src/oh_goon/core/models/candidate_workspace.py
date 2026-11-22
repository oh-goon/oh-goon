"""Read-only current workspace observations anchored to an original candidate Run."""

from __future__ import annotations

from typing import Literal

from oh_goon.core.models.base import ImmutableModel
from oh_goon.core.models.candidate_execution import CandidateWorkspaceAdmission
from oh_goon.core.models.candidate_execution_receipt import CandidateExecutionReceipt
from oh_goon.core.models.workspace import CandidateCheckpoint, WorkspaceState


class CandidateRunInspection(ImmutableModel):
    fleet_run_id: str
    admission: CandidateWorkspaceAdmission
    state: WorkspaceState
    source_state: WorkspaceState
    checkpoint: CandidateCheckpoint | None
    receipt: CandidateExecutionReceipt | None
    path: str | None
    path_status: Literal["resolved", "unavailable"]
    checkpoint_freshness: Literal["recorded_not_reproved"] = "recorded_not_reproved"
