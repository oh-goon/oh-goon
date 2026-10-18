from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
from dataclasses import replace
from pathlib import Path, PurePosixPath

import pytest

from oh_goon.core.fleet_plan_tool_requirements import shell_runtime_support_tool_names
from oh_goon.core.models.fleet_plan import ResolvedCommandStep
from oh_goon.core.models.fleet_plan_step_contract import PlanStepCapabilities
from oh_goon.core.models.repository import RepositoryIdentity
from oh_goon.integrations._fleet_plan_opensandbox_transport import _request_payload
from oh_goon.integrations._fleet_plan_sandbox_launch import sandbox_process_launch
from oh_goon.integrations.content_snapshot import SnapshotBinding, snapshot_identity
from oh_goon.integrations.fleet_plan_atomic_runtime_authority import bind_atomic_runtime_authority
from oh_goon.integrations.fleet_plan_bubblewrap_isolation import BubblewrapProcessIsolation
from oh_goon.integrations.fleet_plan_process_isolation import (
    IsolatedProcessRequest,
)
from oh_goon.integrations.fleet_plan_runtime_authority import (
    observed_runtime_authority_payload,
    reconcile_runtime_authority,
)
from oh_goon.repository_tooling import opensandbox_attempt_runner as remote_runner
from oh_goon.repository_tooling.opensandbox_attempt_runner import _prepare_root
from tests.factories.cicd_repository import seed_cicd_repository
from tests.factories.fleet_plan_executor import (
    TEST_ISOLATION_BACKEND,
    prepare_physical_test_run,
    strict_test_plan,
)
from tests.factories.path_authority import TEST_PATH_AUTHORITY


def test_backends_compile_the_same_fleet_attempt_semantics(tmp_path: Path) -> None:
    repository = tmp_path / "repo"
    seed_cicd_repository(repository)
    (repository / "tool").write_text("payload", encoding="utf-8")
    step = ResolvedCommandStep(timeout_seconds=3600, step_id="check", argv=("./tool", "arg"))
    bubblewrap = BubblewrapProcessIsolation(executable="/usr/bin/true")

    with prepare_physical_test_run(
        RepositoryIdentity(path=repository),
        strict_test_plan(step),
        isolation_backend=TEST_ISOLATION_BACKEND,
    ) as run:
        attempt = run.create_attempt(step, 1)
        request = IsolatedProcessRequest(
            attempt=attempt,
            argv=step.argv,
            cwd=attempt.paths.workspace,
            env={"HOME": "/host/home", "WORK_FILE": str(attempt.paths.workspace / "work.txt")},
        )

        launch = sandbox_process_launch(request)
        with bind_atomic_runtime_authority(request, launch) as atomic:
            atomic_files = atomic.bubblewrap_bindings
            bwrap_argv, bwrap_env = bubblewrap._command(
                request, launch=launch, atomic_authority=atomic, network_prefix=()
            )
        separator = bwrap_argv.index("--")
        opensandbox_payload = _request_payload(
            request, timeout=12.0, staging_root=tmp_path / "input-main"
        )

    assert bwrap_argv[separator + 1 :] == tuple(opensandbox_payload["argv"])
    assert bwrap_env == opensandbox_payload["env"]
    assert bwrap_env["HOME"] == "/home/fleet"
    assert bwrap_env["PATH"] == "/toolchain/bin"
    assert bwrap_env["WORK_FILE"] == "/workspace/work.txt"
    assert opensandbox_payload["cwd"] == "/workspace"
    assert opensandbox_payload["egress"] == {"mode": "none"}
    assert not any(str(value).startswith("/proc/self/fd/") for value in bwrap_argv)
    for runtime_file in atomic_files:
        descriptor = str(runtime_file.descriptor)
        index = bwrap_argv.index(descriptor)
        assert bwrap_argv[index - 1] == "--ro-bind-data"
        assert bwrap_argv[index - 3] == "--perms"
        assert bwrap_argv[index - 2] == f"{runtime_file.mode:o}"
        assert bwrap_argv[index + 1] == str(runtime_file.sandbox_path)
        assert index < separator


