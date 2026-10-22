"""Native Plan verification and native Git hooks share the selected boundary."""

from __future__ import annotations

import hashlib
import os
import shutil
import socket
import subprocess
import sys
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import replace
from functools import partial
from pathlib import Path, PurePosixPath
from typing import Any

import pytest

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.models.agent_providers import (
    NativeAgentDefinition,
    NativeAgentProfile,
    ResolvedNativeAgent,
)
from oh_goon.core.models.fleet_plan import ResolvedCommandStep, ResolvedFleetPlan
from oh_goon.core.models.fleet_plan_evidence import CompletedPlanStepExecution
from oh_goon.core.models.fleet_plan_execution_policy import MaintenanceExecutionPolicy
from oh_goon.core.models.fleet_plan_isolation import CicdResourcePolicy
from oh_goon.core.models.repository import RepositoryIdentity
from oh_goon.engine.command import CommandResult
from oh_goon.engine.errors import OperationCancelledError
from oh_goon.engine.git import GitService
from oh_goon.integrations.fleet_plan_bubblewrap_isolation import BubblewrapProcessIsolation
from oh_goon.integrations.fleet_plan_process_isolation import (
    StandardIsolatedProcessRequest,
    StandardReadOnlyMount,
    native_profile_command_runner,
)
from oh_goon.integrations.fleet_plan_run_execution import FleetPlanRunExecutor
from oh_goon.integrations.fleet_plan_step_execution import StepRuntime, execute_direct_step
from oh_goon.runtime.workspaces import RepositoryWorkspaceAuthority
from tests.factories.runtime_authority import native_invocation_executable


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux executable identity")
@pytest.mark.parametrize("profile", [False, True])
def test_ordinary_step_preserves_native_kernel_invocation_name(
    tmp_path: Path, profile: bool
) -> None:
    executable = native_invocation_executable(tmp_path)
    selection = (
        ResolvedNativeAgent(
            agent="pi",
            profile_name="local",
            definition=NativeAgentDefinition(executable=str(executable)),
            profile=NativeAgentProfile(backend="local"),
            executable_sha256=hashlib.sha256(executable.read_bytes()).hexdigest(),
        )
        if profile
        else None
    )
    result = execute_direct_step(
        tmp_path,
        ResolvedCommandStep(step_id="native-utility", argv=(str(executable),)),
        StepRuntime(
            git=GitService(),
            extensions=None,
            tools=None,
            cancel_requested=None,
            native_profile=selection,
        ),
        step_order=1,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == "native invocation retained"
    assert result.runtime_authority is not None
    assert result.runtime_authority.execution_materialization_identity is None
    assert result.supervisor is not None and result.supervisor.final_process_group_quiescent


def _selection(backend: BubblewrapProcessIsolation) -> ResolvedNativeAgent:
    return ResolvedNativeAgent(
        agent="pi",
        profile_name="sandbox",
        definition=NativeAgentDefinition(executable="/usr/bin/true"),
        profile=NativeAgentProfile(backend="bubblewrap"),
        executable_sha256="a" * 64,
        backend_configuration_id=backend.configuration_identity(),
    )


def _fake_backend(tmp_path: Path) -> BubblewrapProcessIsolation:
    executable = tmp_path / "bwrap"
    executable.write_bytes(b"selected backend")
    return BubblewrapProcessIsolation(str(executable))


def test_profile_runner_transports_pinned_subject_resources_and_native_inputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    backend = _fake_backend(tmp_path)
    resources = CicdResourcePolicy(cpu_millis=1000, memory_mib=256, max_processes=64)
    selection = replace(
        _selection(backend), profile=NativeAgentProfile(backend="bubblewrap", resources=resources)
    )
    calls: list[tuple[StandardIsolatedProcessRequest, dict[str, Any]]] = []
    events: list[str] = []

    @contextmanager
    def pin(path: Path) -> Iterator[Callable[[], None]]:
        assert path == repository
        events.append("pin")
        try:
            yield lambda: events.append("verify")
        finally:
            events.append("release")

    def execute(request: StandardIsolatedProcessRequest, **kwargs: Any) -> CommandResult:
        calls.append((request, kwargs))
        request.require_workspace_current()
        return CommandResult(0, "bounded", "")

    monkeypatch.setattr(backend, "execute", execute)
    runner = native_profile_command_runner(repository, selection, backend, pin)
    public = tmp_path / "public.json"
    public.write_text("{}")
    mount = StandardReadOnlyMount(public, PurePosixPath("/config/native/public.json"))

    def cancel() -> bool:
        return False

    result = runner(
        "/usr/bin/true",
        cwd=repository,
        timeout=7,
        cancel_requested=cancel,
        env={"PATH": os.defpath},
        input_data=b"input",
        retain_output_tail=True,
        read_only_mounts=(mount,),
        read_only_workspace=True,
    )
    assert result.stdout == "bounded"
    request, options = calls[0]
    assert request.workspace == repository and request.cwd == repository
    assert (
        request.resources == resources
        and request.backend_configuration_id == selection.backend_configuration_id
    )
    assert request.input_data == b"input" and request.read_only_workspace
    assert request.retain_output_tail
    assert mount in request.read_only_mounts
    assert options["timeout"] == 7 and options["cancel_requested"] is cancel
    assert events == ["pin", "verify", "verify", "release"]


@pytest.mark.parametrize("missing", ["backend", "pin", "configuration"])
def test_profile_rejects_missing_or_changed_authority_before_launch(
    tmp_path: Path, missing: str
) -> None:
    backend = _fake_backend(tmp_path)
    selected = _selection(backend)
    if missing == "configuration":
        selected = replace(selected, backend_configuration_id="changed")
    with pytest.raises(FleetPlanInvalid, match="backend|workspace authority"):
        native_profile_command_runner(
            tmp_path,
            selected,
            None if missing == "backend" else backend,
            None if missing == "pin" else RepositoryWorkspaceAuthority.pin_repository_subject,
        )


@pytest.mark.parametrize("layout", ["gitfile", "symlink", "commondir"])
def test_external_git_metadata_is_refused_before_git_or_hook_invocation(
    tmp_path: Path, layout: str
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    external = tmp_path / "external.git"
    external.mkdir()
    if layout == "gitfile":
        (repository / ".git").write_text(f"gitdir: {external}\n")
    elif layout == "symlink":
        (repository / ".git").symlink_to(external, target_is_directory=True)
    else:
        (repository / ".git").mkdir()
        (repository / ".git" / "commondir").write_text(str(external))
    backend = _fake_backend(tmp_path)
    with pytest.raises(FleetPlanInvalid, match="repository-local Git metadata"):
        native_profile_command_runner(
            repository,
            _selection(backend),
            backend,
            RepositoryWorkspaceAuthority.pin_repository_subject,
        )


def test_cancelled_followup_does_not_enter_workspace_or_backend(tmp_path: Path) -> None:
    backend = _fake_backend(tmp_path)

    @contextmanager
    def no_pin(path: Path) -> Iterator[Callable[[], None]]:
        pytest.fail("cancelled launch entered workspace")
        yield lambda: None

    runner = native_profile_command_runner(tmp_path, _selection(backend), backend, no_pin)
    with pytest.raises(OperationCancelledError, match="before process launch"):
        runner("/usr/bin/true", cancel_requested=lambda: True)


def test_followup_cannot_reuse_profile_for_another_workspace(tmp_path: Path) -> None:
    approved = tmp_path / "approved"
    approved.mkdir()
    foreign = tmp_path / "foreign"
    foreign.mkdir()
    backend = _fake_backend(tmp_path)
    selected = replace(
        _selection(backend),
        profile=NativeAgentProfile(backend="bubblewrap", workspace_roots=(str(approved),)),
    )
    with pytest.raises(FleetPlanInvalid, match="resolved agent/workspace policy"):
        native_profile_command_runner(
            foreign, selected, backend, RepositoryWorkspaceAuthority.pin_repository_subject
        )


@pytest.mark.parametrize("placement", ["contains-runtime", "inside-runtime"])
def test_writable_native_target_cannot_change_loaded_host_runtime_before_launch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, placement: str
) -> None:
    runtime = tmp_path / "host-runtime"
    repository = runtime / "project" if placement == "inside-runtime" else tmp_path / "project"
    loaded_prefix = runtime if placement == "inside-runtime" else repository / "runtime"
    repository.mkdir(parents=True)
    loaded_prefix.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(sys, "prefix", str(loaded_prefix))
    backend = _fake_backend(tmp_path)

    @contextmanager
    def no_pin(path: Path) -> Iterator[Callable[[], None]]:
        pytest.fail("unapproved writable target entered workspace authority")
        yield lambda: None

    runner = native_profile_command_runner(repository, _selection(backend), backend, no_pin)
    with pytest.raises(FleetPlanInvalid, match="loaded host control-plane or Python runtime"):
        runner("/usr/bin/true")


@pytest.mark.parametrize("read_only_workspace", [False, True])
def test_native_target_supports_sibling_runtime_and_readonly_runtime_inspection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, read_only_workspace: bool
) -> None:
    repository = tmp_path / "project"
    repository.mkdir()
    loaded_prefix = (
        repository / "runtime" if read_only_workspace else tmp_path / "installed-runtime"
    )
    loaded_prefix.mkdir()
    monkeypatch.setattr(sys, "prefix", str(loaded_prefix))
    backend = _fake_backend(tmp_path)
    calls: list[StandardIsolatedProcessRequest] = []

    def execute(request: StandardIsolatedProcessRequest, **kwargs: Any) -> CommandResult:
        request.require_workspace_current()
        calls.append(request)
        return CommandResult(0, "inspected", "")

    monkeypatch.setattr(backend, "execute", execute)
    runner = native_profile_command_runner(
        repository,
        _selection(backend),
        backend,
        RepositoryWorkspaceAuthority.pin_repository_subject,
    )
    assert runner("/usr/bin/true", read_only_workspace=read_only_workspace).returncode == 0
    assert len(calls) == 1 and calls[0].read_only_workspace is read_only_workspace


