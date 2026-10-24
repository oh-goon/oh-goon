from __future__ import annotations

from pydantic import AwareDatetime, Field, computed_field, model_validator

from oh_goon.core.models.base import ImmutableModel
from oh_goon.core.product_identity import new_operation_id, stable_id
from oh_goon.core.time import utc_now
from oh_goon.core.vocabulary import (
    GitBranchState,
    WorktreeState,
)


class StrictProvenanceModel(ImmutableModel):
    pass


class RepositoryStateFingerprint(StrictProvenanceModel):
    captured_at: AwareDatetime
    head_sha: str | None = None
    branch_state: GitBranchState
    branch: str | None = None
    worktree_state: WorktreeState
    pyproject_sha256: str | None = None
    uv_lock_sha256: str | None = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def state_id(self) -> str:
        return stable_id(
            "state",
            {
                "head_sha": self.head_sha,
                "branch_state": self.branch_state.value,
                "branch": self.branch,
                "worktree_state": self.worktree_state.value,
                "pyproject_sha256": self.pyproject_sha256,
                "uv_lock_sha256": self.uv_lock_sha256,
            },
        )


class OperationProvenance(StrictProvenanceModel):
    operation_id: str
    fleet_run_id: str | None = None
    started_at: AwareDatetime
    finished_at: AwareDatetime
    before: RepositoryStateFingerprint | None = None
    after: RepositoryStateFingerprint | None = None

    @model_validator(mode="after")
    def validate_time_order(self) -> OperationProvenance:
        if self.finished_at < self.started_at:
            raise ValueError("Operation finished_at cannot be before started_at")
        states = [state for state in (self.before, self.after) if state is not None]
        if any(
            state.captured_at < self.started_at or state.captured_at > self.finished_at
            for state in states
        ):
            raise ValueError(
                "Repository fingerprints must be captured within the operation interval"
            )
        if self.before is not None and self.after is not None:
            if self.after.captured_at < self.before.captured_at:
                raise ValueError("Operation after fingerprint cannot precede before fingerprint")
        return self


class FleetSnapshotProvenance(StrictProvenanceModel):
    """Identity and revision cut for one derived Fleet snapshot.

    Source operations, Fleet runs, policies, summaries, and attention are already present
    in the snapshot itself and must not be mirrored here.
    """

    snapshot_id: str
    generated_at: AwareDatetime
    durable_revision: int = Field(ge=0)
    journal_revision: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_revision_lineage(self) -> FleetSnapshotProvenance:
        if self.journal_revision > self.durable_revision:
            raise ValueError("Snapshot journal revision cannot exceed durable state revision")
        return self


def create_operation_provenance(
    *,
    fleet_run_id: str | None = None,
    operation_id: str | None = None,
) -> OperationProvenance:
    now = utc_now()
    return OperationProvenance(
        operation_id=operation_id or new_operation_id(),
        fleet_run_id=fleet_run_id,
        started_at=now,
        finished_at=now,
        before=None,
        after=None,
    )