def _assert_toolchain_payloads(
    shell_payload: dict[str, object], plain_payload: dict[str, object]
) -> None:
    shell_tool_names = [item["name"] for item in shell_payload["toolchain"]]
    assert shell_tool_names == sorted({"sh", *shell_runtime_support_tool_names(("sh",))})
    assert [item["name"] for item in plain_payload["toolchain"]] == ["true"]
    assert set(shell_runtime_support_tool_names(("sh",))).isdisjoint({"curl", "git", "npm", "uv"})


def _prepare_remote_root(
    tmp_path: Path, *, label: str, staging: Path, payload: dict[str, object]
) -> Path:
    fleet = tmp_path / f"{label}-fleet"
    shutil.copytree(staging, fleet / "input")
    root = fleet / "rootfs"
    _prepare_root(payload, root)
    return root


def test_step_tool_authority_is_consistent_across_local_and_remote_isolation(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repo"
    seed_cicd_repository(repository)
    shell_step = ResolvedCommandStep(
        timeout_seconds=3600,
        step_id="shell-owner",
        argv=("true",),
        capabilities=PlanStepCapabilities(tools=("sh",)),
    )
    plain_step = ResolvedCommandStep(
        timeout_seconds=3600,
        step_id="plain",
        argv=("true",),
    )
    bubblewrap = BubblewrapProcessIsolation(executable="/usr/bin/true")

    with prepare_physical_test_run(
        RepositoryIdentity(path=repository),
        strict_test_plan(shell_step, plain_step),
        isolation_backend=TEST_ISOLATION_BACKEND,
        path_authority=TEST_PATH_AUTHORITY,
    ) as run:
        shell_attempt = run.create_attempt(shell_step, 1)
        plain_attempt = run.create_attempt(plain_step, 1)
        shell_request = IsolatedProcessRequest(
            attempt=shell_attempt,
            argv=shell_step.argv,
            cwd=shell_attempt.paths.workspace,
            env={},
        )
        plain_request = IsolatedProcessRequest(
            attempt=plain_attempt,
            argv=plain_step.argv,
            cwd=plain_attempt.paths.workspace,
            env={},
        )
        shell_launch = sandbox_process_launch(shell_request)
        plain_launch = sandbox_process_launch(plain_request)
        with bind_atomic_runtime_authority(shell_request, shell_launch) as shell_atomic:
            shell_argv, _ = bubblewrap._command(
                shell_request, launch=shell_launch, atomic_authority=shell_atomic, network_prefix=()
            )
        with bind_atomic_runtime_authority(plain_request, plain_launch) as plain_atomic:
            plain_argv, _ = bubblewrap._command(
                plain_request, launch=plain_launch, atomic_authority=plain_atomic, network_prefix=()
            )
        shell_payload = _request_payload(
            shell_request, timeout=12.0, staging_root=tmp_path / "input-shell"
        )
        plain_payload = _request_payload(
            plain_request, timeout=12.0, staging_root=tmp_path / "input-plain"
        )

    assert "/bin/sh" in shell_argv
    assert "/bin/sh" not in plain_argv
    _assert_toolchain_payloads(shell_payload, plain_payload)

    # Runner file references are relative to root.parent, mirroring /fleet/rootfs
    # and /fleet/input in the real provider.
    shell_root = _prepare_remote_root(
        tmp_path, label="shell", staging=tmp_path / "input-shell", payload=shell_payload
    )
    plain_root = _prepare_remote_root(
        tmp_path, label="plain", staging=tmp_path / "input-plain", payload=plain_payload
    )
    assert (shell_root / "bin" / "sh").is_symlink()
    assert (shell_root / "bin" / "sh").readlink().as_posix() == "/toolchain/bin/sh"
    assert not (plain_root / "bin" / "sh").is_symlink()


def test_read_only_cache_input_is_ro_bound_locally_and_readonly_remotely(tmp_path: Path) -> None:
    repository = tmp_path / "repo-readonly-cache"
    seed_cicd_repository(repository)
    step = ResolvedCommandStep(timeout_seconds=3600, step_id="check-cache", argv=("true",))
    bubblewrap = BubblewrapProcessIsolation(executable="/usr/bin/true")
    readonly_cache = tmp_path / "sealed-cache"
    readonly_cache.mkdir()
    (readonly_cache / "payload").write_text("sealed", encoding="utf-8")
    identity = snapshot_identity(readonly_cache)

    with prepare_physical_test_run(
        RepositoryIdentity(path=repository),
        strict_test_plan(step),
        isolation_backend=TEST_ISOLATION_BACKEND,
        path_authority=TEST_PATH_AUTHORITY,
    ) as run:
        original = run.create_attempt(step, 1)
        binding = SnapshotBinding(
            declared_path=readonly_cache,
            snapshot_path=readonly_cache,
            identity=identity,
            sandbox_path=PurePosixPath("/cache"),
        )
        attempt = replace(original, sandbox_bindings=original.sandbox_bindings + (binding,))
        request = IsolatedProcessRequest(
            attempt=attempt, argv=step.argv, cwd=attempt.paths.workspace, env={}
        )
        launch = sandbox_process_launch(request)
        with bind_atomic_runtime_authority(request, launch) as atomic:
            bwrap_argv, _ = bubblewrap._command(
                request, launch=launch, atomic_authority=atomic, network_prefix=()
            )
        payload = _request_payload(request, timeout=12.0, staging_root=tmp_path / "input-cache")

    pairs = list(zip(bwrap_argv, bwrap_argv[1:], strict=False))
    assert (str(attempt.paths.cache), "/cache") not in pairs
    assert (str(readonly_cache), "/cache") in pairs
    ro_index = bwrap_argv.index(str(readonly_cache)) - 1
    assert bwrap_argv[ro_index] == "--ro-bind"
    assert payload["cache_read_only"] is True
    remote_fleet = tmp_path / "remote-cache-fleet"
    (remote_fleet / "input").mkdir(parents=True)
    for source in (tmp_path / "input-cache").rglob("*"):
        if source.is_file():
            destination = remote_fleet / "input" / source.relative_to(tmp_path / "input-cache")
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(source.read_bytes())
    remote_root = remote_fleet / "rootfs"
    _prepare_root(payload, remote_root)
    assert (remote_root / "cache" / "payload").read_text(encoding="utf-8") == "sealed"
    assert (remote_root / "cache").stat().st_mode & 0o222 == 0
    assert (remote_root / "cache" / "payload").stat().st_mode & 0o222 == 0


def test_remote_runner_observes_same_runtime_authority_as_local_backend(tmp_path: Path) -> None:
    repository = tmp_path / "repo-runtime-observation"
    seed_cicd_repository(repository)
    step = ResolvedCommandStep(timeout_seconds=3600, step_id="check-runtime", argv=("true",))

    with prepare_physical_test_run(
        RepositoryIdentity(path=repository),
        strict_test_plan(step),
        isolation_backend=TEST_ISOLATION_BACKEND,
        path_authority=TEST_PATH_AUTHORITY,
    ) as run:
        attempt = run.create_attempt(step, 1)
        request = IsolatedProcessRequest(
            attempt=attempt, argv=step.argv, cwd=attempt.paths.workspace, env={}
        )
        payload = _request_payload(
            request, timeout=12.0, staging_root=tmp_path / "remote-observation-fleet" / "input"
        )
        expected = observed_runtime_authority_payload(reconcile_runtime_authority(request))

    remote_root = tmp_path / "remote-observation-fleet" / "rootfs"
    _prepare_root(payload, remote_root)
    binding = remote_runner._bind_runtime_execution(payload, remote_root)
    try:
        observed = dict(binding["authority"])
    finally:
        remote_runner._close_runtime_binding(binding)

    assert observed == expected


def test_remote_runner_preserves_runtime_observation_on_workload_timeout(
    tmp_path: Path, monkeypatch
) -> None:
    repository = tmp_path / "repo-runtime-timeout-observation"
    seed_cicd_repository(repository)
    step = ResolvedCommandStep(timeout_seconds=3600, step_id="check-runtime", argv=("true",))

    with prepare_physical_test_run(
        RepositoryIdentity(path=repository),
        strict_test_plan(step),
        isolation_backend=TEST_ISOLATION_BACKEND,
        path_authority=TEST_PATH_AUTHORITY,
    ) as run:
        attempt = run.create_attempt(step, 1)
        request = IsolatedProcessRequest(
            attempt=attempt, argv=step.argv, cwd=attempt.paths.workspace, env={}
        )
        payload = _request_payload(
            request, timeout=12.0, staging_root=tmp_path / "remote-timeout-fleet" / "input"
        )
        expected = observed_runtime_authority_payload(reconcile_runtime_authority(request))

    def timeout(*args, **kwargs):
        del args, kwargs
        raise subprocess.TimeoutExpired(("true",), 12.0, output="partial")

    monkeypatch.setattr(remote_runner, "_run", timeout)
    result = remote_runner._execute_attempt(payload, tmp_path / "remote-timeout-fleet" / "rootfs")

    assert result["returncode"] == 124
    assert result["runtime_authority"] == expected


@pytest.mark.certification
def test_remote_runner_executes_opened_executable_after_path_replacement(
    tmp_path: Path, monkeypatch
) -> None:
    if os.geteuid() != 0:
        pytest.skip("chroot requires root privileges")
    repository = tmp_path / "repo-atomic-remote"
    seed_cicd_repository(repository)
    step = ResolvedCommandStep(timeout_seconds=3600, step_id="atomic-runtime", argv=("true",))

    with prepare_physical_test_run(
        RepositoryIdentity(path=repository),
        strict_test_plan(step),
        isolation_backend=TEST_ISOLATION_BACKEND,
        path_authority=TEST_PATH_AUTHORITY,
    ) as run:
        attempt = run.create_attempt(step, 1)
        request = IsolatedProcessRequest(
            attempt=attempt, argv=step.argv, cwd=attempt.paths.workspace, env={}
        )
        payload = _request_payload(
            request, timeout=12.0, staging_root=tmp_path / "remote-atomic-fleet" / "input"
        )
        expected = observed_runtime_authority_payload(reconcile_runtime_authority(request))

    original_run = remote_runner._run

    def replace_path(payload, root, binding):
        executable = root / str(binding["executable_path"]).lstrip("/")
        executable.write_bytes(b"not-the-opened-executable")
        executable.chmod(0o755)
        return original_run(payload, root, binding)

    monkeypatch.setattr(remote_runner, "_run", replace_path)
    result = remote_runner._execute_attempt(payload, tmp_path / "remote-atomic-fleet" / "rootfs")

    assert result["returncode"] == 0, result
    assert result["runtime_authority"] == expected


@pytest.mark.certification
def test_remote_runner_restores_bound_interpreter_after_path_mutation(
    tmp_path: Path, monkeypatch
) -> None:
    if os.geteuid() != 0:
        pytest.skip("chroot requires root privileges")
    repository = tmp_path / "repo-atomic-interpreter"
    seed_cicd_repository(repository)
    step = ResolvedCommandStep(
        timeout_seconds=3600,
        step_id="atomic-interpreter",
        argv=("npm", "--version"),
        capabilities=PlanStepCapabilities(tools=("node",)),
    )

    with prepare_physical_test_run(
        RepositoryIdentity(path=repository),
        strict_test_plan(step),
        isolation_backend=TEST_ISOLATION_BACKEND,
    ) as run:
        attempt = run.create_attempt(step, 1)
        request = IsolatedProcessRequest(
            attempt=attempt, argv=step.argv, cwd=attempt.paths.workspace, env={}
        )
        payload = _request_payload(
            request,
            timeout=12.0,
            staging_root=tmp_path / "remote-atomic-interpreter-fleet" / "input",
        )
        expected = observed_runtime_authority_payload(reconcile_runtime_authority(request))

    original_run = remote_runner._run

    def replace_interpreter(payload, root, binding):
        interpreter = binding.get("interpreter_resolved_path")
        assert isinstance(interpreter, str)
        path = root / interpreter.lstrip("/")
        path.write_bytes(b"mutated-interpreter")
        path.chmod(0o755)
        return original_run(payload, root, binding)

    monkeypatch.setattr(remote_runner, "_run", replace_interpreter)
    result = remote_runner._execute_attempt(
        payload, tmp_path / "remote-atomic-interpreter-fleet" / "rootfs"
    )

    assert result["returncode"] != 125, result
    assert result["runtime_authority"] == expected
    restored = (
        tmp_path / "remote-atomic-interpreter-fleet" / "rootfs" / "toolchain" / "bin" / "node"
    )
    assert (
        "sha256:" + hashlib.sha256(restored.read_bytes()).hexdigest()
        == expected["interpreter_identity"]
    )
