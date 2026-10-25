from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.models.fleet_plan import (
    ResolvedCommandStep,
    ResolvedFleetPlan,
    UvExecutionEnvironment,
)
from oh_goon.core.models.fleet_plan_execution_policy import CicdExecutionPolicy
from oh_goon.core.models.fleet_plan_isolation import CicdResourcePolicy
from oh_goon.core.models.fleet_plan_step_contract import PlanStepWorkspace, StepWorkspaceSource
from oh_goon.core.models.repository import RepositoryIdentity
from oh_goon.core.path_authority import StrictHostPathAuthority
from oh_goon.engine.command import CommandResult, CommandSupervisorReceipt
from oh_goon.integrations._fleet_plan_step_outcome import settle_step_execution
from oh_goon.integrations.fleet_plan_managed_runtime import (
    prepare_uv_project_runtimes,
)


def _plan(*steps: ResolvedCommandStep) -> ResolvedFleetPlan:
    return ResolvedFleetPlan.create(
        name="uv-native-authority",
        description="",
        execution=CicdExecutionPolicy(
            trust_repository_code=True,
            resources=CicdResourcePolicy(
                cpu_millis=1000,
                memory_mib=1024,
                max_processes=64,
            ),
        ),
        requirements=(),
        steps=steps,
    )


def _step(step_id: str = "package") -> ResolvedCommandStep:
    return ResolvedCommandStep(
        step_id=step_id,
        argv=(
            "uv",
            "run",
            "--frozen",
            "--no-default-groups",
            "--project",
            "backend",
            "--group",
            "build",
            "oh-goon-package-source",
        ),
        uv=UvExecutionEnvironment(project="backend", group="build"),
        timeout_seconds=60,
    )


def _path_authority(tmp_path: Path) -> StrictHostPathAuthority:
    toolchain = tmp_path / "tools"
    toolchain.mkdir()
    for name in ("uv", "ldd"):
        tool = toolchain / name
        tool.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        tool.chmod(0o755)
    return StrictHostPathAuthority.create(
        trusted_roots=(toolchain,),
        tool_search_paths=(toolchain,),
        runtime_inspector=toolchain / "ldd",
    )


def _repository(tmp_path: Path) -> RepositoryIdentity:
    repository = tmp_path / "repo"
    backend = repository / "backend"
    backend.mkdir(parents=True)
    (backend / "pyproject.toml").write_text(
        "[project]\nname='demo'\nversion='1'\n", encoding="utf-8"
    )
    (backend / "uv.lock").write_text("version = 1\n", encoding="utf-8")
    return RepositoryIdentity(path=repository)


def _ok(*argv: str, **_kwargs: object) -> CommandResult:
    return CommandResult(returncode=0, stdout="", stderr="", argv=tuple(argv))


def test_uv_admission_has_no_disposable_native_resolution_owner() -> None:
    import oh_goon.integrations.fleet_plan_managed_runtime as managed_runtime

    assert not hasattr(managed_runtime, "preflight_uv_project_feasibility")


def test_uv_managed_step_requires_repository_workspace_source() -> None:
    producer = ResolvedCommandStep(
        step_id="producer",
        argv=("true", "producer"),
        workspace=PlanStepWorkspace(publish=True),
    )
    consumer = _step("consumer").model_copy(
        update={"workspace": PlanStepWorkspace(source=StepWorkspaceSource(step_id="producer"))}
    )

    with pytest.raises(
        ValueError,
        match="uv-managed command step consumer must use repository workspace source",
    ):
        _plan(producer, consumer)


def test_uv_managed_step_accepts_repository_workspace_source() -> None:
    plan = _plan(_step())

    assert plan.steps[0].workspace.source.kind == "repository"


def test_non_uv_step_can_still_consume_published_workspace() -> None:
    producer = ResolvedCommandStep(
        step_id="producer",
        argv=("true", "producer"),
        workspace=PlanStepWorkspace(publish=True),
    )
    consumer = ResolvedCommandStep(
        step_id="consumer",
        argv=("true", "consumer"),
        workspace=PlanStepWorkspace(source=StepWorkspaceSource(step_id="producer")),
    )

    plan = _plan(producer, consumer)

    assert plan.steps[1].workspace.source.kind == "step"