def _git(repository: Path, *argv: str) -> None:
    subprocess.run(["git", *argv], cwd=repository, check=True, capture_output=True)


@pytest.mark.process
def test_real_verification_and_before_after_final_git_hooks_share_native_boundary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executable = shutil.which("bwrap")
    if executable is None:
        if os.environ.get("OH_GOON_REQUIRE_NATIVE_ISOLATION") == "1":
            pytest.fail("required native bwrap is not installed")
        pytest.skip("native bwrap is not installed")
    repository = tmp_path / "repository"
    repository.mkdir()
    _git(repository, "init", "-q", "-b", "main")
    (repository / "tracked").write_text("source")
    _git(repository, "add", "tracked")
    foreign = tmp_path / "foreign"
    foreign.mkdir()
    secret = foreign / "credential"
    secret.write_text("host secret")
    host_marker = foreign / "escaped"
    backend = BubblewrapProcessIsolation(executable)
    selection = _selection(backend)
    runner = native_profile_command_runner(
        repository, selection, backend, RepositoryWorkspaceAuthority.pin_repository_subject
    )
    try:
        runner("/usr/bin/true", timeout=10)
    except FleetPlanInvalid as exc:
        if os.environ.get("OH_GOON_REQUIRE_NATIVE_ISOLATION") == "1":
            pytest.fail(str(exc))
        pytest.skip(f"native namespace unavailable: {exc}")

    with socket.socket() as host_service:
        host_service.bind(("127.0.0.1", 0))
        host_service.listen()
        port = host_service.getsockname()[1]
        payload = f"""import json, pathlib, socket
assert not pathlib.Path({str(secret)!r}).exists()
try:
    pathlib.Path({str(host_marker)!r}).write_text('escaped')
except OSError:
    pass
else:
    raise AssertionError('host path writable')
try:
    socket.create_connection(('127.0.0.1', {port}), timeout=0.2)
except OSError:
    pass
else:
    raise AssertionError('host service reachable')
"""
        hook = repository / ".git" / "hooks" / "fsmonitor-proof"
        hook.write_text(
            "#!/usr/bin/python3\n"
            + payload
            + "import sys\n"
            + "try:\n    pathlib.Path('observation-mutation').write_text('forbidden')\n"
            + "except OSError:\n    print('fsmonitor-inside-readonly', file=sys.stderr)\n"
            + "else:\n    raise AssertionError('observation workspace writable')\n"
            + "print('token\\0', end='')\n"
        )
        hook.chmod(0o700)
        _git(repository, "config", "core.fsmonitor", str(hook))
        step = ResolvedCommandStep(
            step_id="verify",
            timeout_seconds=10,
            argv=(
                "/usr/bin/python3",
                "-c",
                payload
                + "pathlib.Path('verification-write').write_text('approved')\nprint('verification-inside')",
            ),
        )
        # Verification-only phase Plans preserve the native profile even though
        # their Step list contains no Agent invocation.
        plan = ResolvedFleetPlan.create(
            name="native-verification",
            description="",
            steps=(step,),
            execution=MaintenanceExecutionPolicy(),
            native_agent=selection,
        )
        observed_stderr: list[str] = []
        original_run = GitService.run

        def recorded_run(self: GitService, *argv: str, **kwargs: Any) -> CommandResult:
            command = original_run(self, *argv, **kwargs)
            observed_stderr.append(command.stderr)
            return command

        monkeypatch.setattr(GitService, "run", recorded_run)
        executor = FleetPlanRunExecutor(
            GitService(),
            process_isolation_backends=(),
            standard_process_isolation=backend,
            workspace_pin=RepositoryWorkspaceAuthority.pin_repository_subject,
        )
        executor.prepare_direct_run(
            RepositoryIdentity.model_validate({"path": repository}), plan, cancel_requested=None
        )
        result = executor.execute_direct_run(repository, plan, cancel_requested=None)
        final = executor.observe_repository(repository, plan, None)

    assert "verification-inside" in result.stdout
    assert len(result.steps) == 1 and isinstance(result.steps[0], CompletedPlanStepExecution)
    assert result.steps[0].returncode == 0
    attempt = result.steps[0].attempts[0]
    assert attempt.standard_isolation is not None
    assert attempt.standard_isolation.backend.configuration_id == selection.backend_configuration_id
    assert attempt.egress.mode == "none"
    assert attempt.standard_isolation.resources is None
    assert not attempt.standard_isolation.workspace_read_only
    assert final.is_repository
    assert sum(stderr.count("fsmonitor-inside-readonly") for stderr in observed_stderr) == 3
    assert not (repository / "observation-mutation").exists()
    assert (repository / "verification-write").read_text() == "approved"
    assert not host_marker.exists()


