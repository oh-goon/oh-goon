"""Admit one writable candidate after its exact verified branch advancement."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING

from oh_goon.core.errors import FleetPlanInvalid, RunNotFound, StateConflict
from oh_goon.core.models.agent_providers import PiSessionIntent, ResolvedNativeAgent
from oh_goon.core.models.branch_advance import (
    BranchAdvanceReceipt,
    PreparedBranchAdvance,
    branch_round_run_id,
)
from oh_goon.core.models.fleet_plan import ResolvedAgentStep, ResolvedNativeAgentWorkload
from oh_goon.core.models.fleet_plan_evidence import PlanSnapshotEvidence
from oh_goon.core.models.parallel_agent_work import parallel_native_selection_id
from oh_goon.core.models.parallel_candidate_verification import ParallelCandidateVerifier
from oh_goon.core.models.source_authority import RepositorySourceAuthority
from oh_goon.core.models.target import FleetPlanTarget
from oh_goon.core.product_identity import repository_subject_instance_id
from oh_goon.integrations.content_snapshot import SnapshotIdentity
from oh_goon.integrations.parallel_branch_advance import (
    PreparedBranchAdvance as PreparedGitBranchAdvance,
)
from oh_goon.integrations.parallel_branch_advance import sync_branch_checkout
from oh_goon.integrations.parallel_candidate_verifier import require_verifier_executable
from oh_goon.runtime.branch_advances import BranchAdvanceStore
from oh_goon.runtime.parallel_agent_work import read_parallel_verifier
from oh_goon.runtime.workspaces import RepositoryWorkspaceAuthority
from oh_goon.services.fleet_plan_candidate import (
    require_native_candidate_selection,
    workspace_control_git,
)

if TYPE_CHECKING:
    from oh_goon.core.models.run import FleetRunState
    from oh_goon.runtime.database import LocalDatabase
    from oh_goon.services.fleet_plans import FleetPlanService


def _published(store: BranchAdvanceStore, advance_id: str) -> BranchAdvanceReceipt:
    receipt = store.find(advance_id)
    if receipt is None or receipt.published_at is None:
        raise StateConflict("Next Run requires its published branch advancement")
    return receipt


def _request_sha256(*, selected: ResolvedNativeAgent, skill: Path | None, task: str) -> str:
    payload = {
        "native_selection_id": parallel_native_selection_id(selected),
        "skill": str(skill) if skill is not None else None,
        "task_sha256": hashlib.sha256(task.encode("utf-8")).hexdigest(),
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _snapshot(prepared: PreparedBranchAdvance) -> PlanSnapshotEvidence:
    sha256 = prepared.verification.source_sha256
    if sha256 is None:
        raise StateConflict("Branch advancement has no original verified source identity")
    return PlanSnapshotEvidence(
        sha256=sha256,
        byte_count=prepared.source_byte_count,
        entry_count=prepared.source_entry_count,
    )


def _verifier(
    database: LocalDatabase, prepared: PreparedBranchAdvance
) -> ParallelCandidateVerifier:
    verifier = read_parallel_verifier(database, prepared.verification.parallel_work_id)
    if verifier.identity != prepared.verification.verifier_id:
        raise StateConflict("Next Run verifier differs from the branch advancement")
    require_verifier_executable(verifier)
    return verifier


def require_writable_round_source(
    service: FleetPlanService,
    *,
    advance_id: str,
    fleet_run_id: str,
    selected: ResolvedNativeAgent,
    workspace: Path,
    skill: Path | None,
    task: str,
) -> tuple[PreparedBranchAdvance, RepositorySourceAuthority, ParallelCandidateVerifier]:
    """Bind reserved Run ingress to the durable request before candidate allocation."""
    database = service._workspace_database
    if database is None:
        raise FleetPlanInvalid("Next Run requires the original workspace database")
    receipt = _published(BranchAdvanceStore(database), advance_id)
    prepared = receipt.preparation
    next_run = receipt.next_run
    if (
        next_run is None
        or next_run.repository_subject_id is None
        or next_run.fleet_run_id != fleet_run_id
        or fleet_run_id != branch_round_run_id(advance_id)
        or next_run.request_sha256 != _request_sha256(selected=selected, skill=skill, task=task)
        or prepared.repository_path != workspace
        or selected.session != PiSessionIntent(mode="ephemeral")
    ):
        raise StateConflict("Next Run differs from its published branch and bound request")
    source = RepositorySourceAuthority.create(
        subject_instance_id=next_run.repository_subject_id,
        source_snapshot=_snapshot(prepared),
    )
    return prepared, source, _verifier(database, prepared)


def start_writable_round(
    service: FleetPlanService,
    *,
    advance_id: str,
    selected: ResolvedNativeAgent,
    workspace: Path,
    skill: Path | None,
    task: str,
) -> FleetRunState:
    """Synchronize an explicitly selected checkout and use original Fleet admission once."""
    from oh_goon.services.fleet_plans import require_native_task

    database, repositories = service._workspace_database, service._repositories
    if not service._standard_local_fleet or database is None or repositories is None:
        raise FleetPlanInvalid("Next Run requires its original ordinary Fleet workspace owners")
    if selected.uses_persistent_pi_session:
        raise FleetPlanInvalid("Writable branch rounds require a fresh ephemeral Pi session")
    selected = replace(selected, session=PiSessionIntent(mode="ephemeral"))
    require_native_candidate_selection(selected)
    require_native_task(task)
    if skill is not None and (not skill.is_absolute() or ".." in skill.parts):
        raise FleetPlanInvalid("Select an absolute Skill path for the next Run")

    store = BranchAdvanceStore(database)
    fleet_run_id = branch_round_run_id(advance_id)
    with store.lock_next_request(advance_id):
        prepared = _published(store, advance_id).preparation
        if workspace != prepared.repository_path:
            raise StateConflict("Next Run requires the branch advancement's selected checkout")
        repository = repositories.require_included(prepared.repository_id)
        if repository.path != workspace:
            raise StateConflict("Next Run requires the exact registered checkout")
        request_sha256 = _request_sha256(selected=selected, skill=skill, task=task)
        try:
            existing = service._runs.get(fleet_run_id).run
        except RunNotFound:
            existing = None
        if existing is not None:
            next_run = _published(store, advance_id).next_run
            target = existing.target
            if (
                next_run is None
                or next_run.repository_subject_id is None
                or next_run.fleet_run_id != fleet_run_id
                or next_run.request_sha256 != request_sha256
                or not isinstance(target, FleetPlanTarget)
                or target.candidate_workspace is None
                or target.candidate_workspace.fleet_run_id != fleet_run_id
                or target.candidate_workspace.workspace.base_revision != prepared.commit
                or target.candidate_workspace.source
                != RepositorySourceAuthority.create(
                    subject_instance_id=next_run.repository_subject_id,
                    source_snapshot=_snapshot(prepared),
                )
                or tuple(item.repository_id for item in existing.targets)
                != (prepared.repository_id,)
                or len(target.plan.steps) != 3
                or not isinstance(step := target.plan.steps[1], ResolvedAgentStep)
                or not isinstance(step.workload, ResolvedNativeAgentWorkload)
                or step.task_sha256 != hashlib.sha256(task.encode("utf-8")).hexdigest()
                or parallel_native_selection_id(step.workload.native_agent)
                != parallel_native_selection_id(selected)
            ):
                raise StateConflict("Next Run identity is already bound to different intent")
            target.require_candidate_native_intent()
            return existing

        service.require_plan_ingress_preparation(fleet_run_id)
        if skill is not None:
            selected_skill = skill.resolve(strict=True)
            marker = selected_skill / "SKILL.md" if selected_skill.is_dir() else selected_skill
            if marker.name != "SKILL.md" or not marker.is_file():
                raise FleetPlanInvalid("Select the canonical SKILL.md or its directory")
        snapshot = _snapshot(prepared)
        _verifier(database, prepared)
        with (
            RepositoryWorkspaceAuthority.pin_repository_subject(
                prepared.branch_owner_path
            ) as require_owner_current,
            RepositoryWorkspaceAuthority.pin_repository_subject(
                workspace
            ) as require_checkout_current,
            TemporaryDirectory(prefix="oh-goon-branch-round-") as temporary,
        ):
            if (
                repository_subject_instance_id(prepared.branch_owner_path)
                != prepared.branch_owner_subject_id
                or RepositoryWorkspaceAuthority.receiving_checkout_identity(workspace)
                != prepared.checkout_identity
            ):
                raise StateConflict("Branch advancement's original repository subject changed")
            store.bind_next_request(
                advance_id, request_sha256=request_sha256, fleet_run_id=fleet_run_id
            )
            sync_branch_checkout(
                workspace_control_git(service._git_service),
                repository=prepared.branch_owner_path,
                checkout=workspace,
                prepared=PreparedGitBranchAdvance(
                    prepared.branch_ref, prepared.expected_tip, prepared.commit, prepared.tree
                ),
                candidate_identity=SnapshotIdentity(
                    snapshot.sha256, snapshot.byte_count, snapshot.entry_count
                ),
                scratch=Path(temporary),
                require_current=require_owner_current,
                require_checkout_current=require_checkout_current,
            )
            require_checkout_current()
            store.bind_next_source(
                advance_id,
                repository_subject_id=repository_subject_instance_id(workspace),
                fleet_run_id=fleet_run_id,
                request_sha256=request_sha256,
            )
            return service.run_native_agent(
                selected=selected,
                workspace=workspace,
                skill=skill,
                task=task,
                fleet_run_id=fleet_run_id,
                candidate=True,
                branch_advance_id=advance_id,
            )
