"""One authored task and two original candidate Run identities."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, StrictBool, TypeAdapter, model_validator

from oh_goon.core.models.agent_providers import ResolvedNativeAgent
from oh_goon.core.models.base import ImmutableModel
from oh_goon.core.models.fleet_plan import MAX_AGENT_TASK_CHARS
from oh_goon.core.models.parallel_candidate_verification import ParallelCandidateVerifier
from oh_goon.core.models.run_resources import RunConcurrencyPolicy
from oh_goon.core.models.source_authority import RepositorySourceAuthority
from oh_goon.core.models.workspace import WorkspaceState
from oh_goon.core.product_identity import stable_id
from oh_goon.core.vocabulary import FleetRunOutcome, FleetRunStatus

ParallelWorkId = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")]
TaskDigest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
BaseCommit = Annotated[str, Field(pattern=r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")]
WorkText = Annotated[str, Field(min_length=1, max_length=MAX_AGENT_TASK_CHARS)]
ProfileName = Annotated[str, Field(min_length=1, max_length=160)]
_NATIVE_SELECTION = TypeAdapter(ResolvedNativeAgent)


def parallel_native_selection_id(selected: ResolvedNativeAgent) -> str:
    """Group intent binds selection; original admission binds physical backend identity."""
    payload = _NATIVE_SELECTION.dump_python(
        selected, mode="json", exclude={"backend_configuration_id"}
    )
    return stable_id("parallel-native-selection", payload)


class ParallelAgentWorkRequest(ImmutableModel):
    work_id: ParallelWorkId
    workspace: Path
    expected_base_revision: BaseCommit
    task: WorkText
    briefs: tuple[WorkText, WorkText]
    native_profiles: tuple[ProfileName, ProfileName]
    provider_catalog: Path | None = None
    capacity: RunConcurrencyPolicy
    notes_enabled: StrictBool
    verifier: ParallelCandidateVerifier

    @model_validator(mode="after")
    def validate_request(self) -> ParallelAgentWorkRequest:
        for path in (self.workspace, self.provider_catalog):
            if path is not None and (not path.is_absolute() or ".." in path.parts):
                raise ValueError("Parallel work paths must be canonical absolute paths")
        if self.briefs[0] == self.briefs[1]:
            raise ValueError("Parallel work requires distinct briefs")
        for index in (0, 1):
            task = self.child_task(index)
            if not self.task.strip() or not self.briefs[index].strip() or "\x00" in task:
                raise ValueError(
                    "Parallel work task and briefs must be nonblank and contain no NUL"
                )
            task.encode("utf-8")
            if len(task) > MAX_AGENT_TASK_CHARS:
                raise ValueError("Parallel work task, brief and acceptance exceed the Agent limit")
        return self

    def child_task(self, index: int) -> str:
        return f"{self.task}\n\nYour independent brief\n{self.briefs[index]}\n\nAcceptance criteria\n{self.verifier.acceptance}"


class ParallelAgentChild(ImmutableModel):
    fleet_run_id: str = Field(min_length=1, max_length=160)
    task_sha256: TaskDigest
    native_selection_id: str = Field(min_length=1, max_length=160)


class ParallelAgentWorkIntent(ImmutableModel):
    work_id: ParallelWorkId
    repository_id: str = Field(min_length=1, max_length=160)
    source: RepositorySourceAuthority
    base_revision: BaseCommit
    children: tuple[ParallelAgentChild, ParallelAgentChild]
    notes_enabled: StrictBool
    verifier_id: str = Field(min_length=1, max_length=160)

    @model_validator(mode="after")
    def require_distinct_children(self) -> ParallelAgentWorkIntent:
        if self.children[0].fleet_run_id == self.children[1].fleet_run_id:
            raise ValueError("Parallel work requires two distinct planned Run identities")
        return self


class ParallelAgentChildReadback(ImmutableModel):
    fleet_run_id: str
    dispatch: Literal["pending", "never_started", "unresolved", "admitted"]
    status: FleetRunStatus | None = None
    outcome: FleetRunOutcome | None = None
    refusal: str | None = None
    cancellation_requested: bool = False
    candidate_state: WorkspaceState | None = None
    source_state: WorkspaceState | None = None


class ParallelAgentWorkReadback(ImmutableModel):
    intent: ParallelAgentWorkIntent
    dispatch_closed: bool
    cancelled: bool
    children: tuple[ParallelAgentChildReadback, ...]