@pytest.mark.process
def test_loaded_python_runtime_preserves_native_venv_interpreter_alias(tmp_path: Path) -> None:
    executable = shutil.which("bwrap")
    if executable is None:
        if os.environ.get("OH_GOON_REQUIRE_NATIVE_ISOLATION") == "1":
            pytest.fail("required native bwrap is not installed")
        pytest.skip("native bwrap is not installed")
    backend = BubblewrapProcessIsolation(executable)
    repository = tmp_path / "repo"
    repository.mkdir()
    runner = native_profile_command_runner(
        repository,
        _selection(backend),
        backend,
        RepositoryWorkspaceAuthority.pin_repository_subject,
    )
    try:
        result = runner(
            sys.executable,
            "-c",
            "import encodings,oh_goon,hashmarks,sys; print(sys.prefix)",
            timeout=15,
        )
    except FleetPlanInvalid as exc:
        if os.environ.get("OH_GOON_REQUIRE_NATIVE_ISOLATION") == "1":
            pytest.fail(str(exc))
        pytest.skip(f"native namespace unavailable: {exc}")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == sys.prefix
    assert result.standard_isolation is not None


@pytest.mark.process
def test_timed_out_verification_keeps_partial_output_elapsed_and_physical_cleanup(
    tmp_path: Path,
) -> None:
    executable = shutil.which("bwrap")
    if executable is None:
        if os.environ.get("OH_GOON_REQUIRE_NATIVE_ISOLATION") == "1":
            pytest.fail("required native bwrap is not installed")
        pytest.skip("native bwrap is not installed")
    backend = BubblewrapProcessIsolation(executable)
    repository = tmp_path / "repo"
    repository.mkdir()
    runtime = StepRuntime(
        git=GitService(),
        extensions=None,
        tools=None,
        cancel_requested=None,
        native_profile=_selection(backend),
        standard_process_isolation=backend,
        workspace_pin=RepositoryWorkspaceAuthority.pin_repository_subject,
    )
    step = ResolvedCommandStep(
        step_id="timed-verification",
        timeout_seconds=0.3,
        argv=(
            "/usr/bin/python3",
            "-c",
            "import sys,time; print('partial verification',flush=True); print('native stderr',file=sys.stderr,flush=True); time.sleep(30)",
        ),
    )
    outcome = execute_direct_step(repository, step, runtime, step_order=1)
    assert outcome.returncode == 124
    assert outcome.stdout == "partial verification\n"
    assert "native stderr" in outcome.stderr and "timed out" in outcome.stderr
    assert outcome.duration_ms >= 300 and outcome.physical_execution_id is not None
    assert outcome.supervisor is not None and outcome.supervisor.final_process_group_quiescent
    assert outcome.standard_isolation is None


