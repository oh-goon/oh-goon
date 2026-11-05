from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from functools import partial
from pathlib import Path
from typing import Any, NoReturn

import pytest

from oh_goon import _application_worker_runtime as worker
from oh_goon._application_foundation import RuntimeFoundation, build_runtime_foundation
from oh_goon.core.errors import FleetPlanInvalid, OwnershipLost
from oh_goon.core.models.agent_providers import (
    NativeAgentDefinition,
    NativeAgentProfile,
    ResolvedNativeAgent,
)
from oh_goon.core.models.agent_repair import AgentRepairContext
from oh_goon.core.models.fleet_plan_execution_policy import FleetPlanRunLimits
from oh_goon.core.models.run_admission import (
    DirectFleetExecutionAdmission,
    FleetRunAdmissionIntent,
    fleet_repository_scope,
)
from oh_goon.core.vocabulary import OperationAction
from oh_goon.engine.command import CommandResult
from oh_goon.engine.git import GitService
from oh_goon.integrations.agent_native_execution import native_executable_sha256
from oh_goon.integrations.fleet_plan_bubblewrap_isolation import BubblewrapProcessIsolation
from oh_goon.integrations.fleet_plan_process_isolation import (
    NativeProfileCommandRunner,
    StandardIsolatedProcessRequest,
    native_profile_command_runner,
)
from oh_goon.integrations.hashmarks import require_loaded_hashmarks_runtime
from oh_goon.runtime._executor_configuration import restore_worker_isolation_configuration
from oh_goon.runtime.workspaces import RepositoryWorkspaceAuthority
from tests.factories.capabilities import capability_snapshot


@pytest.fixture
def foundation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[RuntimeFoundation]:
    monkeypatch.setenv("OH_GOON_DB", str(tmp_path / "state" / "app.sqlite"))
    monkeypatch.setenv("OH_GOON_LEAGUE_FILE", str(tmp_path / "league.yml"))
    monkeypatch.delenv("OH_GOON_EXECUTORS_FILE", raising=False)
    for name, directory in (
        ("XDG_CONFIG_HOME", "config"),
        ("XDG_DATA_HOME", "data"),
        ("XDG_STATE_HOME", "state"),
    ):
        monkeypatch.setenv(name, str(tmp_path / directory))
    value = build_runtime_foundation(
        standard_local=True,
        startup_purpose="workflow",
        git_service=GitService(default_timeout_seconds=13),
    )
    try:
        yield value
    finally:
        value.supervisor.shutdown(wait=True)
        value.lifecycle.close()


