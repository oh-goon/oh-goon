"""Candidate transport checks without claiming native namespace qualification."""

from __future__ import annotations

import os
import shutil
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock

import pytest

from oh_goon._application_worker_runtime import WorkerGitConfiguration
from oh_goon.core.errors import FleetPlanInvalid, WorkspaceLeaseLost
from oh_goon.core.models.agent_providers import (
    NativeAgentDefinition,
    NativeAgentProfile,
    ResolvedNativeAgent,
)
from oh_goon.core.models.fleet_plan import ResolvedCommandStep, ResolvedFleetPlan
from oh_goon.core.models.fleet_plan_execution_policy import MaintenanceExecutionPolicy
from oh_goon.engine.command import CommandResult
from oh_goon.engine.git import GitService
from oh_goon.integrations.fleet_plan_bubblewrap_isolation import BubblewrapProcessIsolation
from oh_goon.integrations.fleet_plan_process_isolation import (
    native_profile_command_runner,
    require_native_workspace_scope,
)
from oh_goon.integrations.fleet_plan_run_execution import FleetPlanRunExecutor
from oh_goon.runtime.execution_assignments import ExecutionAssignmentAuthority
from oh_goon.runtime.workspaces import RepositoryWorkspaceAuthority
from oh_goon.services.fleet_plan_candidate import workspace_control_git
from tests.factories.execution_materialization import ready_execution_materialization_candidate


@pytest.fixture
def candidate(tmp_path: Path):
    database, workspaces, workspace, state, checkpoint, _old, leases, lease, authority = (
        ready_execution_materialization_candidate(tmp_path)
    )
    ready = leases.release(lease, expected_state_version=state.state_version)
    assignments = ExecutionAssignmentAuthority(database, workspaces)
    assignment = assignments.issue_candidate_mutation(
        workspace,
        attempt_id="original-operation",
        expected_checkpoint_id=checkpoint.checkpoint_id,
        wall_time_seconds=60,
        isolation_requirements=("private_filesystem", "private_pid_namespace"),
    )
    _state, lease = leases.acquire(
        workspace,
        assignment,
        executor_id=database.session_id,
        executor_generation=1,
        expected_state_version=ready.state_version,
    )
    path = workspaces.physical_path(workspace)
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        materialization = authority.materialize(
            workspace,
            assignment,
            lease,
            writer_scope_kind="native_namespace_scope",
            workspace_directory_fd=descriptor,
        )
        executable = tmp_path / "bwrap"
        executable.write_text("selected native backend")
        backend = BubblewrapProcessIsolation(str(executable))
        selected = ResolvedNativeAgent(
            agent="pi",
            profile_name="candidate",
            definition=NativeAgentDefinition(executable="/usr/bin/true"),
            profile=NativeAgentProfile(backend="bubblewrap", workspace_roots=(str(path),)),
            executable_sha256="a" * 64,
            backend_configuration_id=backend.configuration_identity(),
        )
        yield path, descriptor, materialization, selected, backend
    finally:
        os.close(descriptor)


def test_candidate_uses_exact_materialization_fd_and_original_verifier(candidate, monkeypatch):
    path, descriptor, materialization, selected, backend = candidate
    verify = Mock()
    captured = []

    def execute(request, **_kwargs):
        request.require_workspace_current()
        captured.append(request)
        return CommandResult(0, "bounded", "")

    monkeypatch.setattr(backend, "execute", execute)
    runner = native_profile_command_runner(
        path,
        selected,
        backend,
        RepositoryWorkspaceAuthority.pin_repository_subject,
        workspace_materialization=materialization,
        workspace_directory_fd=descriptor,
        require_candidate_current=verify,
    )
    result = runner("/usr/bin/true", cwd=path, env={"PATH": os.defpath})
    assert result.returncode == 0
    assert len(captured) == 1
    assert captured[0].workspace == path
    assert captured[0].workspace_materialization == materialization
    assert captured[0].workspace_directory_fd == descriptor
    verify.side_effect = WorkspaceLeaseLost("original writer lease lost")
    with pytest.raises(WorkspaceLeaseLost):
        runner("/usr/bin/true", cwd=path, env={"PATH": os.defpath})
    assert len(captured) == 1