@pytest.mark.process
def test_real_scoped_git_fingerprints_do_not_read_outside_workspace_links(tmp_path: Path) -> None:
    executable = shutil.which("bwrap")
    if executable is None:
        if os.environ.get("OH_GOON_REQUIRE_NATIVE_ISOLATION") == "1":
            pytest.fail("required native bwrap is not installed")
        pytest.skip("native bwrap is not installed")
    repository = tmp_path / "repo"
    repository.mkdir()
    _git(repository, "init", "-q", "-b", "main")
    canary = tmp_path / "host-canary"
    canary.write_bytes(b"outside approved workspace")
    pyproject = repository / "pyproject.toml"
    pyproject.symlink_to(canary)
    (repository / "uv.lock").write_bytes(b"version = 1\n")
    backend = BubblewrapProcessIsolation(executable)
    runner = native_profile_command_runner(
        repository,
        _selection(backend),
        backend,
        RepositoryWorkspaceAuthority.pin_repository_subject,
    )
    git = GitService(
        command_runner=runner, observation_runner=partial(runner, read_only_workspace=True)
    )
    observed = git.observe(repository)
    assert observed.is_repository
    assert observed.fingerprint.pyproject_sha256 is None
    assert observed.fingerprint.uv_lock_sha256 == hashlib.sha256(b"version = 1\n").hexdigest()
    pyproject.unlink()
    pyproject.write_bytes(b"[project]\nname='approved'\n")
    regular = git.observe(repository).fingerprint
    assert regular.pyproject_sha256 == hashlib.sha256(b"[project]\nname='approved'\n").hexdigest()
