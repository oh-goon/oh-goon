"""Registered-source candidate preparation uses the durable workspace owners."""

from __future__ import annotations

import hashlib
import subprocess
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast
from unittest.mock import Mock

import pytest

from oh_goon.core.errors import (
    FleetPlanInvalid,
    RunNotFound,
    ScheduleExecutionStale,
    StateConflict,
    WorkspaceStateStale,
)
from oh_goon.core.models.agent_providers import (
    AgentConnection,
    AgentModel,
    AgentProvider,
    AgentProviderError,
    AgentProvidersConfig,
    NativeAgentDefinition,
    NativeAgentProfile,
    PiSessionIntent,
    ResolvedNativeAgent,
)
from oh_goon.core.models.branch_advance import PreparedBranchAdvance, branch_round_run_id
from oh_goon.core.models.parallel_agent_work import ParallelAgentWorkRequest
from oh_goon.core.models.parallel_candidate_verification import (
    FrozenVerifierInput,
    ParallelCandidateVerification,
    ParallelCandidateVerifier,
)
from oh_goon.core.models.repository import RepositoryIdentity
from oh_goon.core.product_identity import repository_subject_instance_id
from oh_goon.core.vocabulary.workspace import WorkspaceLifecycleState
from oh_goon.engine.git import GitService
from oh_goon.integrations import source_snapshot
from oh_goon.runtime.branch_advances import BranchAdvanceStore
from oh_goon.runtime.database import LocalDatabase
from oh_goon.runtime.parallel_agent_work import replay_parallel_work
from oh_goon.runtime.schedule_definitions import ScheduleDefinitionStore
from oh_goon.runtime.workspaces import RepositoryWorkspaceAuthority
from oh_goon.services import fleet_plan_parallel_agents
from oh_goon.services.fleet_plan_candidate import (
    prepare_candidate_workspace,
    release_unadmitted_candidate,
    require_native_candidate_selection,
    workspace_control_git,
)
from oh_goon.services.fleet_plan_parallel_agents import (
    parallel_request_hash,
    start_parallel_agent_work,
)
from oh_goon.services.repositories import RepositoryService
from oh_goon.services.repository_discovery import RepositoryDiscoveryService
from tests.factories.local_state import local_state
from tests.factories.run_resources import run_concurrency_policy

if TYPE_CHECKING:
    from oh_goon.services.fleet_plans import FleetPlanService


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


def test_named_candidate_requires_exact_branch_even_at_the_same_commit(case):
    database, _, repo = case
    commit = _git(repo.path, "rev-parse", "HEAD")
    _git(repo.path, "branch", "agents/approved")
    _git(repo.path, "switch", "-c", "agents/other")
    worktrees = _git(repo.path, "worktree", "list", "--porcelain")

    with pytest.raises(StateConflict, match="branch changed before preparation"):
        _prepare(case, expected_source_branch="agents/approved", expected_base_revision=commit)
    assert _git(repo.path, "worktree", "list", "--porcelain") == worktrees

    _git(repo.path, "switch", "agents/approved")

    def switch_before_materialization(_path):
        _git(repo.path, "switch", "agents/other")

    with pytest.raises(WorkspaceStateStale, match="branch changed before materialization"):
        _prepare(
            case,
            expected_source_branch="agents/approved",
            expected_base_revision=commit,
            validate_candidate_path=switch_before_materialization,
        )
    assert _git(repo.path, "worktree", "list", "--porcelain") == worktrees

    _git(repo.path, "switch", "agents/approved")
    admission = _prepare(
        case, expected_source_branch="agents/approved", expected_base_revision=commit
    )
    try:
        assert admission.workspace.base_revision == commit
        assert _git(repo.path, "symbolic-ref", "--short", "HEAD") == "agents/approved"
    finally:
        release_unadmitted_candidate(
            admission, database=database, fleet_run_id="candidate-test-run"
        )


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


