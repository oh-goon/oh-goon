"""Native build namespace proof; this does not qualify an untrusted VM executor."""

from __future__ import annotations

import json
import os
import shutil
import socket
from pathlib import Path, PurePosixPath
from typing import Any

import pytest
import yaml

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.models.fleet_plan import ResolvedCommandStep
from oh_goon.core.models.repository import RepositoryIdentity
from oh_goon.core.path_layouts import AttemptPathLayout, RunPathLayout
from oh_goon.engine.command import CommandResult, run_command
from oh_goon.engine.git import GitService
from oh_goon.integrations.content_snapshot import snapshot_identity
from oh_goon.integrations.fleet_plan_bubblewrap_isolation import BubblewrapProcessIsolation
from oh_goon.integrations.fleet_plan_managed_runtime import UvProjectManagedRuntime
from oh_goon.integrations.fleet_plan_process_isolation import (
    StandardIsolatedProcessRequest,
    StandardReadOnlyMount,
)
from oh_goon.integrations.fleet_plan_step_execution import (
    StepRuntime,
    _execute_command_step,
    _prepare_python_project_attempt,
    _prepare_strict_uv_attempt,
    _StepProcess,
)
from oh_goon.integrations.python_goon_projects import materialize_plan_projects
from oh_goon.services.fleet_plan_resolver import FleetPlanResolver
from tests.factories.fleet_plan_executor import prepare_physical_test_run
from tests.factories.python_goon_projects import write_isolated_build_project

pytestmark = pytest.mark.process


@pytest.mark.skipif(
    shutil.which("uv") is None or shutil.which("bwrap") is None,
    reason="native uv and Bubblewrap required",
)
@pytest.mark.parametrize("stale_lock", (False, True))
def test_native_untrusted_build_uses_private_copy_and_installed_command(
    tmp_path: Path, stale_lock: bool
) -> None:
    project = tmp_path / "authoring"
    write_isolated_build_project(project, stale_lock=stale_lock)
    plan = FleetPlanResolver().resolve(project / "goon.yml")
    step = plan.steps[0]
    assert isinstance(step, ResolvedCommandStep)
    target = tmp_path / "target"
    target.mkdir()
    (target / "subject.txt").write_text("independent target")
    secret = tmp_path / "host-private-secret"
    secret.write_text("never mounted")
    uv_executable = shutil.which("uv")
    bwrap_executable = shutil.which("bwrap")
    assert uv_executable is not None and bwrap_executable is not None
    uv = Path(uv_executable).resolve(strict=True)
    backend = BubblewrapProcessIsolation(bwrap_executable)

    with (
        prepare_physical_test_run(
            RepositoryIdentity.model_validate({"path": target}), plan
        ) as prepared_run,
        socket.socket() as listener,
    ):
        attempt = prepared_run.create_attempt(step, 1)
        managed = attempt.managed_runtime
        assert isinstance(managed, UvProjectManagedRuntime)
        assert managed.isolated_preparation and managed.package_source is not None
        source = managed.package_source
        original = snapshot_identity(source)
        assert snapshot_identity(attempt.paths.uv_build_project) == original
        assert not (project / "backend-build.json").exists()
        assert not (source / "backend-build.json").exists()
        assert not (attempt.paths.uv_build_project / "backend-build.json").exists()
        assert not tuple((attempt.paths.cache / "uv").iterdir())
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]
        # Establish that the same host endpoint is reachable outside the namespace.
        with socket.create_connection(("127.0.0.1", port)), listener.accept()[0]:
            pass
        mounts = [StandardReadOnlyMount(source, PurePosixPath(source))]
        if not uv.is_relative_to("/usr"):
            mounts.append(StandardReadOnlyMount(uv, PurePosixPath(uv)))
        environment = {
            "PATH": os.defpath,
            "UV_CACHE_DIR": str(attempt.paths.cache / "uv"),
            "UV_PYTHON": str(managed.python),
            "UV_NO_MANAGED_PYTHON": "1",
            "UV_PYTHON_DOWNLOADS": "never",
            "UV_OFFLINE": "1",
            "PROBE_CAPTURED_SOURCE": str(source),
            "PROBE_HOST_SECRET": str(secret),
            "PROBE_HOST_PORT": str(port),
        }
        preflight = StandardIsolatedProcessRequest(
            workspace=attempt.paths.root,
            argv=("/bin/true",),
            cwd=attempt.paths.workspace,
            env=environment,
            backend_configuration_id=backend.configuration_identity(),
            require_workspace_current=lambda: None,
            read_only_mounts=tuple(mounts),
        )
        try:
            backend.require_standard_available(preflight)
        except FleetPlanInvalid as exc:
            if os.environ.get("OH_GOON_REQUIRE_NATIVE_ISOLATION") == "1":
                pytest.fail(str(exc))
            pytest.skip(f"native namespace proof unavailable: {exc}")
        launches: list[tuple[str, ...]] = []

        def runner(*argv: str, **options: Any) -> CommandResult:
            assert argv[0] == "uv"
            launches.append(argv)
            return backend.execute(
                StandardIsolatedProcessRequest(
                    workspace=attempt.paths.root,
                    argv=(str(uv), *argv[1:]),
                    cwd=options["cwd"],
                    env=options["env"],
                    backend_configuration_id=backend.configuration_identity(),
                    require_workspace_current=lambda: None,
                    read_only_mounts=tuple(mounts),
                ),
                timeout=options["timeout"],
                cancel_requested=options["cancel_requested"],
                runtime_supervision=options.get("runtime_supervision"),
            )

        process = _StepProcess(
            paths=attempt.paths,
            environment=environment,
            host_environment={},
            command_runner=runner,
            git_command_runner=runner,
            managed_runtime=managed,
        )
        runtime = StepRuntime(git=GitService(), extensions=None, tools=None, cancel_requested=None)
        installed, failure = _prepare_strict_uv_attempt(
            attempt.paths.workspace, step, process, runtime
        )
        if stale_lock:
            assert failure is not None and failure.returncode != 0
            assert "lockfile" in failure.stderr.lower()
            assert len(launches) == 1
            assert not (attempt.paths.uv_build_project / "backend-build.json").exists()
            assert not (attempt.paths.workspace / "isolated-result.json").exists()
        else:
            assert failure is None
            observed = json.loads(
                (attempt.paths.uv_build_project / "backend-build.json").read_text()
            )
            assert observed == {
                "private_write": True,
                "readonly_source": True,
                "host_secret_absent": True,
                "network_denied": True,
            }
            outcome = _execute_command_step(attempt.paths.workspace, step, installed, runtime)
            assert outcome.returncode == 0, outcome.stderr
            assert "installed-command-ran" in outcome.stdout
            result = json.loads((attempt.paths.workspace / "isolated-result.json").read_text())
            assert result["dev_dependency_absent"] is True
            assert "uv-project-environment" in result["installed_module"]
            assert len(launches) == 2
        assert snapshot_identity(source) == original
        assert not (project / "backend-build.json").exists()
        assert not (source / "backend-host-write").exists()
        assert secret.read_text() == "never mounted"