@pytest.mark.process
@pytest.mark.skipif(shutil.which("bwrap") is None, reason="Selected native Bubblewrap boundary")
def test_candidate_host_metadata_is_not_rebound_into_git_masked_payload(candidate):
    path, descriptor, materialization, selected, _old_backend = candidate
    backend = BubblewrapProcessIsolation(shutil.which("bwrap") or "")
    selected = replace(selected, backend_configuration_id=backend.configuration_identity())
    git = workspace_control_git(GitService())
    executor = FleetPlanRunExecutor(
        git,
        process_isolation_backends=(),
        standard_process_isolation=backend,
        workspace_pin=RepositoryWorkspaceAuthority.pin_repository_subject,
    )
    plan = ResolvedFleetPlan.create(
        name="candidate-metadata",
        description="",
        execution=MaintenanceExecutionPolicy(),
        native_agent=selected,
        steps=(ResolvedCommandStep(step_id="verify", argv=("/usr/bin/true",)),),
    )
    marker = (path / ".git").read_bytes()
    observed = executor.git_for_plan(
        path,
        plan,
        workspace_materialization=materialization,
        workspace_directory_fd=descriptor,
        require_candidate_current=lambda: None,
    ).observe(path)
    assert observed.is_repository and observed.head_sha is not None
    # The host-only routing change cannot bypass the original binding guard.
    with pytest.raises(FleetPlanInvalid, match="directory"):
        executor.git_for_plan(
            path,
            plan,
            workspace_materialization=materialization,
            require_candidate_current=lambda: None,
        )
    runner = native_profile_command_runner(
        path,
        selected,
        backend,
        RepositoryWorkspaceAuthority.pin_repository_subject,
        workspace_materialization=materialization,
        workspace_directory_fd=descriptor,
        require_candidate_current=lambda: None,
    )
    result = runner("/usr/bin/git", "status", "--porcelain", cwd=path, env={"PATH": os.defpath})
    assert result.returncode != 0
    assert (path / ".git").read_bytes() == marker


def test_linked_workspace_cannot_borrow_an_unbound_native_runner(candidate):
    path, _descriptor, _materialization, selected, backend = candidate
    with pytest.raises(FleetPlanInvalid, match="external linked-worktree"):
        native_profile_command_runner(
            path, selected, backend, RepositoryWorkspaceAuthority.pin_repository_subject
        )


def test_candidate_materialization_requires_its_owned_root_descriptor(candidate):
    path, _descriptor, materialization, selected, backend = candidate
    with pytest.raises(FleetPlanInvalid, match="directory custody"):
        native_profile_command_runner(
            path,
            selected,
            backend,
            RepositoryWorkspaceAuthority.pin_repository_subject,
            workspace_materialization=materialization,
            require_candidate_current=lambda: None,
        )


def test_candidate_does_not_widen_selected_physical_roots(candidate, tmp_path):
    path, descriptor, materialization, selected, backend = candidate
    selected = replace(
        selected,
        profile=selected.profile.model_copy(update={"workspace_roots": (str(tmp_path / "other"),)}),
    )
    with pytest.raises(FleetPlanInvalid, match="workspace policy"):
        native_profile_command_runner(
            path,
            selected,
            backend,
            RepositoryWorkspaceAuthority.pin_repository_subject,
            workspace_materialization=materialization,
            workspace_directory_fd=descriptor,
            require_candidate_current=lambda: None,
        )
    proposed = tmp_path / "other" / "not-created-yet"
    require_native_workspace_scope(proposed, selected)
    assert not proposed.exists()


def test_worker_transport_refuses_live_candidate_resources_instead_of_dropping_them(candidate):
    path, descriptor, materialization, selected, backend = candidate
    runner = native_profile_command_runner(
        path,
        selected,
        backend,
        RepositoryWorkspaceAuthority.pin_repository_subject,
        workspace_materialization=materialization,
        workspace_directory_fd=descriptor,
        require_candidate_current=lambda: None,
    )
    with pytest.raises(FleetPlanInvalid, match="without injected callbacks"):
        WorkerGitConfiguration.capture(GitService(command_runner=runner), isolation=backend)