def _candidate_selection() -> ResolvedNativeAgent:
    return ResolvedNativeAgent(
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


@pytest.mark.parametrize("override", ["arguments", "configuration_directory"])
def test_candidate_mode_refuses_unsupported_native_configuration_before_preparation(override):
    selected = _candidate_selection()
    require_native_candidate_selection(selected)
    changes = (
        {"arguments": ("--session", "private")}
        if override == "arguments"
        else {"configuration_directory": "/private/config"}
    )
    invalid = replace(selected, definition=selected.definition.model_copy(update=changes))
    with pytest.raises(FleetPlanInvalid, match="Candidate execution requires"):
        require_native_candidate_selection(invalid)


@pytest.mark.parametrize(
    ("advance_id", "error", "message"),
    [
        (None, FleetPlanInvalid, "original advancement inputs"),
        ("missing", StateConflict, "published branch advancement"),
    ],
)
def test_reserved_successor_refuses_without_published_branch_authority(
    case: tuple[LocalDatabase, RepositoryService, RepositoryIdentity],
    advance_id: str | None,
    error: type[Exception],
    message: str,
) -> None:
    from oh_goon.services.fleet_plans import FleetPlanService

    database, repositories, repository = case
    original_worktrees = _git(repository.path, "worktree", "list", "--porcelain")
    runs = Mock()
    service = FleetPlanService(
        Mock(), runs, Mock(), Mock(), Mock(), repositories=repositories, workspace_database=database
    )
    selected = ResolvedNativeAgent(
        agent="pi",
        profile_name="candidate",
        definition=NativeAgentDefinition(executable="/bin/true"),
        profile=NativeAgentProfile(backend="bubblewrap"),
        executable_sha256="a" * 64,
        session=PiSessionIntent(mode="ephemeral"),
    )
    with pytest.raises(error, match=message):
        service.run_native_agent(
            selected=selected,
            workspace=repository.path,
            skill=None,
            task="Edit this candidate",
            fleet_run_id=branch_round_run_id("missing"),
            candidate=True,
            branch_advance_id=advance_id,
        )
    assert not runs.mock_calls
    assert _git(repository.path, "worktree", "list", "--porcelain") == original_worktrees


def test_successor_retry_preserves_published_source_and_original_authored_selection(
    case: tuple[LocalDatabase, RepositoryService, RepositoryIdentity],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from oh_goon.services import fleet_plan_writable_round
    from oh_goon.services.fleet_plans import FleetPlanService

    database, repositories, repository = case
    owner = tmp_path / "owner.git"
    owner.mkdir()
    skill = tmp_path / "SKILL.md"
    skill.write_text("# Reviewed task context\n", encoding="utf-8")
    executable = Path("/usr/bin/true").resolve(strict=True)
    verifier = ParallelCandidateVerifier(
        acceptance="Check the exact candidate",
        executable=str(executable),
        executable_sha256=hashlib.sha256(executable.read_bytes()).hexdigest(),
        arguments=("{input:check}", "{candidate}"),
        inputs=(
            FrozenVerifierInput(name="check", content="", sha256=hashlib.sha256(b"").hexdigest()),
        ),
    )
    previous = "a" * 40
    previous_subject = repository_subject_instance_id(repository.path)
    prepared = PreparedBranchAdvance(
        advance_id="next-round",
        request_sha256="1" * 64,
        repository_path=repository.path,
        repository_id=repository.repository_id,
        checkout_identity=RepositoryWorkspaceAuthority.receiving_checkout_identity(repository.path),
        branch_owner_path=owner,
        branch_owner_subject_id=repository_subject_instance_id(owner),
        branch_ref="refs/heads/agent",
        expected_tip=previous,
        commit="b" * 40,
        tree="c" * 40,
        patch_sha256="d" * 64,
        verification=ParallelCandidateVerification(
            fleet_run_id="prior-run",
            parallel_work_id="original-work",
            workspace_id="prior-workspace",
            base_revision=previous,
            base_source_id="prior-source",
            verifier_id=verifier.identity,
            checkpoint_id="verified-checkpoint",
            source_sha256="e" * 64,
            result_identity="verified-result",
            physical_execution_id="native-verifier",
            native_outcome="PASS",
            outcome="PASS",
            reason="Passed",
        ),
        source_byte_count=30,
        source_entry_count=2,
    )
    store = BranchAdvanceStore(database)
    store.prepare(prepared)
    store.confirm(prepared, observed_tip=prepared.commit)
    runs = Mock()
    runs.get.side_effect = RunNotFound("No Run admitted before contact loss")
    service = FleetPlanService(
        Mock(),
        runs,
        Mock(),
        Mock(),
        Mock(),
        standard_local_fleet=True,
        repositories=repositories,
        workspace_database=database,
    )
    launched = Mock(side_effect=OSError("Interrupted before Run admission"))

    def synchronize(*_args: object, **_kwargs: object) -> None:
        (repository.path / "synchronized.py").write_text("updated\n", encoding="utf-8")

    sync = Mock(side_effect=synchronize)
    monkeypatch.setattr(service, "run_native_agent", launched)
    monkeypatch.setattr(fleet_plan_writable_round, "sync_branch_checkout", sync)
    monkeypatch.setattr(
        fleet_plan_writable_round, "read_parallel_verifier", Mock(return_value=verifier)
    )

    def start(task: str) -> None:
        service.start_writable_round(
            advance_id=prepared.advance_id,
            selected=_candidate_selection(),
            workspace=repository.path,
            skill=skill,
            task=task,
        )

    with pytest.raises(ScheduleExecutionStale):
        service.start_writable_round(
            advance_id=prepared.advance_id,
            selected=_candidate_selection(),
            workspace=repository.path,
            skill=None,
            task="Not selected by an occurrence",
            schedule_run_id="unrecorded-occurrence",
        )
    with monkeypatch.context() as reserved:
        reserved.setattr(ScheduleDefinitionStore, "seed_reserved", lambda _store, _id: True)
        with pytest.raises(StateConflict, match="belongs to a writable Schedule"):
            start("Cannot take a reserved seed")
    untouched = store.find(prepared.advance_id)
    assert untouched is not None and untouched.next_run is None
    sync.assert_not_called()
    launched.assert_not_called()

    with pytest.raises(OSError, match="Interrupted before Run admission"):
        start("Use the reviewed task context")
    receipt = store.find(prepared.advance_id)
    assert receipt is not None and receipt.next_run is not None
    assert receipt.next_run.fleet_run_id == branch_round_run_id(prepared.advance_id)
    assert receipt.next_run.repository_subject_id == repository_subject_instance_id(repository.path)
    assert receipt.next_run.repository_subject_id != previous_subject
    assert (
        RepositoryWorkspaceAuthority.receiving_checkout_identity(repository.path)
        == prepared.checkout_identity
    )
    assert sync.call_args.kwargs["prepared"].commit == prepared.commit
    assert sync.call_args.kwargs["candidate_identity"].sha256 == prepared.verification.source_sha256
    assert launched.call_args.kwargs["fleet_run_id"] == receipt.next_run.fleet_run_id
    assert launched.call_args.kwargs["skill"] == skill
    assert launched.call_args.kwargs["selected"].session == PiSessionIntent(mode="ephemeral")
    with pytest.raises(StateConflict, match="different successor"):
        start("Replace the previously authorized task")
    sync.assert_called_once()
    launched.assert_called_once()


def test_parallel_work_refuses_a_selected_key_file_before_snapshot_and_admission(
    case: tuple[LocalDatabase, RepositoryService, RepositoryIdentity],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database, repositories, repository = case
    key = repository.path / "synthetic-provider-key"
    key.write_text("synthetic-test-value\n")
    _git(repository.path, "add", key.name)
    _git(repository.path, "commit", "-qm", "synthetic credential")

    catalog = AgentProvidersConfig(
        providers={
            "plain": AgentProvider(api="openai-chat-completions", base_url="http://127.0.0.1:1"),
            "file": AgentProvider(
                api="openai-chat-completions", base_url="http://127.0.0.1:1", api_key_file=str(key)
            ),
        },
        models={
            "plain": AgentModel(provider="plain", model="synthetic"),
            "file": AgentModel(provider="file", model="synthetic"),
        },
    )
    selected = ResolvedNativeAgent(
        agent="pi",
        profile_name="plain",
        definition=NativeAgentDefinition(executable="/usr/bin/true"),
        profile=NativeAgentProfile(backend="bubblewrap", inference_profile="plain"),
        executable_sha256="a" * 64,
        connection=AgentConnection.select(catalog, "plain"),
    )
    file_selected = replace(
        selected,
        profile_name="file",
        profile=NativeAgentProfile(backend="bubblewrap", inference_profile="file"),
        connection=AgentConnection.select(catalog, "file"),
    )
    capacity = run_concurrency_policy()
    verifier_executable = Path("/usr/bin/true").resolve(strict=True)
    request = ParallelAgentWorkRequest(
        work_id="credential-file",
        workspace=repository.path,
        expected_base_revision=_git(repository.path, "rev-parse", "HEAD"),
        task="Check candidates",
        briefs=("Try one change", "Try another change"),
        native_profiles=("plain", "file"),
        capacity=capacity,
        notes_enabled=False,
        verifier=ParallelCandidateVerifier(
            acceptance="Check the candidate",
            executable=str(verifier_executable),
            executable_sha256=hashlib.sha256(verifier_executable.read_bytes()).hexdigest(),
            arguments=("{input:check}", "{candidate}"),
            inputs=(
                FrozenVerifierInput(
                    name="check", content="", sha256=hashlib.sha256(b"").hexdigest()
                ),
            ),
        ),
    )
    snapshot = Mock()
    run = Mock()
    service = cast(
        "FleetPlanService",
        SimpleNamespace(
            _workspace_database=database,
            _standard_local_fleet=True,
            _run_concurrency=capacity,
            _repositories=repositories,
            _git_service=None,
            require_plan_ingress_preparation=Mock(),
            run_native_agent=run,
        ),
    )
    monkeypatch.setattr(fleet_plan_parallel_agents, "source_snapshot_identity", snapshot)

    with pytest.raises(AgentProviderError, match="overlaps an Agent source or tool root"):
        start_parallel_agent_work(service, request, selected=(selected, file_selected))

    snapshot.assert_not_called()
    run.assert_not_called()
    assert replay_parallel_work(database, request.work_id, parallel_request_hash(request)) is None
