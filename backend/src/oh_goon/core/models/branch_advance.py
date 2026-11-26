"""Receipt for a verified candidate published by native Git."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Annotated

from pydantic import AwareDatetime, Field, model_validator

from oh_goon.core.models.agent_providers import ResolvedNativeAgent
from oh_goon.core.models.base import ImmutableModel
from oh_goon.core.models.parallel_agent_work import BaseCommit, parallel_native_selection_id
from oh_goon.core.models.parallel_candidate_verification import ParallelCandidateVerification
from oh_goon.core.product_identity import stable_id

BranchAdvanceId = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")]
BranchRunId = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,159}$")]
BranchRequestDigest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
BRANCH_ROUND_RUN_PREFIX = "fleet-branch-round-"


def branch_round_run_id(advance_id: str) -> str:
    return stable_id(BRANCH_ROUND_RUN_PREFIX.removesuffix("-"), advance_id)


def is_branch_round_run_id(fleet_run_id: str) -> bool:
    return (
        fleet_run_id.startswith(BRANCH_ROUND_RUN_PREFIX)
        and re.fullmatch(r"[0-9a-f]{64}", fleet_run_id.removeprefix(BRANCH_ROUND_RUN_PREFIX))
        is not None
    )


def writable_round_request_sha256(
    *, selected: ResolvedNativeAgent, skill: Path | None, task: str
) -> str:
    payload = {
        "native_selection_id": parallel_native_selection_id(selected),
        "skill": str(skill) if skill is not None else None,
        "task_sha256": hashlib.sha256(task.encode("utf-8")).hexdigest(),
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


class PreparedBranchAdvance(ImmutableModel):
    """The exact predecessor and proposed Git result before ref publication."""

    advance_id: BranchAdvanceId
    request_sha256: BranchRequestDigest
    repository_path: Path
    repository_id: str = Field(min_length=1, max_length=160)
    checkout_identity: str = Field(min_length=1, max_length=160)
    branch_owner_path: Path
    branch_owner_subject_id: str = Field(min_length=1, max_length=160)
    branch_ref: str = Field(min_length=12, max_length=1024)
    expected_tip: BaseCommit
    commit: BaseCommit
    tree: BaseCommit
    patch_sha256: BranchRequestDigest
    verification: ParallelCandidateVerification
    source_byte_count: int = Field(ge=0)
    source_entry_count: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_binding(self) -> PreparedBranchAdvance:
        if (
            any(
                not path.is_absolute() or ".." in path.parts
                for path in (self.repository_path, self.branch_owner_path)
            )
            or self.repository_path == self.branch_owner_path
        ):
            raise ValueError(
                "Branch advancement requires distinct absolute owner and checkout paths"
            )
        if not self.branch_ref.startswith("refs/heads/") or any(
            ord(char) <= 32 for char in self.branch_ref
        ):
            raise ValueError("Branch advancement requires an exact local heads ref")
        if len({len(self.expected_tip), len(self.commit), len(self.tree)}) != 1:
            raise ValueError("Branch result and predecessor must use the same Git object format")
        verified = self.verification
        if (
            self.commit == self.expected_tip
            or verified.base_revision != self.expected_tip
            or verified.outcome != "PASS"
            or verified.native_outcome != "PASS"
            or not verified.checkpoint_id
            or not verified.result_identity
            or not verified.physical_execution_id
            or not verified.source_sha256
        ):
            raise ValueError("Branch advancement requires exact settled candidate verification")
        return self


class NextBranchRunRequest(ImmutableModel):
    fleet_run_id: BranchRunId
    request_sha256: BranchRequestDigest
    repository_subject_id: str | None = Field(default=None, min_length=1, max_length=160)


class BranchAdvanceReceipt(ImmutableModel):
    preparation: PreparedBranchAdvance
    published_at: AwareDatetime | None = None
    next_run: NextBranchRunRequest | None = None

    @model_validator(mode="after")
    def validate_successor(self) -> BranchAdvanceReceipt:
        if self.next_run is not None and (
            self.published_at is None
            or self.next_run.fleet_run_id != branch_round_run_id(self.preparation.advance_id)
            or self.next_run.fleet_run_id == self.preparation.verification.fleet_run_id
        ):
            raise ValueError("Only a published advance can bind a distinct successor Run")
        return self