@pytest.mark.skipif(shutil.which("uv") is None, reason="native uv required")
def test_native_trusted_repeated_imports_build_private_copies_without_mutating_source(
    tmp_path: Path,
) -> None:
    project = tmp_path / "catalog/general/isolated-build"
    write_isolated_build_project(project)
    goon = project / "goon.yml"
    declaration = yaml.safe_load(goon.read_text())
    declaration.pop("execution")
    goon.write_text(yaml.safe_dump(declaration))
    source = tmp_path / "plan.yml"
    source.write_text(
        "goons:\n- import: general/isolated-build\n  from: catalog\n"
        "- import: general/isolated-build\n  from: catalog\n"
    )
    plan = FleetPlanResolver().resolve(source)
    layout = RunPathLayout.from_root(tmp_path / "captured")
    sources = materialize_plan_projects(plan, layout)
    assert len(sources) == 1
    captured = next(iter(sources.values()))
    original = snapshot_identity(captured)
    uv_executable = shutil.which("uv")
    assert uv_executable is not None
    uv = Path(uv_executable).resolve(strict=True)

    def runner(*argv: str, **options: Any) -> CommandResult:
        assert argv[0] == "uv"
        return run_command(
            str(uv),
            *argv[1:],
            **options,
            require_process_group_quiescence=True,
            max_capture_bytes_per_stream=1024 * 1024,
        )

    runtime = StepRuntime(
        git=GitService(),
        extensions=None,
        tools=None,
        cancel_requested=None,
        project_sources=sources,
    )
    environments: set[str] = set()
    for index, step in enumerate(plan.steps):
        assert isinstance(step, ResolvedCommandStep)
        paths = AttemptPathLayout.from_root(tmp_path / f"direct-attempt-{index}")
        for path in (paths.workspace, paths.home, paths.tmp, paths.cache, paths.config):
            path.mkdir(parents=True, exist_ok=True)
        process = _StepProcess(
            paths=paths,
            environment={"UV_OFFLINE": "1"},
            host_environment={},
            command_runner=runner,
            git_command_runner=runner,
            managed_runtime=None,
        )
        installed, failure = _prepare_python_project_attempt(
            paths.workspace, step, process, runtime
        )
        assert failure is None
        assert json.loads((paths.uv_build_project / "backend-build.json").read_text()) == {
            "private_write": True
        }
        outcome = _execute_command_step(paths.workspace, step, installed, runtime)
        assert outcome.returncode == 0, outcome.stderr
        result = json.loads((paths.workspace / "isolated-result.json").read_text())
        assert result["dev_dependency_absent"] is True
        environments.add(result["installed_module"])
        assert snapshot_identity(captured) == original
        assert not (captured / "backend-build.json").exists()
        assert not (project / "backend-build.json").exists()
    assert len(environments) == 2
