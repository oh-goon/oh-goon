"""Publish a verified candidate through the original workspace and local Git owners."""

from __future__ import annotations

import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory

from pydantic import Field, model_validator

from oh_goon.core.errors import FleetPlanInvalid, ScheduleExecutionStale, StateConflict
from oh_goon.core.file_digest import sha256_file_with_size
from oh_goon.core.models.base import ImmutableModel
from oh_goon.core.models.branch_advance import (
    BranchAdvanceId,
    BranchAdvanceReceipt,
    BranchRunId,
    PreparedBranchAdvance,
    is_branch_round_run_id,
)
from oh_goon.core.models.parallel_agent_work import BaseCommit
from oh_goon.core.models.writable_schedule import is_scheduled_branch_advance_id
from oh_goon.core.product_identity import repository_id
from oh_goon.core.vocabulary import FleetRunOutcome
from oh_goon.engine.git import GitService
from oh_goon.integrations.parallel_branch_advance import (
    PreparedBranchAdvance as GitBranchAdvance,
)
from oh_goon.integrations.parallel_branch_advance import (
    prepare_branch_advance,
    publish_branch_advance,
    read_branch_tip,
    require_branch_checkout,
)
from oh_goon.runtime.branch_advances import BranchAdvanceStore
from oh_goon.runtime.database import LocalDatabase
from oh_goon.runtime.journal import Journal
from oh_goon.runtime.run_admission_lock import serialize_branch_handoff
from oh_goon.runtime.schedule_executions import ScheduleExecutionStore
from oh_goon.runtime.workspaces import RepositoryWorkspaceAuthority
from oh_goon.services.fleet_plan_candidate import workspace_control_git
from oh_goon.services.parallel_candidate_verification import prepare_verified_candidate_patch
from oh_goon.services.repositories import RepositoryService


class BranchAdvanceRequest(ImmutableModel):
    advance_id: BranchAdvanceId
    fleet_run_id: BranchRunId
    checkpoint_id: str = Field(min_length=1, max_length=160)
    branch_owner: Path
    workspace: Path
    branch_ref: str = Field(min_length=12, max_length=256, pattern=r"^refs/heads/")
    expected_tip: BaseCommit

    @model_validator(mode="after")
    def validate_paths(self) -> BranchAdvanceRequest:
        if any(
            not path.is_absolute() or ".." in path.parts
            for path in (self.branch_owner, self.workspace)
        ):
            raise ValueError("Branch advancement requires absolute canonical paths")
        if _overlap(self.branch_owner, self.workspace):
            raise ValueError("Select separate bare branch owner and receiving checkout")
        return self


def _overlap(first: Path, second: Path) -> bool:
    return first.is_relative_to(second) or second.is_relative_to(first)


def _require_receipt_request(receipt: BranchAdvanceReceipt, request_sha256: str) -> None:
    if receipt.preparation.request_sha256 != request_sha256:
        raise StateConflict("Branch advancement ID already binds a different request")


