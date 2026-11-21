"""Registered-source candidate preparation uses the durable workspace owners."""

from __future__ import annotations

import subprocess
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock

import pytest

from oh_goon.core.errors import FleetPlanInvalid, StateConflict
from oh_goon.core.models.agent_providers import (
    AgentConnection,
    AgentModel,
    AgentProvider,
    AgentProvidersConfig,
    NativeAgentDefinition,
    NativeAgentProfile,
    ResolvedNativeAgent,
)
from oh_goon.core.models.repository import RepositoryIdentity
from oh_goon.core.product_identity import repository_subject_instance_id
from oh_goon.core.vocabulary.workspace import WorkspaceLifecycleState
from oh_goon.engine.git import GitService
from oh_goon.integrations import source_snapshot
from oh_goon.runtime.database import LocalDatabase
from oh_goon.runtime.workspaces import RepositoryWorkspaceAuthority
from oh_goon.services.fleet_plan_candidate import (
    prepare_candidate_workspace,
    release_unadmitted_candidate,
    require_native_candidate_selection,
    workspace_control_git,
)
from oh_goon.services.repositories import RepositoryService
from oh_goon.services.repository_discovery import RepositoryDiscoveryService
from tests.factories.local_state import local_state


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ("git", *args), cwd=repo, text=True, capture_output=True, check=True
    ).stdout.strip()


@pytest.fixture
def case(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.name", "Test")
    _git(repo, "config", "user.email", "test@example.invalid")
    (repo / "app.py").write_text("VALUE = 1\n")
    _git(repo, "add", "app.py")
    _git(repo, "commit", "-qm", "base")
    database = LocalDatabase(tmp_path / "state/control.sqlite")
    state = local_state(database)
    state.add_location(repo)
    return (
        database,
        RepositoryService(state, RepositoryDiscoveryService()),
        RepositoryIdentity(path=repo),
    )


def _prepare(case, **options):
    database, repositories, repo = case
    return prepare_candidate_workspace(
        database=database,
        repositories=repositories,
        repository_id=repo.repository_id,
        fleet_run_id="candidate-test-run",
        **options,
    )


def test_registered_source_becomes_two_owned_workspaces_and_fenced_initial_checkpoint(case):
    database, _, repo = case
    observed = []

    def validate(path):
        assert not path.exists()
        observed.append(path)

    admission = _prepare(case, validate_candidate_path=validate)
    owner = RepositoryWorkspaceAuthority(database)
    assert observed == [owner.physical_path(admission.workspace)]
    assert admission.source.subject_instance_id == repository_subject_instance_id(repo.path)
    assert (
        admission.workspace.repository_id
        == admission.source_workspace.repository_id
        == repo.repository_id
    )
    assert owner.current_candidate_checkpoint(admission.workspace) == admission.checkpoint
    assert (owner.physical_path(admission.workspace) / "app.py").read_text() == "VALUE = 1\n"
    assert (repo.path / "app.py").read_text() == "VALUE = 1\n"
    assert admission.workspace_inode == owner.physical_path(admission.workspace).stat().st_ino
    release_unadmitted_candidate(admission, database=database, fleet_run_id="candidate-test-run")
    assert (
        owner.state(admission.workspace.workspace_id).lifecycle_state
        is WorkspaceLifecycleState.RELEASED
    )
    assert (
        owner.state(admission.source_workspace.workspace_id).lifecycle_state
        is WorkspaceLifecycleState.RELEASED
    )


def test_candidate_profile_path_refusal_precedes_source_byte_capture(case, monkeypatch):
    def reject(_path):
        raise FleetPlanInvalid("Selected roots do not include the owned candidate")

    def bytes_must_not_open(*_args, **_kwargs):
        raise AssertionError("source identity was read before path authorization")

    monkeypatch.setattr(source_snapshot, "_identify_source", bytes_must_not_open)
    with pytest.raises(FleetPlanInvalid, match="Selected roots"):
        _prepare(case, validate_candidate_path=reject)


def test_candidate_source_refuses_dirty_registered_checkout(case):
    (case[2].path / "app.py").write_text("UNCOMMITTED\n")
    with pytest.raises(FleetPlanInvalid, match="committed and clean"):
        _prepare(case)


def test_candidate_source_rejects_grader_material_before_byte_copy(case, monkeypatch):
    repo = case[2].path
    private = repo / "docs/certification/answers/private.txt"
    private.parent.mkdir(parents=True)
    private.write_text("GRADER_ONLY_CANARY")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "grader")

    def bytes_must_not_open(*_args, **_kwargs):
        raise AssertionError("grader bytes reached content identity")

    monkeypatch.setattr(source_snapshot, "_identify_source", bytes_must_not_open)
    with pytest.raises(FleetPlanInvalid, match="grader-only"):
        _prepare(case)