def test_worker_configuration_keeps_resolved_roots_and_git_authority(
    foundation: RuntimeFoundation, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured = worker.capture_runtime_worker_configuration(foundation, "selected-run")
    monkeypatch.setenv("OH_GOON_DB", str(tmp_path / "another" / "db.sqlite"))
    monkeypatch.setenv("OH_GOON_EXECUTORS_FILE", str(tmp_path / "missing.toml"))
    restored = worker.RunWorkerConfiguration.model_validate_json(captured.model_dump_json())
    assert restored == captured
    assert restored.fleet_run_id == "selected-run"
    assert restored.hashmarks_implementation_identity == require_loaded_hashmarks_runtime()
    assert restored.extension_root == foundation.extension_store.root
    assert restored.tool_root == foundation.tool_store.root
    assert restored.content_store_root == foundation.content_store.root
    assert restored.git.restore() == foundation.git_service
    assert restored.standard_local_fleet is True
    assert restored.require_authenticated_fleet is False
    assert restored.fleet_authority is None and restored.executor_configuration is None


def test_injected_git_callbacks_are_refused_before_admission(
    foundation: RuntimeFoundation,
) -> None:
    def callback(*_args: object, **_kwargs: object) -> CommandResult:
        pytest.fail("worker capture must not call or substitute an injected native owner")

    source = replace(foundation, git_service=GitService(command_runner=callback))
    before = source.database.durable_revision()
    with pytest.raises(FleetPlanInvalid, match="without injected callbacks"):
        worker.capture_runtime_worker_configuration(source, "selected-run")
    assert source.database.durable_revision() == before
    assert source.execution.active_lease() is None


def _native_git(
    tmp_path: Path, *, sandboxed: bool
) -> tuple[Path, ResolvedNativeAgent, BubblewrapProcessIsolation | None, GitService]:
    repository = tmp_path / "repository"
    repository.mkdir()
    assert GitService().run("init", "-q", str(repository)).returncode == 0
    program = tmp_path / "native"
    program.write_text("#!/bin/sh\nexit 0\n")
    program.chmod(0o700)
    isolation = (
        BubblewrapProcessIsolation(str(program), control_state_root=tmp_path / "control")
        if sandboxed
        else None
    )
    selected = ResolvedNativeAgent(
        agent="pi",
        profile_name="captured-native",
        definition=NativeAgentDefinition(executable=str(program)),
        profile=NativeAgentProfile(backend="bubblewrap" if sandboxed else "local"),
        executable_sha256=native_executable_sha256(program),
        backend_configuration_id=isolation.configuration_identity() if isolation else None,
    )
    runner = native_profile_command_runner(
        repository, selected, isolation, RepositoryWorkspaceAuthority.pin_repository_subject
    )
    return (
        repository,
        selected,
        isolation,
        GitService(
            default_timeout_seconds=13,
            command_runner=runner,
            observation_runner=partial(runner, read_only_workspace=True) if sandboxed else None,
        ),
    )


@pytest.mark.parametrize("sandboxed", (False, True))
def test_worker_preserves_original_native_git_scope_and_observation(
    foundation: RuntimeFoundation,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    sandboxed: bool,
) -> None:
    repository, selected, isolation, git = _native_git(tmp_path, sandboxed=sandboxed)
    source = replace(foundation, git_service=git, standard_process_isolation=isolation)
    captured = worker.capture_runtime_worker_configuration(source, "native-run")
    transported = worker.RunWorkerConfiguration.model_validate_json(captured.model_dump_json())
    assert transported.git.native_repository == repository
    assert transported.git.native_agent == selected
    assert transported.git.default_timeout_seconds == 13
    backend = (
        restore_worker_isolation_configuration(transported.standard_process_isolation)
        if transported.standard_process_isolation is not None
        else None
    )
    restored = transported.git.restore(isolation=backend)
    assert type(restored.command_runner) is NativeProfileCommandRunner
    if not sandboxed:
        assert restored.observation_runner is None
        result = restored.run("rev-parse", "--show-toplevel", cwd=repository)
        assert result.returncode == 0 and result.stdout.strip() == str(repository)
        return
    requests: list[StandardIsolatedProcessRequest] = []

    def execute(
        _backend: BubblewrapProcessIsolation,
        request: StandardIsolatedProcessRequest,
        **_options: Any,
    ) -> CommandResult:
        request.require_workspace_current()
        requests.append(request)
        return CommandResult(argv=request.argv, returncode=0, stdout="scoped", stderr="")

    monkeypatch.setattr(BubblewrapProcessIsolation, "execute", execute)
    assert restored.run("status", cwd=repository, env={"EXPLICIT": "captured"}).returncode == 0
    assert restored.observation_runner is not None
    assert restored.observation_runner("git", "status", cwd=repository, env={}).returncode == 0
    assert [request.read_only_workspace for request in requests] == [False, True]
    assert all(request.workspace == repository for request in requests)
    assert all(
        request.backend_configuration_id == selected.backend_configuration_id
        for request in requests
    )
    assert requests[0].env == {"EXPLICIT": "captured"}
    assert all(request.resources == selected.profile.resources for request in requests)


@pytest.mark.parametrize(
    "fault",
    (
        "pin",
        "pin-equality",
        "termination",
        "repair",
        "subclass",
        "observation-false",
        "observation-extra",
        "observation-other-owner",
        "backend",
    ),
)
def test_native_git_transport_refuses_unfrozen_or_changed_owners_before_admission(
    foundation: RuntimeFoundation, tmp_path: Path, fault: str
) -> None:
    repository, selected, isolation, git = _native_git(tmp_path, sandboxed=True)
    pin: Callable[[Path], AbstractContextManager[Callable[[], None]]] = (
        RepositoryWorkspaceAuthority.pin_repository_subject
    )
    if fault == "pin":

        def rejected_pin(_path: Path) -> NoReturn:
            pytest.fail("injected workspace pin ran")

        pin = rejected_pin
    elif fault == "pin-equality":

        class EqualPin:
            def __eq__(self, _other: object) -> bool:
                return True

            def __call__(self, _path: Path) -> Any:
                pytest.fail("equal-looking injected workspace pin ran")

        pin = EqualPin()
    runner = native_profile_command_runner(
        repository,
        selected,
        isolation,
        pin,
        termination_requested=(lambda: None) if fault == "termination" else None,
        repair_context=AgentRepairContext(
            repair_id="repair",
            attempt=1,
            max_attempts=1,
            limits=FleetPlanRunLimits(
                deadline_at=datetime.now(UTC) + timedelta(minutes=1), step_timeout_seconds=10.0
            ),
        )
        if fault == "repair"
        else None,
    )
    keywords: dict[str, Any] = {"read_only_workspace": fault != "observation-false"}
    if fault == "observation-extra":
        keywords["env"] = {"UNDECLARED": "value"}
    observation = partial(runner, **keywords)
    if fault == "subclass":

        class OtherNativeRunner(NativeProfileCommandRunner):
            def __call__(self, *_args: str, **_options: Any) -> CommandResult:
                pytest.fail("unrecognized native owner executed")

        runner = OtherNativeRunner(
            repository, selected, isolation, RepositoryWorkspaceAuthority.pin_repository_subject
        )
        observation = partial(runner, read_only_workspace=True)
    elif fault == "observation-other-owner":
        other = tmp_path / "other-repository"
        other.mkdir()
        other_runner = native_profile_command_runner(
            other, selected, isolation, RepositoryWorkspaceAuthority.pin_repository_subject
        )
        observation = partial(other_runner, read_only_workspace=True)
    git = replace(git, command_runner=runner, observation_runner=observation)
    source = replace(
        foundation,
        git_service=git,
        standard_process_isolation=None if fault == "backend" else isolation,
    )
    before = source.database.durable_revision()
    with pytest.raises(FleetPlanInvalid):
        worker.capture_runtime_worker_configuration(source, "native-run")
    assert source.database.durable_revision() == before
    assert source.execution.active_lease() is None


@pytest.mark.parametrize("field", ("native_repository", "native_agent", "native_observation"))
def test_native_git_payload_rejects_incomplete_or_weakened_scope(
    tmp_path: Path, field: str
) -> None:
    _, _, isolation, git = _native_git(tmp_path, sandboxed=True)
    captured = worker.WorkerGitConfiguration.capture(git, isolation=isolation)
    payload = captured.model_dump(mode="json")
    payload[field] = False if field == "native_observation" else None
    with pytest.raises(ValueError):
        worker.WorkerGitConfiguration.model_validate(payload)


def test_unclaimed_ticket_cannot_construct_effectful_worker_runtime(
    foundation: RuntimeFoundation, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_id = "selected-run"
    configuration = worker.capture_runtime_worker_configuration(foundation, run_id)
    journal = foundation.state.journal
    intent = FleetRunAdmissionIntent(
        capability=capability_snapshot(OperationAction.INIT),
        execution=DirectFleetExecutionAdmission(),
        repository_admissions=(),
    )
    ticket, _ = foundation.execution.reserve_fleet_with(
        run_id,
        expected_durable_revision=foundation.database.durable_revision(),
        mutate=lambda connection: journal.admit_run_in(
            connection, run_id, intent, fleet_repository_scope(None), []
        ),
    )

    def unexpected(*_args: object, **_kwargs: object) -> NoReturn:
        pytest.fail("effectful construction preceded the canonical worker claim")

    monkeypatch.setattr(worker, "_build_capability_runtime", unexpected)
    before = foundation.database.durable_revision()
    with pytest.raises(OwnershipLost):
        worker.build_claimed_run_execution(
            foundation.database,
            ticket,
            configuration,
            terminal=None,
            live_output=foundation.live_output,
            diagnostics=foundation.diagnostics,
        )
    assert foundation.database.durable_revision() == before
