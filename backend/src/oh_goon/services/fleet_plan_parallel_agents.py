"""Bind one explicit task to two independent original candidate Runs."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import TYPE_CHECKING

from oh_goon.core.errors import FleetDomainError, FleetPlanInvalid, StateConflict
from oh_goon.core.models.agent_providers import ResolvedNativeAgent
from oh_goon.core.models.agent_source import MAX_REPOSITORY_BYTES, MAX_SOURCE_ENTRIES
from oh_goon.core.models.fleet_plan_evidence import PlanSnapshotEvidence
from oh_goon.core.models.parallel_agent_work import (
    ParallelAgentChild,
    ParallelAgentWorkIntent,
    ParallelAgentWorkReadback,
    ParallelAgentWorkRequest,
    parallel_native_selection_id,
)
from oh_goon.core.models.source_authority import RepositorySourceAuthority
from oh_goon.core.product_identity import (
    repository_id,
    repository_subject_instance_id,
    stable_id,
)
from oh_goon.integrations.agent_native_execution import native_executable_sha256
from oh_goon.integrations.agent_provider_client import require_provider_key_file_separation
from oh_goon.integrations.source_snapshot import source_snapshot_identity
from oh_goon.runtime.parallel_agent_work import (
    begin_parallel_work,
    close_parallel_work,
    read_parallel_work,
    record_child_refusal,
    replay_parallel_work,
)
from oh_goon.runtime.workspaces import RepositoryWorkspaceAuthority
from oh_goon.services.agent_authoring_sources import require_native_agent_source_path_privacy
from oh_goon.services.fleet_plan_candidate import (
    require_native_candidate_selection,
    workspace_control_git,
)

if TYPE_CHECKING:
    from oh_goon.services.fleet_plans import FleetPlanService


def parallel_request_hash(request: ParallelAgentWorkRequest) -> str:
    return stable_id("parallel-agent-request", request.model_dump(mode="json"))


def start_parallel_agent_work(
    service: FleetPlanService,
    request: ParallelAgentWorkRequest,
    *,
    selected: tuple[ResolvedNativeAgent, ResolvedNativeAgent],
) -> ParallelAgentWorkReadback:
    database = service._workspace_database
    if database is None:
        raise FleetPlanInvalid("Parallel work requires the original workspace database")
    request_hash = parallel_request_hash(request)
    replay = replay_parallel_work(database, request.work_id, request_hash)
    if replay is not None:
        return replay
    if (
        not service._standard_local_fleet
        or service._run_concurrency != request.capacity
        or service._repositories is None
        or tuple(agent.profile_name for agent in selected) != request.native_profiles
    ):
        raise FleetPlanInvalid(
            "Parallel work requires its exact admitted two-worker runtime and profiles"
        )
    for agent in selected:
        require_native_candidate_selection(agent)
        if any(value is not None for value in agent.profile.resources.model_dump().values()):
            raise FleetPlanInvalid("Parallel work uses the original per-worker resource scope")
    executable = Path(request.verifier.executable)
    if native_executable_sha256(executable) != request.verifier.executable_sha256:
        raise StateConflict("The operator-selected verifier executable has changed")

    child_ids = (f"{request.work_id}-1", f"{request.work_id}-2")
    service.require_plan_ingress_preparation(child_ids[0])
    workspace = request.workspace.resolve(strict=True)
    if workspace != request.workspace:
        raise FleetPlanInvalid("Parallel work requires the canonical registered repository path")
    for agent in selected:
        require_provider_key_file_separation(agent.connection, (workspace,))
    logical_repository_id = repository_id(workspace)
    registered = service._repositories.require_included(logical_repository_id)
    if registered.path != workspace:
        raise FleetPlanInvalid("Parallel work requires the exact registered repository")
    git = workspace_control_git(service._git_service)
    with RepositoryWorkspaceAuthority.pin_repository_subject(workspace) as require_current:
        if git.root(workspace) != workspace:
            raise FleetPlanInvalid("Parallel work requires its registered canonical Git root")
        observation = git.observe(workspace)
        if observation.head_sha != request.expected_base_revision or observation.changed_files:
            raise StateConflict("Parallel work requires the expected clean registered Git base")
        subject = repository_subject_instance_id(workspace)
        snapshot = source_snapshot_identity(
            workspace, source_path_validator=require_native_agent_source_path_privacy
        )
        if snapshot.byte_count > MAX_REPOSITORY_BYTES or snapshot.entry_count > MAX_SOURCE_ENTRIES:
            raise FleetPlanInvalid("Parallel work source exceeds the existing capture limit")
        require_current()
        current = git.observe(workspace)
        if (
            current.fingerprint.state_id != observation.fingerprint.state_id
            or current.changed_files
        ):
            raise StateConflict("Parallel work source changed during base capture")
    source = RepositorySourceAuthority.create(
        subject_instance_id=subject,
        source_snapshot=PlanSnapshotEvidence(
            sha256=snapshot.sha256, byte_count=snapshot.byte_count, entry_count=snapshot.entry_count
        ),
    )
    child_specs = tuple(
        ParallelAgentChild(
            fleet_run_id=run_id,
            task_sha256=hashlib.sha256(request.child_task(index).encode("utf-8")).hexdigest(),
            native_selection_id=parallel_native_selection_id(selected[index]),
        )
        for index, run_id in enumerate(child_ids)
    )
    intent = ParallelAgentWorkIntent(
        work_id=request.work_id,
        repository_id=logical_repository_id,
        source=source,
        base_revision=request.expected_base_revision,
        children=(child_specs[0], child_specs[1]),
        notes_enabled=request.notes_enabled,
        verifier_id=request.verifier.identity,
    )
    started = begin_parallel_work(database, intent, request_hash, request.verifier)
    if started:
        try:
            for index, run_id in enumerate(child_ids):
                observed = read_parallel_work(database, intent.work_id)
                if observed.dispatch_closed or observed.children[index].cancellation_requested:
                    continue
                try:
                    service.run_native_agent(
                        selected=selected[index],
                        workspace=workspace,
                        skill=None,
                        task=request.child_task(index),
                        verification=(request.verifier.argv,),
                        fleet_run_id=run_id,
                        reviewed_repository_source=source,
                        expected_repository_instance_ids={logical_repository_id: subject},
                        candidate=True,
                        parallel_work_id=request.work_id,
                        expected_candidate_base_revision=intent.base_revision,
                    )
                except FleetDomainError as exc:
                    record_child_refusal(database, intent.work_id, run_id, exc.code)
                except OSError:
                    record_child_refusal(database, intent.work_id, run_id, "PREPARATION_IO_FAILURE")
        finally:
            close_parallel_work(database, intent.work_id)
    return read_parallel_work(database, intent.work_id)