def test_uv_preparation_acquires_once_per_project_group_and_keeps_only_native_cache(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    path_authority = _path_authority(tmp_path)
    run_root = tmp_path / "run"
    run_root.mkdir()
    calls: list[tuple[tuple[str, ...], dict[str, object]]] = []
    selections: list[Path] = []

    def runner(*argv: str, **kwargs: object) -> CommandResult:
        calls.append((tuple(argv), dict(kwargs)))
        return _ok(*argv)

    def select_python(_uv: Path) -> Path:
        selected = Path("/usr/bin/python3")
        selections.append(selected)
        return selected

    runtimes = prepare_uv_project_runtimes(
        _plan(_step("first"), _step("second")),
        source_root=repository.path,
        run_root=run_root,
        path_authority=path_authority,
        cancel_requested=None,
        command_runner=runner,
        python_resolver=select_python,
    )

    assert set(runtimes) == {"first", "second"}
    assert selections == [Path("/usr/bin/python3")]
    assert len(calls) == 1
    argv, kwargs = calls[0]
    assert argv[1:] == (
        "sync",
        "--project",
        str(repository.path / "backend"),
        "--locked",
        "--no-default-groups",
        "--group",
        "build",
    )
    assert runtimes["first"].cache == runtimes["second"].cache
    assert runtimes["first"].python == Path("/usr/bin/python3")
    assert runtimes["second"].python == Path("/usr/bin/python3")
    assert runtimes["first"].cache.is_dir()
    preparation_environments = run_root / ".preparation" / "uv-environments"
    assert preparation_environments.is_dir()
    assert tuple(preparation_environments.iterdir()) == ()
    assert kwargs["env"]["UV_PROJECT_ENVIRONMENT"] != str(repository.path / "backend" / ".venv")


def test_uv_preparation_preserves_native_tls_and_proxy_transport(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = _repository(tmp_path)
    path_authority = _path_authority(tmp_path)
    run_root = tmp_path / "run"
    run_root.mkdir()
    calls: list[dict[str, object]] = []

    def runner(*argv: str, **kwargs: object) -> CommandResult:
        calls.append(dict(kwargs))
        return _ok(*argv)

    native_transport = {
        "UV_SYSTEM_CERTS": "true",
        "HTTPS_PROXY": "http://proxy.example:8443",
        "HTTP_PROXY": "http://proxy.example:8080",
        "ALL_PROXY": "socks5://proxy.example:1080",
        "NO_PROXY": "localhost,127.0.0.1",
        "SSL_CERT_FILE": "/etc/ssl/certs/corporate.pem",
        "SSL_CERT_DIR": "/etc/ssl/certs",
        "SSL_CLIENT_CERT": "/run/secrets/client.pem",
        "UV_OFFLINE": "1",
        "UV_CACHE_DIR": "/host/uv-cache",
        "UV_PYTHON": "/host/python",
        "UNRELATED_SECRET": "must-not-reach-native-uv",
    }
    for name, value in native_transport.items():
        monkeypatch.setenv(name, value)

    prepare_uv_project_runtimes(
        _plan(_step()),
        source_root=repository.path,
        run_root=run_root,
        path_authority=path_authority,
        cancel_requested=None,
        command_runner=runner,
        python_resolver=lambda _uv: Path("/usr/bin/python3"),
    )

    assert len(calls) == 1
    env = calls[0]["env"]
    assert isinstance(env, dict)
    assert env["UV_SYSTEM_CERTS"] == "true"
    assert env["HTTPS_PROXY"] == "http://proxy.example:8443"
    assert env["HTTP_PROXY"] == "http://proxy.example:8080"
    assert env["ALL_PROXY"] == "socks5://proxy.example:1080"
    assert env["NO_PROXY"] == "localhost,127.0.0.1"
    assert env["SSL_CERT_FILE"] == "/etc/ssl/certs/corporate.pem"
    assert env["SSL_CERT_DIR"] == "/etc/ssl/certs"
    assert env["SSL_CLIENT_CERT"] == "/run/secrets/client.pem"
    assert "UV_OFFLINE" not in env
    assert env["UV_CACHE_DIR"] == str(run_root / ".preparation" / "cache" / "uv")
    assert env["UV_PYTHON"] == "/usr/bin/python3"
    assert "UNRELATED_SECRET" not in env


def test_uv_preparation_preserves_native_uv_configuration_and_credentials_opaquely(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    path_authority = _path_authority(tmp_path)
    run_root = tmp_path / "run"
    run_root.mkdir()
    calls: list[dict[str, object]] = []

    def runner(*argv: str, **kwargs: object) -> CommandResult:
        calls.append(dict(kwargs))
        return _ok(*argv)

    host_environment = {
        "HOME": "/home/operator",
        "USERPROFILE": "/home/operator",
        "XDG_CONFIG_HOME": "/home/operator/.config",
        "PATH": "/home/operator/.local/bin:/usr/bin",
        "UV_CONFIG_FILE": "/secure/native-uv.toml",
        "UV_DEFAULT_INDEX": "https://packages.example/simple",
        "UV_INDEX": "private=https://private.example/simple",
        "UV_INDEX_PRIVATE_USERNAME": "service-account",
        "UV_INDEX_PRIVATE_PASSWORD": "credential-secret",
        "UV_FUTURE_NATIVE_SETTING": "opaque-native-value",
        "UV_KEYRING_PROVIDER": "subprocess",
        "UV_OFFLINE": "1",
        "UV_CACHE_DIR": "/host/uv-cache",
        "UV_PROJECT_ENVIRONMENT": "/host/project-environment",
        "UV_PYTHON": "/host/python",
        "UV_NO_MANAGED_PYTHON": "0",
        "UV_PYTHON_DOWNLOADS": "automatic",
        "UNRELATED_SECRET": "must-not-reach-native-uv",
    }

    runtimes = prepare_uv_project_runtimes(
        _plan(_step()),
        source_root=repository.path,
        run_root=run_root,
        path_authority=path_authority,
        cancel_requested=None,
        command_runner=runner,
        python_resolver=lambda _uv: Path("/usr/bin/python3"),
        host_environment=host_environment,
    )

    assert len(calls) == 1
    env = calls[0]["env"]
    assert isinstance(env, dict)
    assert env["HOME"] == "/home/operator"
    assert env["USERPROFILE"] == "/home/operator"
    assert env["XDG_CONFIG_HOME"] == "/home/operator/.config"
    assert env["PATH"] == "/home/operator/.local/bin:/usr/bin"
    assert env["UV_CONFIG_FILE"] == "/secure/native-uv.toml"
    assert env["UV_DEFAULT_INDEX"] == "https://packages.example/simple"
    assert env["UV_INDEX"] == "private=https://private.example/simple"
    assert env["UV_INDEX_PRIVATE_USERNAME"] == "service-account"
    assert env["UV_INDEX_PRIVATE_PASSWORD"] == "credential-secret"
    assert env["UV_FUTURE_NATIVE_SETTING"] == "opaque-native-value"
    assert env["UV_KEYRING_PROVIDER"] == "subprocess"

    assert "UV_OFFLINE" not in env
    assert env["UV_CACHE_DIR"] == str(run_root / ".preparation" / "cache" / "uv")
    assert env["UV_PROJECT_ENVIRONMENT"].startswith(
        str(run_root / ".preparation" / "uv-environments")
    )
    assert env["UV_PYTHON"] == "/usr/bin/python3"
    assert env["UV_NO_MANAGED_PYTHON"] == "1"
    assert env["UV_PYTHON_DOWNLOADS"] == "never"
    assert "UNRELATED_SECRET" not in env

    runtime = runtimes["package"]
    assert "credential-secret" not in repr(runtime)
    assert tuple((run_root / ".preparation" / "config").iterdir()) == ()


def test_uv_preparation_can_execute_native_subprocess_credential_helper_from_operator_path(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    run_root = tmp_path / "run"
    run_root.mkdir()
    helpers = tmp_path / "helpers"
    helpers.mkdir()
    keyring = helpers / "keyring"
    keyring.write_text("#!/bin/sh\nprintf 'credential-from-helper\\n'\n", encoding="utf-8")
    keyring.chmod(0o755)

    toolchain = tmp_path / "tools"
    toolchain.mkdir()
    uv = toolchain / "uv"
    uv.write_text(
        "#!/bin/sh\n"
        'test "$UV_KEYRING_PROVIDER" = subprocess || exit 41\n'
        'test -z "${UNRELATED_SECRET+x}" || exit 42\n'
        'test "$(keyring get https://private.example service-account)" = '
        "credential-from-helper || exit 43\n",
        encoding="utf-8",
    )
    uv.chmod(0o755)
    ldd = toolchain / "ldd"
    ldd.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    ldd.chmod(0o755)
    path_authority = StrictHostPathAuthority.create(
        trusted_roots=(toolchain,),
        tool_search_paths=(toolchain,),
        runtime_inspector=ldd,
    )

    runtimes = prepare_uv_project_runtimes(
        _plan(_step()),
        source_root=repository.path,
        run_root=run_root,
        path_authority=path_authority,
        cancel_requested=None,
        python_resolver=lambda _uv: Path("/usr/bin/python3"),
        host_environment={
            "PATH": str(helpers),
            "UV_KEYRING_PROVIDER": "subprocess",
            "UNRELATED_SECRET": "must-not-reach-native-uv",
        },
    )

    assert runtimes["package"].python == Path("/usr/bin/python3")


def test_uv_preparation_failure_stops_before_application_work(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    path_authority = _path_authority(tmp_path)
    run_root = tmp_path / "run"
    run_root.mkdir()
    calls: list[tuple[str, ...]] = []

    def reject(*argv: str, **_kwargs: object) -> CommandResult:
        calls.append(tuple(argv))
        return CommandResult(
            returncode=2,
            stdout="",
            stderr="locked distribution unavailable to native uv",
            argv=tuple(argv),
        )

    with pytest.raises(
        FleetPlanInvalid,
        match="Native uv preparation rejected.*locked distribution unavailable",
    ):
        prepare_uv_project_runtimes(
            _plan(_step()),
            source_root=repository.path,
            run_root=run_root,
            path_authority=path_authority,
            cancel_requested=None,
            command_runner=reject,
            python_resolver=lambda _uv: Path("/usr/bin/python3"),
        )

    assert len(calls) == 1
    assert calls[0][1:] == (
        "sync",
        "--project",
        str(repository.path / "backend"),
        "--locked",
        "--no-default-groups",
        "--group",
        "build",
    )
    assert tuple((run_root / ".preparation" / "uv-environments").glob("*")) == ()


@pytest.mark.parametrize("quiescent", (True, False, None))
def test_strict_uv_attempt_materializes_offline_then_runs_no_sync(
    tmp_path: Path,
    quiescent: bool | None,
) -> None:
    from oh_goon.core.path_layouts import AttemptPathLayout
    from oh_goon.engine.git import GitService
    from oh_goon.integrations.fleet_plan_managed_runtime import UvProjectManagedRuntime
    from oh_goon.integrations.fleet_plan_step_execution import (
        StepRuntime,
        _execute_command_step,
        _prepare_strict_uv_attempt,
        _StepProcess,
    )

    repository = tmp_path / "workspace"
    (repository / "backend").mkdir(parents=True)
    (repository / "tools").mkdir()
    paths = AttemptPathLayout.from_root(tmp_path / "attempt")
    for path in (paths.root, paths.home, paths.tmp, paths.cache, paths.config):
        path.mkdir(parents=True, exist_ok=True)
    calls: list[tuple[tuple[str, ...], dict[str, object]]] = []

    def runner(*argv: str, **kwargs: object) -> CommandResult:
        calls.append((tuple(argv), dict(kwargs)))
        if argv[:2] == ("uv", "sync"):
            return CommandResult(
                argv=argv,
                returncode=0,
                stdout="",
                stderr="",
                supervisor=cast(
                    CommandSupervisorReceipt,
                    CommandSupervisorReceipt(final_process_group_quiescent=quiescent)
                    if quiescent is not None
                    else None,
                ),
            )
        return _ok(*argv)

    step = _step().model_copy(update={"working_directory": "tools"})
    process = _StepProcess(
        paths=paths,
        environment={"UV_CACHE_DIR": "/cache/uv"},
        host_environment={},
        command_runner=runner,
        git_command_runner=runner,
        managed_runtime=UvProjectManagedRuntime(
            project="backend",
            group="build",
            cache=tmp_path / "prepared-cache",
            python=Path("/usr/bin/python3"),
        ),
    )
    runtime = StepRuntime(
        git=GitService(),
        extensions=None,
        tools=None,
        cancel_requested=None,
    )

    prepared, failure = _prepare_strict_uv_attempt(repository, step, process, runtime)
    if quiescent is not True:
        assert failure is not None and len(calls) == 1
        settlement = settle_step_execution(step, failure)
        assert settlement.outcome == "INCOMPLETE"
        assert settlement.cleanup == ("UNVERIFIED" if quiescent is False else "UNAVAILABLE")
        assert failure.observed_returncode == 0
        return
    assert failure is None
    outcome = _execute_command_step(repository, step, prepared, runtime)
    assert outcome.returncode == 0

    assert calls[0][0] == (
        "uv",
        "sync",
        "--project",
        str(repository / "backend"),
        "--frozen",
        "--offline",
        "--link-mode",
        "copy",
        "--no-default-groups",
        "--group",
        "build",
    )
    assert calls[0][1]["cwd"] == repository
    prep_environment = calls[0][1]["env"]
    assert isinstance(prep_environment, dict)
    assert prep_environment["UV_PROJECT_ENVIRONMENT"] == str(paths.cache / "uv-project-environment")

    assert calls[1][0] == (
        "uv",
        "run",
        "--no-sync",
        "--frozen",
        "--no-default-groups",
        "--project",
        str(repository / "backend"),
        "--group",
        "build",
        "oh-goon-package-source",
    )
    assert calls[1][1]["env"] == prep_environment
    assert calls[1][1]["cwd"] == repository / "tools"


def test_uv_run_preparation_is_the_only_implicit_cache_owner() -> None:
    from oh_goon.core.models.fleet_plan_step_contract import PlanStepCache
    from oh_goon.services.fleet_plan_resolver import _validate_backend_uv_cache_authority

    first = _step("first")
    second = _step("second")

    resolved = _validate_backend_uv_cache_authority((first, second))

    assert resolved == (first, second)
    assert all(step.cache == PlanStepCache() for step in resolved)


def test_strict_uv_materialization_failure_prevents_application_command(
    tmp_path: Path,
) -> None:
    from oh_goon.core.path_layouts import AttemptPathLayout
    from oh_goon.engine.git import GitService
    from oh_goon.integrations.fleet_plan_managed_runtime import UvProjectManagedRuntime
    from oh_goon.integrations.fleet_plan_step_execution import (
        StepRuntime,
        _prepare_strict_uv_attempt,
        _StepProcess,
    )

    repository = tmp_path / "workspace"
    (repository / "backend").mkdir(parents=True)
    paths = AttemptPathLayout.from_root(tmp_path / "attempt")
    for path in (paths.root, paths.home, paths.tmp, paths.cache, paths.config):
        path.mkdir(parents=True, exist_ok=True)
    calls: list[tuple[str, ...]] = []

    def reject(*argv: str, **_kwargs: object) -> CommandResult:
        calls.append(tuple(argv))
        return CommandResult(
            returncode=2,
            stdout="",
            stderr="offline cache cannot satisfy locked project",
            argv=tuple(argv),
        )

    step = _step()
    process = _StepProcess(
        paths=paths,
        environment={},
        host_environment={},
        command_runner=reject,
        git_command_runner=reject,
        managed_runtime=UvProjectManagedRuntime(
            project="backend",
            group="build",
            cache=tmp_path / "prepared-cache",
            python=Path("/usr/bin/python3"),
        ),
    )
    runtime = StepRuntime(git=GitService(), extensions=None, tools=None, cancel_requested=None)

    _prepared, failure = _prepare_strict_uv_attempt(repository, step, process, runtime)
    assert failure is not None
    assert failure.returncode == 2
    assert "offline cache cannot satisfy" in failure.stderr
    assert calls == [
        (
            "uv",
            "sync",
            "--project",
            str(repository / "backend"),
            "--frozen",
            "--offline",
            "--link-mode",
            "copy",
            "--no-default-groups",
            "--group",
            "build",
        )
    ]


def test_uv_prepared_cache_seed_has_identity_owner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from oh_goon.integrations import fleet_plan_run
    from oh_goon.integrations.content_snapshot import SnapshotBinding, SnapshotIdentity
    from oh_goon.integrations.fleet_plan_managed_runtime import UvProjectManagedRuntime

    prepared_cache = tmp_path / "prepared-cache"
    prepared_cache.mkdir()
    frozen_cache = tmp_path / "frozen-cache"
    frozen_cache.mkdir()
    binding_identity = SnapshotIdentity(sha256="a" * 64, byte_count=12, entry_count=1)
    attempt_identity = SnapshotIdentity(sha256="b" * 64, byte_count=12, entry_count=2)
    binding = SnapshotBinding(
        declared_path=prepared_cache,
        snapshot_path=frozen_cache,
        identity=binding_identity,
    )
    runtime = UvProjectManagedRuntime(
        project="backend",
        group="build",
        cache=prepared_cache,
        python=Path("/usr/bin/python3"),
    )
    attempt_cache = tmp_path / "attempt-cache"

    def copy_frozen(source: Path, destination: Path, **_kwargs: object) -> SnapshotIdentity:
        assert source == frozen_cache
        destination.mkdir(parents=True)
        (destination / "wheel.bin").write_bytes(b"locked-wheel")
        return binding_identity

    def identify(path: Path, **_kwargs: object) -> SnapshotIdentity:
        assert path == attempt_cache
        return attempt_identity

    monkeypatch.setattr(fleet_plan_run, "copy_frozen_directory", copy_frozen)
    monkeypatch.setattr(fleet_plan_run, "snapshot_identity", identify)

    observed = fleet_plan_run._seed_uv_prepared_cache(
        attempt_cache,
        runtime,
        (binding,),
        cancel_requested=None,
    )

    assert (attempt_cache / "uv" / "wheel.bin").read_bytes() == b"locked-wheel"
    assert observed == attempt_identity


def test_physical_attempt_consumes_prepared_uv_cache_and_binds_result_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from types import SimpleNamespace

    from oh_goon.integrations import fleet_plan_run
    from oh_goon.integrations.content_snapshot import SnapshotBinding, SnapshotIdentity
    from oh_goon.integrations.fleet_plan_managed_runtime import UvProjectManagedRuntime

    source = tmp_path / "source"
    source.mkdir()
    (source / "payload.txt").write_text("source\n", encoding="utf-8")
    source_identity = SnapshotIdentity(sha256="1" * 64, byte_count=7, entry_count=1)

    prepared_cache = tmp_path / "prepared-cache"
    prepared_cache.mkdir()
    frozen_cache = tmp_path / "frozen-cache"
    frozen_cache.mkdir()
    (frozen_cache / "wheel.bin").write_bytes(b"locked-wheel")
    binding_identity = SnapshotIdentity(sha256="2" * 64, byte_count=12, entry_count=1)
    attempt_identity = SnapshotIdentity(sha256="3" * 64, byte_count=12, entry_count=2)
    binding = SnapshotBinding(
        declared_path=prepared_cache,
        snapshot_path=frozen_cache,
        identity=binding_identity,
    )
    runtime = UvProjectManagedRuntime(
        project="backend",
        group="build",
        cache=prepared_cache,
        python=Path("/usr/bin/python3"),
    )
    step = _step()
    attempt_root = tmp_path / "attempt-1"

    class _Lineage:
        def source(self, _step: object):
            return source, source_identity

        def seed_cache(self, _step: object, cache: Path):
            cache.mkdir()
            return SnapshotIdentity(sha256="0" * 64, byte_count=0, entry_count=0)

        def read_only_cache_binding(self, _step: object):
            return None

    step_binding = SimpleNamespace(
        managed_runtime=runtime,
        sandbox_bindings=(binding,),
        hosts=(),
        execution_authority=object(),
        egress=None,
    )
    inputs = SimpleNamespace(
        step_binding=lambda _step: step_binding,
        host_tools_for=lambda _step: (),
        host_environment_for=lambda _step: {},
        runtime_root=None,
        isolation_backend=None,
        resources=None,
    )
    layout = SimpleNamespace(attempt_root=lambda _step_id, _attempt_number: attempt_root)

    prepared = object.__new__(fleet_plan_run.PreparedPlanRun)
    prepared._lineage = _Lineage()
    prepared._layout = layout
    prepared._inputs = inputs
    prepared._cancel_requested = None

    monkeypatch.setattr(fleet_plan_run, "_write_minimal_etc", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        fleet_plan_run,
        "_clear_declared_publish_channels",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        fleet_plan_run,
        "bind_step_executable_materialization",
        lambda *args, **kwargs: object(),
    )

    def copy_frozen(source_path: Path, destination: Path, **_kwargs: object) -> SnapshotIdentity:
        destination.mkdir(parents=True)
        if source_path == source:
            (destination / "payload.txt").write_text("source\n", encoding="utf-8")
            return source_identity
        assert source_path == frozen_cache
        (destination / "wheel.bin").write_bytes(b"locked-wheel")
        return binding_identity

    def identify(path: Path, **_kwargs: object) -> SnapshotIdentity:
        assert path == attempt_root / "cache"
        return attempt_identity

    monkeypatch.setattr(fleet_plan_run, "copy_frozen_directory", copy_frozen)
    monkeypatch.setattr(fleet_plan_run, "snapshot_identity", identify)

    attempt = prepared._create_physical_attempt(step, 1)

    assert (attempt.paths.cache / "uv" / "wheel.bin").read_bytes() == b"locked-wheel"
    assert attempt.cache_identity == attempt_identity
    assert attempt.managed_runtime == runtime
    assert attempt.attempt_number == 1


def test_strict_uv_attempt_copy_link_mode_prevents_hard_linked_cache_output(
    tmp_path: Path,
) -> None:
    import os
    import shutil
    from types import SimpleNamespace

    from oh_goon.core.path_layouts import AttemptPathLayout
    from oh_goon.engine.git import GitService
    from oh_goon.integrations.fleet_plan_managed_runtime import UvProjectManagedRuntime
    from oh_goon.integrations.fleet_plan_run import FleetPlanStepAttempt
    from oh_goon.integrations.fleet_plan_step_execution import (
        StepRuntime,
        _prepare_strict_uv_attempt,
        _StepProcess,
    )

    paths = AttemptPathLayout.from_root(tmp_path / "attempt")
    for path in (
        paths.root,
        paths.workspace,
        paths.home,
        paths.tmp,
        paths.cache,
        paths.config,
    ):
        path.mkdir(parents=True, exist_ok=True)
    (paths.workspace / "backend").mkdir()

    prepared_cache = tmp_path / "prepared-cache"
    prepared_cache.mkdir()
    cached_extension = prepared_cache / "_cffi_backend.cpython-312-x86_64-linux-gnu.so"
    cached_extension.write_bytes(b"native-extension")
    calls: list[tuple[str, ...]] = []

    def runner(*argv: str, **kwargs: object) -> CommandResult:
        calls.append(tuple(argv))
        environment = kwargs["env"]
        assert isinstance(environment, dict)
        installed = (
            Path(environment["UV_PROJECT_ENVIRONMENT"])
            / "lib/python3.12/site-packages/_cffi_backend.cpython-312-x86_64-linux-gnu.so"
        )
        installed.parent.mkdir(parents=True, exist_ok=True)
        link_mode_index = argv.index("--link-mode")
        if argv[link_mode_index + 1] == "copy":
            shutil.copyfile(cached_extension, installed)
        else:  # pragma: no cover - the regression requires native copy mode
            os.link(cached_extension, installed)
        return _ok(*argv)

    process = _StepProcess(
        paths=paths,
        environment={"UV_CACHE_DIR": str(prepared_cache)},
        host_environment={},
        command_runner=runner,
        git_command_runner=runner,
        managed_runtime=UvProjectManagedRuntime(
            project="backend",
            group="build",
            cache=prepared_cache,
            python=Path("/usr/bin/python3"),
        ),
    )
    runtime = StepRuntime(git=GitService(), extensions=None, tools=None, cancel_requested=None)

    prepared, failure = _prepare_strict_uv_attempt(paths.workspace, _step(), process, runtime)

    assert failure is None
    assert calls == [
        (
            "uv",
            "sync",
            "--project",
            str(paths.workspace / "backend"),
            "--frozen",
            "--offline",
            "--link-mode",
            "copy",
            "--no-default-groups",
            "--group",
            "build",
        )
    ]
    installed = (
        paths.cache
        / "uv-project-environment/lib/python3.12/site-packages/_cffi_backend.cpython-312-x86_64-linux-gnu.so"
    )
    assert installed.stat().st_nlink == 1
    assert cached_extension.stat().st_nlink == 1

    runtime_root = tmp_path / "runtime-root"
    runtime_root.mkdir()
    authority = object()
    attempt = SimpleNamespace(
        paths=paths,
        runtime_root=SimpleNamespace(root=runtime_root),
        host_tools=(),
        sandbox_bindings=(),
        _output_publication_authority=authority,
    )

    sealed = FleetPlanStepAttempt.seal_owned_outputs(attempt)  # type: ignore[arg-type]

    assert sealed.cache == paths.cache.resolve()
    assert sealed.workspace == paths.workspace.resolve()
    assert sealed._authority is authority