def test_candidate_lifecycle_git_does_not_execute_local_fsmonitor_or_hooks(case, tmp_path):
    repo = case[2].path
    canary = tmp_path / "unexpected-helper-effect"
    helper = tmp_path / "git-helper"
    helper.write_text(f"#!/bin/sh\nprintf invoked > '{canary}'\nprintf '1\\n'\n")
    helper.chmod(0o755)
    _git(repo, "config", "core.fsmonitor", str(helper))
    hooks = tmp_path / "hooks"
    hooks.mkdir()
    (hooks / "post-checkout").write_text(f"#!/bin/sh\nprintf hook > '{canary}'\n")
    (hooks / "post-checkout").chmod(0o755)
    _git(repo, "config", "core.hooksPath", str(hooks))
    # Establish the canary actually detects the native repository configuration.
    _git(repo, "status", "--porcelain")
    assert canary.exists()
    canary.unlink()
    admission = _prepare(case)
    control = workspace_control_git()
    owner = RepositoryWorkspaceAuthority(case[0])
    assert not control.observe(owner.physical_path(admission.workspace)).changed_files
    release_unadmitted_candidate(admission, database=case[0], fleet_run_id="candidate-test-run")
    assert not canary.exists()


def test_candidate_cleanup_rejects_another_proposed_run(case):
    admission = _prepare(case)
    with pytest.raises(StateConflict, match="another proposed Run"):
        release_unadmitted_candidate(admission, database=case[0], fleet_run_id="another-run")
    owner = RepositoryWorkspaceAuthority(case[0])
    assert (
        owner.state(admission.workspace.workspace_id).lifecycle_state
        is WorkspaceLifecycleState.READY
    )


def test_candidate_control_refuses_unknown_git_execution_policy():
    provider = Mock()
    with pytest.raises(FleetPlanInvalid, match="custom Git execution provider"):
        workspace_control_git(GitService(command_runner=provider))
    provider.assert_not_called()


def test_candidate_control_refuses_native_clean_filter_without_running_it(case, tmp_path):
    repo = case[2].path
    (repo / ".gitattributes").write_text("*.py filter=evil\n")
    _git(repo, "add", ".gitattributes")
    _git(repo, "commit", "-qm", "attributes")
    marker = tmp_path / "filter-executed"
    helper = tmp_path / "clean-filter"
    helper.write_text(f"#!/bin/sh\nprintf invoked > '{marker}'\ncat\n")
    helper.chmod(0o755)
    _git(repo, "config", "filter.evil.clean", str(helper))
    (repo / "app.py").touch()
    _git(repo, "status", "--porcelain")
    assert marker.exists()
    marker.unlink()
    with pytest.raises(StateConflict, match="clean/process filters"):
        _prepare(case)
    assert not marker.exists()


@pytest.mark.parametrize("override", ["arguments", "configuration_directory"])
def test_candidate_mode_refuses_unsupported_native_configuration_before_preparation(override):
    selected = ResolvedNativeAgent(
        agent="pi",
        profile_name="candidate",
        definition=NativeAgentDefinition(executable="/bin/true"),
        profile=NativeAgentProfile(backend="bubblewrap", inference_profile="model"),
        executable_sha256="a" * 64,
        connection=AgentConnection.select(
            AgentProvidersConfig(
                providers={
                    "test": AgentProvider(
                        api="openai-chat-completions", base_url="http://127.0.0.1:1"
                    )
                },
                models={"model": AgentModel(provider="test", model="synthetic")},
            ),
            "model",
        ),
    )
    require_native_candidate_selection(selected)
    changes = (
        {"arguments": ("--session", "private")}
        if override == "arguments"
        else {"configuration_directory": "/private/config"}
    )
    invalid = replace(selected, definition=selected.definition.model_copy(update=changes))
    with pytest.raises(FleetPlanInvalid, match="Candidate execution requires"):
        require_native_candidate_selection(invalid)