def advance_verified_candidate(
    request: BranchAdvanceRequest,
    *,
    database: LocalDatabase,
    repositories: RepositoryService,
    schedule_run_id: str | None = None,
    git: GitService | None = None,
) -> BranchAdvanceReceipt:
    """Record the verified proposal before publishing its exact native branch transition."""
    if is_scheduled_branch_advance_id(request.advance_id) != (schedule_run_id is not None):
        raise StateConflict("Scheduled branch advancement requires its original occurrence")

    logical_repository = repository_id(request.workspace)

    def require_scheduled_selection() -> None:
        if schedule_run_id is None:
            return
        executions = ScheduleExecutionStore(database)
        selection = executions.get(schedule_run_id).intent.writable_round
        if selection is None or selection.predecessor_run_id != request.fleet_run_id:
            raise ScheduleExecutionStale(
                "Branch advancement differs from the scheduled predecessor"
            )
        executions.require_pending_writable_round(
            schedule_run_id,
            repository_id=logical_repository,
            advance_id=request.advance_id,
            task_sha256=selection.task.prompt_sha256,
            native_selection_id=selection.native_selection_id,
        )

    require_scheduled_selection()
    request_sha256 = hashlib.sha256(request.model_dump_json().encode()).hexdigest()
    store = BranchAdvanceStore(database)
    existing = store.find(request.advance_id)
    if existing is not None:
        _require_receipt_request(existing, request_sha256)
        if existing.published_at is not None:
            return existing

    owner, workspace = request.branch_owner, request.workspace
    for path in (owner, workspace):
        if path.resolve(strict=True) != path or _overlap(path, database.path.parent):
            raise FleetPlanInvalid("Select canonical Git directories outside application state")
    if repositories.require_included(logical_repository).path != workspace:
        raise StateConflict("Branch advance requires its exact registered receiving checkout")
    git = workspace_control_git(git)
    with (
        RepositoryWorkspaceAuthority.pin_bare_branch_owner(owner) as (
            owner_subject,
            require_owner_current,
        ),
        RepositoryWorkspaceAuthority.pin_repository_subject(workspace) as require_checkout_current,
    ):
        checkout_identity = RepositoryWorkspaceAuthority.receiving_checkout_identity(workspace)
        with serialize_branch_handoff(database.path, checkout_identity):
            require_scheduled_selection()
            receipt = store.find(request.advance_id)
            if receipt is None:
                require_branch_checkout(
                    git,
                    checkout=workspace,
                    allowed_tips=(request.expected_tip,),
                    require_current=require_checkout_current,
                )
                detail = Journal(database).read_view().required_run_detail(request.fleet_run_id)
                if detail.outcome is not FleetRunOutcome.SUCCESS or len(detail.run.targets) != 1:
                    raise FleetPlanInvalid(
                        "Branch advancement requires an original successful candidate Run"
                    )
                original = detail.run.targets[0].path
                if _overlap(owner, original):
                    raise FleetPlanInvalid("Select a branch owner separate from the original Run")
                if is_branch_round_run_id(request.fleet_run_id):
                    predecessor = store.find_by_next_run(request.fleet_run_id)
                    if predecessor is None or predecessor.published_at is None:
                        raise StateConflict("Writable candidate has no published predecessor")
                    prior = predecessor.preparation
                    if (
                        prior.repository_path != workspace
                        or prior.checkout_identity != checkout_identity
                        or prior.branch_owner_path != owner
                        or prior.branch_ref != request.branch_ref
                        or prior.commit != request.expected_tip
                    ):
                        raise StateConflict(
                            "Writable candidate belongs to a different branch lineage"
                        )
                    RepositoryWorkspaceAuthority.require_bare_branch_owner_identity(
                        owner, prior.branch_owner_subject_id
                    )
                elif _overlap(workspace, original):
                    raise FleetPlanInvalid(
                        "Select a receiving checkout separate from the original Run"
                    )

                with TemporaryDirectory(prefix="oh-goon-branch-advance-") as directory:
                    scratch = Path(directory)
                    verified = prepare_verified_candidate_patch(
                        detail,
                        database=database,
                        checkpoint_id=request.checkpoint_id,
                        scratch=scratch,
                        git=git,
                    )
                    if verified.verification.base_revision != request.expected_tip:
                        raise StateConflict(
                            "Verified candidate base differs from the expected branch tip"
                        )
                    require_branch_checkout(
                        git,
                        checkout=workspace,
                        allowed_tips=(request.expected_tip,),
                        require_current=require_checkout_current,
                        scratch=scratch,
                    )
                    patch_sha256, _ = sha256_file_with_size(verified.patch.path)
                    proposed = prepare_branch_advance(
                        git,
                        repository=owner,
                        ref=request.branch_ref,
                        expected_tip=request.expected_tip,
                        patch=verified.patch.path,
                        patch_sha256=patch_sha256,
                        verified_tree=verified.patch.tree,
                        operation_id=request.advance_id,
                        scratch=scratch,
                        require_current=require_owner_current,
                    )
                    verified.require_current()
                    require_checkout_current()
                    receipt = store.prepare(
                        PreparedBranchAdvance(
                            advance_id=request.advance_id,
                            request_sha256=request_sha256,
                            repository_path=workspace,
                            repository_id=logical_repository,
                            checkout_identity=checkout_identity,
                            branch_owner_path=owner,
                            branch_owner_subject_id=owner_subject,
                            branch_ref=request.branch_ref,
                            expected_tip=request.expected_tip,
                            commit=proposed.commit,
                            tree=proposed.tree,
                            patch_sha256=patch_sha256,
                            verification=verified.verification,
                            source_byte_count=verified.source.byte_count,
                            source_entry_count=verified.source.entry_count,
                        )
                    )
            else:
                _require_receipt_request(receipt, request_sha256)
                if receipt.published_at is not None:
                    return receipt
            prepared = receipt.preparation
            if (
                prepared.checkout_identity
                != RepositoryWorkspaceAuthority.receiving_checkout_identity(workspace)
            ):
                raise StateConflict("Receiving checkout was replaced after branch preparation")
            RepositoryWorkspaceAuthority.require_bare_branch_owner_identity(
                owner, prepared.branch_owner_subject_id
            )
            require_branch_checkout(
                git,
                checkout=workspace,
                allowed_tips=(prepared.expected_tip, prepared.commit),
                require_current=require_checkout_current,
            )
            require_scheduled_selection()
            publish_branch_advance(
                git,
                repository=owner,
                prepared=GitBranchAdvance(
                    prepared.branch_ref, prepared.expected_tip, prepared.commit, prepared.tree
                ),
                require_current=require_owner_current,
            )
            observed_tip = read_branch_tip(
                git,
                repository=owner,
                ref=prepared.branch_ref,
                require_current=require_owner_current,
            )
            return store.confirm(prepared, observed_tip=observed_tip)
