"""Original claimed-effect preparation port for an owned candidate workspace."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from oh_goon.core.models.candidate_execution import CandidateWorkspaceAdmission
from oh_goon.core.models.candidate_execution_receipt import CandidateExecutionReceipt
from oh_goon.core.models.execution_materialization import ExecutionMaterialization
from oh_goon.core.models.fleet_plan_evidence import FleetPlanResult
from oh_goon.core.models.provenance import RepositoryStateFingerprint
from oh_goon.core.ports.capability_runner import CapabilityExecutionRequest


class CandidateExecutionContext(Protocol):
    """One actual materialization owned until original operation settlement."""

    @property
    def path(self) -> Path: ...

    @property
    def materialization(self) -> ExecutionMaterialization: ...

    @property
    def workspace_directory_fd(self) -> int: ...

    def require_current(self) -> None: ...

    def mark_writer_started(self) -> None: ...

    def settle(self, result: FleetPlanResult) -> CandidateExecutionReceipt: ...

    def observe_subject(self) -> RepositoryStateFingerprint: ...

    def close(self) -> None: ...


CandidateExecutionFactory = Callable[
    [CandidateWorkspaceAdmission, CapabilityExecutionRequest], CandidateExecutionContext
]
