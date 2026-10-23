"""Ordinary native namespace restrictions reuse the Bubblewrap process owner."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import socket
import tempfile
import time
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path, PurePosixPath
from threading import Thread
from typing import Any

import pytest

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.models.fleet_plan_isolation import CicdResourcePolicy
from oh_goon.engine.command import (
    CommandCancelledError,
    CommandOutputLimitExceeded,
    CommandProcessLifecycleEvent,
    CommandResult,
    CommandSupervisorReceipt,
    CommandTerminal,
    CommandTimeoutExpired,
)
from oh_goon.engine.errors import OperationCancelledError
from oh_goon.integrations import fleet_plan_bubblewrap_isolation as module
from oh_goon.integrations.fleet_plan_bubblewrap_isolation import BubblewrapProcessIsolation
from oh_goon.integrations.fleet_plan_process_isolation import (
    StandardIsolatedProcessRequest,
    StandardReadOnlyMount,
)
from oh_goon.runtime.workspaces import RepositoryWorkspaceAuthority


def _backend(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> BubblewrapProcessIsolation:
    """Give launcher fakes a physical test runtime; native tests retain host roots."""
    executable = tmp_path / "bwrap"
    executable.write_bytes(b"test selected executable")
    runtime = tmp_path / "system-runtime"
    runtime.mkdir()
    (runtime / "public-runtime").write_bytes(b"test runtime bytes")
    monkeypatch.setattr(module, "_STANDARD_RUNTIME_DIRECTORIES", (str(runtime),))
    return BubblewrapProcessIsolation(str(executable))


def _request(
    backend: BubblewrapProcessIsolation, workspace: Path, **changes: Any
) -> StandardIsolatedProcessRequest:
    return StandardIsolatedProcessRequest(
        **{
            "workspace": workspace,
            "cwd": workspace,
            "argv": ("/usr/bin/true",),
            "env": {"PATH": os.defpath},
            "backend_configuration_id": backend.configuration_identity(),
            "require_workspace_current": lambda: None,
            **changes,
        }
    )


def _native_exec_ack(argv: tuple[str, ...], returncode: int = 0) -> None:
    if "--json-status-fd" in argv:
        fd = int(argv[argv.index("--json-status-fd") + 1])
        os.write(
            fd, b'{"child-pid":123}\n' + json.dumps({"exit-code": returncode}).encode() + b"\n"
        )


@pytest.mark.parametrize("surface", ["workspace", "readonly-workspace", "runtime", "config"])
def test_private_application_state_is_refused_before_any_native_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, surface: str
) -> None:
    backend = _backend(tmp_path, monkeypatch)
    workspace = tmp_path / "project"
    workspace.mkdir()
    private = workspace / "state" if "workspace" in surface else tmp_path / "private-state"
    private.mkdir()
    journal = private / "custom.sqlite"
    journal.write_text("private other Run")
    backend.bind_application_state(private_paths=(journal,), read_only_paths=())
    changes: dict[str, Any] = {"read_only_workspace": surface == "readonly-workspace"}
    if surface == "runtime":
        changes["read_only_mounts"] = (
            StandardReadOnlyMount(private, PurePosixPath("/opt/native-input")),
        )
    elif surface == "config":
        changes["private_configuration_directory"] = private
    monkeypatch.setattr(
        module, "run_command", lambda *a, **kw: pytest.fail("private state reached process launch")
    )
    with pytest.raises(FleetPlanInvalid, match="private application state"):
        backend.execute(_request(backend, workspace, **changes), timeout=1, cancel_requested=None)
    assert journal.read_text() == "private other Run"


@pytest.mark.parametrize("hardlink_alias", [False, True])
def test_late_sqlite_sidecar_mount_cannot_bypass_resolved_database_privacy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, hardlink_alias: bool
) -> None:
    from oh_goon.runtime.database import LocalDatabase

    database = LocalDatabase(tmp_path / "custom.sqlite", journal_mode="DELETE")
    backend = _backend(tmp_path, monkeypatch)
    backend.bind_application_state(private_paths=database.private_paths, read_only_paths=())
    sidecar = database.path.with_name(database.path.name + "-wal")
    sidecar.write_text("private journal pages")
    workspace = tmp_path / "project"
    workspace.mkdir()
    selected = sidecar
    if hardlink_alias:
        selected = tmp_path / "public-looking-runtime"
        os.link(sidecar, selected)
    monkeypatch.setattr(module, "run_command", lambda *a, **kw: pytest.fail("sidecar was exposed"))
    with pytest.raises(FleetPlanInvalid, match="private application (state|file alias)"):
        backend.execute(
            _request(
                backend,
                workspace,
                read_only_mounts=(StandardReadOnlyMount(selected, PurePosixPath("/opt/state")),),
            ),
            timeout=1,
            cancel_requested=None,
        )


@pytest.mark.parametrize(
    "surface", ["workspace", "readonly-workspace", "runtime", "runtime-file", "config"]
)
def test_active_database_hardlink_alias_is_refused_before_native_process_birth(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, surface: str
) -> None:
    from oh_goon.runtime.database import LocalDatabase

    database = LocalDatabase(tmp_path / "private-state" / "active.sqlite", journal_mode="DELETE")
    backend = _backend(tmp_path, monkeypatch)
    backend.bind_application_state(private_paths=database.private_paths, read_only_paths=())
    workspace = tmp_path / "project"
    workspace.mkdir()
    exposed = workspace if "workspace" in surface else tmp_path / "public-looking-input"
    exposed.mkdir(exist_ok=True)
    alias = exposed / "ordinary-file.sqlite"
    os.link(database.path, alias)
    assert os.path.samefile(database.path, alias)
    original = database.path.read_bytes()
    assert alias.read_bytes() == original
    changes: dict[str, Any] = {"read_only_workspace": surface == "readonly-workspace"}
    if surface in {"runtime", "runtime-file"}:
        changes["read_only_mounts"] = (
            StandardReadOnlyMount(
                alias if surface == "runtime-file" else exposed, PurePosixPath("/opt/native-input")
            ),
        )
    elif surface == "config":
        changes["private_configuration_directory"] = exposed
    monkeypatch.setattr(
        module,
        "run_command",
        lambda *a, **kw: pytest.fail("private hardlink reached process launch"),
    )
    with pytest.raises(FleetPlanInvalid, match="private application file alias"):
        backend.execute(_request(backend, workspace, **changes), timeout=1, cancel_requested=None)
    assert database.path.read_bytes() == original


@pytest.mark.process
def test_native_public_hardlinks_remain_usable_in_all_approved_input_surfaces(
    tmp_path: Path,
) -> None:
    executable = shutil.which("bwrap")
    if executable is None:
        pytest.skip("native Bubblewrap not installed")
    backend = BubblewrapProcessIsolation(executable)
    workspace = tmp_path / "project"
    workspace.mkdir()
    runtime = tmp_path / "public-runtime"
    runtime.mkdir()
    configuration = tmp_path / "public-configuration"
    configuration.mkdir()
    private = tmp_path / "active.sqlite"
    private.write_text("private journal")
    public = tmp_path / "public.txt"
    public.write_text("public hardlink\n")
    for directory in (workspace, runtime, configuration):
        os.link(public, directory / "public.txt")
    backend.bind_application_state(private_paths=(private,), read_only_paths=())
    with RepositoryWorkspaceAuthority.pin_repository_subject(workspace) as require_current:
        request = _request(
            backend,
            workspace,
            argv=(
                "/bin/cat",
                "public.txt",
                "/opt/public-runtime/public.txt",
                "/config/native/public.txt",
            ),
            read_only_mounts=(
                StandardReadOnlyMount(runtime, PurePosixPath("/opt/public-runtime")),
            ),
            private_configuration_directory=configuration,
            read_only_workspace=True,
            require_workspace_current=require_current,
        )
        _require_native(backend, request)
        result = backend.execute(request, timeout=5, cancel_requested=None)
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == ["public hardlink"] * 3
    assert result.standard_isolation is not None
    assert result.standard_isolation.workspace_read_only
    assert public.read_text() == "public hardlink\n"


@pytest.mark.parametrize("read_only_tool_workspace", [False, True])
def test_sibling_database_and_private_state_do_not_exclude_approved_repository(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, read_only_tool_workspace: bool
) -> None:
    backend = _backend(tmp_path, monkeypatch)
    workspace = (
        tmp_path / "extensions" / "project" if read_only_tool_workspace else tmp_path / "project"
    )
    workspace.mkdir(parents=True)
    journal = tmp_path / "custom.sqlite"
    journal.write_text("private other Run")
    runtime = tmp_path / "selected-native-runtime"
    runtime.mkdir()
    (runtime / "tool").write_text("public runtime")
    backend.bind_application_state(
        private_paths=(journal, tmp_path / "executor-control"),
        read_only_paths=(tmp_path / "extensions",),
    )
    calls: list[tuple[str, ...]] = []

    def command(*argv: str, **kwargs: Any) -> CommandResult:
        calls.append(argv)
        _native_exec_ack(argv)
        return CommandResult(0, "approved", "")

    monkeypatch.setattr(module, "run_command", command)
    result = backend.execute(
        _request(
            backend,
            workspace,
            read_only_mounts=(StandardReadOnlyMount(runtime, PurePosixPath(runtime)),),
            read_only_workspace=read_only_tool_workspace,
        ),
        timeout=1,
        cancel_requested=None,
    )
    assert result.stdout == "approved" and result.standard_isolation is not None
    assert len(calls) == 2
    assert str(journal) not in calls[-1]


def test_application_state_binding_cannot_be_replaced_by_another_runtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    backend = _backend(tmp_path, monkeypatch)
    backend.bind_application_state(private_paths=(tmp_path / "active.sqlite",), read_only_paths=())
    backend.bind_application_state(private_paths=(tmp_path / "active.sqlite",), read_only_paths=())
    with pytest.raises(FleetPlanInvalid, match="different application state"):
        backend.bind_application_state(
            private_paths=(tmp_path / "other.sqlite",), read_only_paths=()
        )


def test_standard_launch_retains_namespace_cancellation_and_private_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    backend = _backend(tmp_path, monkeypatch)
    workspace = tmp_path / "repo"
    workspace.mkdir()
    selected_config = tmp_path / "native.json"
    selected_config.write_text("{}")
    calls: list[tuple[tuple[str, ...], dict[str, Any]]] = []

    def command(*argv: str, **kwargs: Any) -> CommandResult:
        calls.append((argv, kwargs))
        _native_exec_ack(argv)
        return CommandResult(returncode=0, stdout="done", stderr="")

    monkeypatch.setattr(module, "run_command", command)

    def cancellation() -> bool:
        return False

    request = _request(
        backend,
        workspace,
        argv=("/usr/bin/python3", str(workspace / "main.py")),
        env={"PATH": os.defpath, "HOME": "/host/home", "SSH_AUTH_SOCK": "/host/socket"},
        input_data=b"input",
        read_only_mounts=(
            StandardReadOnlyMount(selected_config, PurePosixPath("/config/native/config.json")),
        ),
    )
    result = backend.execute(request, timeout=4, cancel_requested=cancellation)
    assert result.stdout == "done"
    assert len(calls) == 2  # Actual namespace preflight, then this workload.
    argv, options = calls[-1]
    assert "--unshare-user" in argv and "--unshare-net" in argv and "--unshare-pid" in argv
    assert "--die-with-parent" in argv
    assert argv[-2:] == ("/usr/bin/python3", "/workspace/main.py")
    assert options["cancel_requested"] is cancellation
    assert options["input_data"] == b"input"
    assert options["env"]["HOME"] == "/home/fleet"
    assert "SSH_AUTH_SOCK" not in options["env"]
    assert ("--ro-bind", str(selected_config), "/config/native/config.json") in tuple(
        argv[index : index + 3] for index in range(len(argv) - 2)
    )
    assert not any(str(Path.home()) == item for item in argv)
    private_home_index = argv.index("/home/fleet")
    assert not Path(argv[private_home_index - 1]).exists()


def test_explicit_native_runtime_and_prompt_paths_map_at_the_process_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    backend = _backend(tmp_path, monkeypatch)
    workspace = tmp_path / "repo"
    workspace.mkdir()
    runtime = workspace / "selected-runtime"
    runtime.mkdir()
    (runtime / "agent").write_text("native agent")
    prompt = tmp_path / "prompt.md"
    prompt.write_text("caller task")
    calls: list[tuple[tuple[str, ...], dict[str, Any]]] = []

    def command(*argv: str, **kwargs: Any) -> CommandResult:
        calls.append((argv, kwargs))
        _native_exec_ack(argv)
        return CommandResult(0, "", "")

    monkeypatch.setattr(module, "run_command", command)
    request = _request(
        backend,
        workspace,
        argv=(str(runtime / "agent"), str(prompt)),
        env={"PATH": f"/usr/bin:{runtime}/bin", "PROMPT_FILE": str(prompt)},
        read_only_mounts=(
            StandardReadOnlyMount(runtime, PurePosixPath("/opt/oh-goon-native/runtime0")),
            StandardReadOnlyMount(prompt, PurePosixPath("/config/oh-goon/context.md")),
        ),
    )
    backend.execute(request, timeout=4, cancel_requested=None)
    argv, options = calls[-1]
    assert argv[-2:] == ("/opt/oh-goon-native/runtime0/agent", "/config/oh-goon/context.md")
    assert options["env"]["PATH"] == "/usr/bin:/opt/oh-goon-native/runtime0/bin"
    assert options["env"]["PROMPT_FILE"] == "/config/oh-goon/context.md"
    assert prompt.read_text() == "caller task"


def test_terminal_transport_does_not_start_another_terminal_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    backend = _backend(tmp_path, monkeypatch)
    workspace = tmp_path / "repo"
    workspace.mkdir()
    calls: list[tuple[tuple[str, ...], dict[str, Any]]] = []

    def command(*argv: str, **kwargs: Any) -> CommandResult:
        calls.append((argv, kwargs))
        _native_exec_ack(argv)
        return CommandResult(0, "", "")

    monkeypatch.setattr(module, "run_command", command)
    terminal = CommandTerminal(read_input=lambda size: None, write_output=lambda data: None)
    backend.execute(
        _request(backend, workspace, terminal=terminal), timeout=4, cancel_requested=None
    )
    assert "--new-session" not in calls[-1][0]
    assert "--unshare-pid" in calls[-1][0]
    assert calls[-1][1]["terminal"] is terminal


@pytest.mark.parametrize("attack", ["configuration", "workspace", "resources", "strict", "cancel"])
def test_invalid_or_cancelled_standard_request_never_starts_a_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, attack: str
) -> None:
    backend = _backend(tmp_path, monkeypatch)
    workspace = tmp_path / "repo"
    workspace.mkdir()
    request = _request(backend, workspace)
    if attack == "configuration":
        request = replace(request, backend_configuration_id="old-selected-config")
    elif attack == "workspace":

        def lost_workspace() -> None:
            raise FleetPlanInvalid("workspace authority changed")

        request = replace(request, require_workspace_current=lost_workspace)
    elif attack == "resources":
        request = replace(request, resources=CicdResourcePolicy(memory_mib=64))
    elif attack == "strict":
        backend.require_local_resource_intent = True
        request = replace(request, backend_configuration_id=backend.configuration_identity())
    monkeypatch.setattr(
        module, "run_command", lambda *a, **kw: pytest.fail("must reject before process")
    )
    with pytest.raises((FleetPlanInvalid, OperationCancelledError)):
        backend.execute(request, timeout=4, cancel_requested=lambda: attack == "cancel")


def test_native_namespace_failure_is_not_a_host_network_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    backend = _backend(tmp_path, monkeypatch)
    calls: list[tuple[str, ...]] = []

    def unavailable(*argv: str, **kwargs: Any) -> CommandResult:
        calls.append(argv)
        return CommandResult(1, "", "namespace permission denied")

    monkeypatch.setattr(module, "run_command", unavailable)
    with pytest.raises(FleetPlanInvalid, match="namespace permission denied"):
        backend.execute(_request(backend, tmp_path), timeout=4, cancel_requested=None)
    assert len(calls) == 1


@pytest.mark.parametrize("name", ["LD_PRELOAD", "LD_AUDIT", "PYTHONPATH", "BASH_ENV"])
def test_workload_environment_cannot_inject_code_before_namespace_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    backend = _backend(tmp_path, monkeypatch)
    request = _request(backend, tmp_path, env={"PATH": os.defpath, name: "/host/injection"})
    monkeypatch.setattr(
        module, "run_command", lambda *a, **kw: pytest.fail("injection reached launcher")
    )
    with pytest.raises(FleetPlanInvalid, match="loader/interpreter injection"):
        backend.require_standard_available(request)


@pytest.mark.process
def test_system_runtime_links_cannot_reach_unmounted_host_ipc(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executable = shutil.which("bwrap")
    if executable is None:
        pytest.skip("native bwrap is not installed")
    workspace = tmp_path / "repo"
    workspace.mkdir()
    available = BubblewrapProcessIsolation(executable)
    _require_native(available, _request(available, workspace))
    alternatives = tmp_path / "alternatives"
    alternatives.mkdir()
    socket_path = tmp_path / "host-service.sock"
    fifo_path = tmp_path / "host-service.fifo"
    os.mkfifo(fifo_path)
    (alternatives / "service.sock").symlink_to(socket_path)
    (alternatives / "service.fifo").symlink_to(fifo_path)
    (alternatives / "null").symlink_to("/dev/null")
    original_binds = module._standard_system_binds()
    monkeypatch.setattr(
        module,
        "_STANDARD_RUNTIME_DIRECTORIES",
        (*module._STANDARD_RUNTIME_DIRECTORIES, str(alternatives)),
    )
    monkeypatch.setattr(
        module,
        "_standard_system_binds",
        lambda: (*original_binds, "--ro-bind", str(alternatives), "/etc/alternatives"),
    )
    script = """
import errno, json, pathlib, socket
root = pathlib.Path('/etc/alternatives')
assert (root / 'service.sock').is_symlink()
assert (root / 'service.fifo').is_symlink()
assert not (root / 'service.sock').exists()
assert not (root / 'service.fifo').exists()
with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
    try:
        connection.connect(str(root / 'service.sock'))
    except OSError as error:
        assert error.errno == errno.ENOENT
    else:
        raise AssertionError('host service became reachable')
with (root / 'null').open('wb') as null:
    assert null.write(b'sandbox-owned device') == 20
print(json.dumps({'host_ipc': 'absent', 'synthetic_device': 'usable'}))
"""
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
        server.bind(str(socket_path))
        server.listen()
        server.setblocking(False)
        backend = BubblewrapProcessIsolation(executable)
        with RepositoryWorkspaceAuthority.pin_repository_subject(workspace) as require_current:
            result = backend.execute(
                _request(
                    backend,
                    workspace,
                    require_workspace_current=require_current,
                    argv=("/usr/bin/python3", "-c", script),
                ),
                timeout=5,
                cancel_requested=None,
            )
        try:
            connection, _ = server.accept()
        except BlockingIOError:
            pass
        else:
            with connection:
                pytest.fail("host service accepted a sandbox connection")
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "host_ipc": "absent",
        "synthetic_device": "usable",
    }


@pytest.mark.process
@pytest.mark.parametrize("kind", ["socket", "fifo", "socket-link", "fifo-link"])
def test_system_runtime_rejects_host_ipc_visible_in_any_bound_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    alternatives = tmp_path / "alternatives"
    alternatives.mkdir()
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    # Inspect the alternatives first, so cross-root links require the full mount set.
    backend = _backend(tmp_path, monkeypatch)
    monkeypatch.setattr(module, "_STANDARD_RUNTIME_DIRECTORIES", (str(alternatives), str(runtime)))
    monkeypatch.setattr(
        module, "run_command", lambda *a, **kw: pytest.fail("host IPC reached native launch")
    )
    endpoint = runtime / "endpoint" if kind.endswith("-link") else alternatives / "endpoint"
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
        if kind.startswith("fifo"):
            os.mkfifo(endpoint)
        else:
            server.bind(str(endpoint))
        if kind.endswith("-link"):
            (alternatives / "service").symlink_to(endpoint)
        with pytest.raises(FleetPlanInvalid, match="IPC/special"):
            backend.require_standard_available(_request(backend, workspace))


def test_system_runtime_external_link_still_rejects_private_file_alias(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    private = tmp_path / "private.sqlite"
    private.write_bytes(b"private application state")
    (runtime / "ordinary-looking-input").symlink_to(private)
    backend = _backend(tmp_path, monkeypatch)
    backend.bind_application_state(private_paths=(private,), read_only_paths=())
    monkeypatch.setattr(module, "_STANDARD_RUNTIME_DIRECTORIES", (str(runtime),))
    monkeypatch.setattr(
        module,
        "run_command",
        lambda *a, **kw: pytest.fail("private file alias reached native launch"),
    )
    with pytest.raises(FleetPlanInvalid, match="private application file alias"):
        backend.require_standard_available(_request(backend, workspace))


@pytest.mark.process
@pytest.mark.parametrize("location", ["workspace", "input", "input-alias", "private-config"])
@pytest.mark.parametrize("kind", ["socket", "fifo", "socket-symlink"])
def test_ordinary_binds_reject_existing_host_ipc_before_process_creation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, location: str, kind: str
) -> None:
    backend = _backend(tmp_path, monkeypatch)
    workspace = tmp_path / "repo"
    workspace.mkdir()
    selected_input = tmp_path / "selected"
    selected_input.mkdir()
    exposed = workspace if location == "workspace" else selected_input
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
        if kind == "fifo":
            os.mkfifo(exposed / "endpoint")
        elif kind == "socket":
            server.bind(str(exposed / "endpoint"))
        else:
            server.bind(str(tmp_path / "socket"))
            (exposed / "endpoint").symlink_to(tmp_path / "socket")
        request = _request(
            backend,
            workspace,
            private_configuration_directory=selected_input
            if location == "private-config"
            else None,
            read_only_mounts=()
            if location == "private-config"
            else (
                StandardReadOnlyMount(
                    selected_input,
                    PurePosixPath(str(selected_input))
                    if location == "input-alias"
                    else PurePosixPath("/config/native"),
                ),
            ),
        )
        monkeypatch.setattr(
            module, "run_command", lambda *a, **kw: pytest.fail("host IPC reached launch")
        )
        with pytest.raises(FleetPlanInvalid, match="IPC/special"):
            backend.require_standard_available(request)


def test_requested_caps_use_native_whole_workload_scope_for_preflight_and_launch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    resource_executable = tmp_path / "systemd-run"
    resource_executable.write_text("native-resource-owner")
    resource_executable.chmod(0o755)
    backend = _backend(tmp_path, monkeypatch)
    backend.standard_resource_executable = str(resource_executable)
    calls: list[tuple[str, ...]] = []

    def command(*argv: str, **kwargs: Any) -> CommandResult:
        calls.append(argv)
        _native_exec_ack(argv)
        return CommandResult(0, "", "")

    monkeypatch.setattr(module, "run_command", command)
    request = _request(
        backend,
        tmp_path,
        resources=CicdResourcePolicy(cpu_millis=1250, memory_mib=64, max_processes=32),
    )
    backend.execute(request, timeout=4, cancel_requested=None)
    assert len(calls) == 2
    for argv in calls:
        assert argv[0] == str(resource_executable)
        assert "--scope" in argv and "--collect" in argv
        assert "--property=CPUQuota=125%" in argv
        assert "--property=MemoryMax=67108864" in argv
        assert "--property=MemorySwapMax=0" in argv
        assert "--property=TasksMax=32" in argv


@pytest.mark.process
def test_real_native_scope_applies_limits_to_the_bubblewrap_workload(tmp_path: Path) -> None:
    executable, resources = shutil.which("bwrap"), shutil.which("systemd-run")
    if executable is None or resources is None:
        pytest.skip("native bwrap and systemd-run are not installed")
    backend = BubblewrapProcessIsolation(executable, standard_resource_executable=resources)
    workspace = tmp_path / "repo"
    workspace.mkdir()
    observed: dict[str, str] = {}
    pids: list[int] = []

    def started(event: CommandProcessLifecycleEvent) -> None:
        if event.kind == "PROCESS_STARTED" and event.pid is not None:
            pids.append(event.pid)

    def cancel_after_kernel_readback() -> bool:
        if not (workspace / "started").is_file() or not pids:
            return False
        relative = (Path("/proc") / str(pids[0]) / "cgroup").read_text().strip().split("::", 1)[1]
        group = Path("/sys/fs/cgroup") / relative.lstrip("/")
        for name in ("memory.max", "memory.swap.max", "pids.max", "cpu.max"):
            observed[name] = (group / name).read_text().strip()
        return True

    with RepositoryWorkspaceAuthority.pin_repository_subject(workspace) as require_current:
        request = _request(
            backend,
            workspace,
            require_workspace_current=require_current,
            resources=CicdResourcePolicy(cpu_millis=1000, memory_mib=256, max_processes=64),
            argv=(
                "/usr/bin/python3",
                "-c",
                "import pathlib,time; pathlib.Path('started').write_text('scoped'); time.sleep(10)",
            ),
        )
        _require_native(backend, request)
        with pytest.raises(CommandCancelledError):
            backend.execute(
                request,
                timeout=5,
                cancel_requested=cancel_after_kernel_readback,
                process_observer=started,
            )
    assert observed == {
        "memory.max": "268435456",
        "memory.swap.max": "0",
        "pids.max": "64",
        "cpu.max": "100000 100000",
    }


@pytest.mark.parametrize(
    "target", ["/workspace/config", "/proc/escape", "/home/fleet", "/config", "/etc", "/tmp"]
)
def test_explicit_mount_cannot_replace_workspace_or_private_namespace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, target: str
) -> None:
    backend = _backend(tmp_path, monkeypatch)
    config = tmp_path / "selected.json"
    config.write_text("{}")
    request = _request(
        backend, tmp_path, read_only_mounts=(StandardReadOnlyMount(config, PurePosixPath(target)),)
    )
    monkeypatch.setattr(
        module, "run_command", lambda *a, **kw: pytest.fail("invalid mount launched")
    )
    with pytest.raises(FleetPlanInvalid):
        backend.require_standard_available(request)


@pytest.mark.parametrize("target_scope", ["exact", "parent", "child"])
def test_explicit_mount_cannot_shadow_original_workspace_alias(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, target_scope: str
) -> None:
    backend = _backend(tmp_path, monkeypatch)
    workspace = tmp_path / "repository"
    workspace.mkdir()
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    target = {
        "exact": workspace,
        "parent": workspace.parent,
        "child": workspace / "native-input",
    }[target_scope]
    request = _request(
        backend,
        workspace,
        read_only_mounts=(StandardReadOnlyMount(runtime, PurePosixPath(target)),),
    )
    monkeypatch.setattr(
        module, "run_command", lambda *a, **kw: pytest.fail("colliding mount reached native work")
    )
    with pytest.raises(FleetPlanInvalid, match="explicit scope"):
        backend.require_standard_available(request)


@pytest.mark.process
def test_native_configuration_writes_use_private_overlay_without_changing_host_inputs(
    tmp_path: Path,
) -> None:
    executable = shutil.which("bwrap")
    if executable is None:
        if os.environ.get("OH_GOON_REQUIRE_NATIVE_ISOLATION") == "1":
            pytest.fail("required native bwrap not installed")
        pytest.skip("native bwrap not installed")
    backend = BubblewrapProcessIsolation(executable)
    workspace = tmp_path / "repo"
    workspace.mkdir()
    configuration = tmp_path / "public-config"
    configuration.mkdir()
    (configuration / "settings.json").write_text('{"theme":"selected"}')
    with RepositoryWorkspaceAuthority.pin_repository_subject(workspace) as require_current:
        request = _request(
            backend,
            workspace,
            require_workspace_current=require_current,
            private_configuration_directory=configuration,
            argv=(
                "/usr/bin/python3",
                "-c",
                "from pathlib import Path; root=Path('/config/native'); assert 'selected' in (root/'settings.json').read_text(); (root/'trust.json.lock').mkdir(); (root/'auth.json').write_text('private native state'); (root/'settings.json').write_text('private changed setting'); print('private-config-ready')",
            ),
        )
        _require_native(backend, request)
        result = backend.execute(request, timeout=10, cancel_requested=None)
        clean = backend.execute(
            replace(
                request,
                argv=(
                    "/usr/bin/python3",
                    "-c",
                    "from pathlib import Path; root=Path('/config/native'); assert not (root/'auth.json').exists(); assert not (root/'trust.json.lock').exists(); assert 'selected' in (root/'settings.json').read_text(); print('new-private-overlay')",
                ),
            ),
            timeout=10,
            cancel_requested=None,
        )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "private-config-ready"
    assert clean.returncode == 0 and clean.stdout.strip() == "new-private-overlay"
    assert (configuration / "settings.json").read_text() == '{"theme":"selected"}'
    assert sorted(path.name for path in configuration.iterdir()) == ["settings.json"]


def _require_native(
    backend: BubblewrapProcessIsolation, request: StandardIsolatedProcessRequest
) -> None:
    try:
        backend.require_standard_available(request)
    except FleetPlanInvalid as exc:
        if os.environ.get("OH_GOON_REQUIRE_NATIVE_ISOLATION") == "1":
            pytest.fail(str(exc))
        pytest.skip(f"native namespace proof unavailable: {exc}")


def test_unavailable_native_overlay_rejects_before_workload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    backend = _backend(tmp_path, monkeypatch)
    workspace = tmp_path / "repo"
    workspace.mkdir()
    configuration = tmp_path / "config"
    configuration.mkdir()
    calls: list[tuple[str, ...]] = []

    def unavailable(*argv: str, **kwargs: Any) -> CommandResult:
        calls.append(argv)
        assert "--tmp-overlay" in argv
        return CommandResult(1, "", "native overlay unavailable")

    monkeypatch.setattr(module, "run_command", unavailable)
    request = _request(
        backend, workspace, private_configuration_directory=configuration, argv=("/must-not-run",)
    )
    with pytest.raises(FleetPlanInvalid, match="native overlay unavailable"):
        backend.execute(request, timeout=5, cancel_requested=None)
    assert len(calls) == 1 and calls[0][-1] == "/bin/true"


@pytest.mark.parametrize("cause", ["timeout", "cancel", "output_limit"])
@pytest.mark.parametrize("acknowledged", [False, True])
def test_aborted_native_workload_retains_only_acknowledged_isolation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cause: str, acknowledged: bool
) -> None:
    backend = _backend(tmp_path, monkeypatch)
    request = _request(backend, tmp_path)
    error = (
        CommandTimeoutExpired(request.argv, 0.2, supervisor=CommandSupervisorReceipt())
        if cause == "timeout"
        else CommandCancelledError("cancelled", supervisor=CommandSupervisorReceipt())
        if cause == "cancel"
        else CommandOutputLimitExceeded(
            stream="stdout",
            limit_bytes=1,
            stdout="bounded",
            stderr="",
            supervisor=CommandSupervisorReceipt(),
        )
    )

    def abort(*argv: str, **kwargs: Any) -> CommandResult:
        if "--json-status-fd" not in argv:
            return CommandResult(0, "", "")
        fd = int(argv[argv.index("--json-status-fd") + 1])
        os.write(fd, b'{"child-pid":123}\n')
        if acknowledged:
            os.write(fd, b'{"exit-code":143}\n')
        raise error

    monkeypatch.setattr(module, "run_command", abort)
    with pytest.raises(type(error)) as caught:
        backend.execute(request, timeout=0.2, cancel_requested=None)
    assert caught.value is error
    isolation = getattr(error, "standard_isolation", None)
    assert (isolation is not None) is acknowledged
    if isolation is not None:
        assert isolation.backend.configuration_id == request.backend_configuration_id
        assert isolation.filesystem_cleanup == "verified"


@pytest.mark.process
@pytest.mark.parametrize("retain_tail", [False, True])
def test_native_headless_protocol_output_tail_is_explicit_and_metadata_covers_full_stream(
    tmp_path: Path, retain_tail: bool
) -> None:
    executable = shutil.which("bwrap")
    if executable is None:
        if os.environ.get("OH_GOON_REQUIRE_NATIVE_ISOLATION") == "1":
            pytest.fail("required native bwrap not installed")
        pytest.skip("native bwrap not installed")
    backend = BubblewrapProcessIsolation(executable)
    workspace = tmp_path / "repo"
    workspace.mkdir()
    record = (
        json.dumps({"type": "text", "part": {"text": "x" * 4096}}, separators=(",", ":")).encode()
        + b"\n"
    )
    final = b'{"type":"step_finish"}\n'
    original = record * 320 + final
    script = (
        "import sys; record="
        + repr(record)
        + "; sys.stdout.buffer.write(record*320+"
        + repr(final)
        + "); sys.stdout.buffer.flush()"
    )
    observed = hashlib.sha256()
    observed_bytes = 0

    def observe(stream: str, chunk: str) -> None:
        nonlocal observed_bytes
        if stream == "stdout":
            data = chunk.encode()
            observed.update(data)
            observed_bytes += len(data)

    with RepositoryWorkspaceAuthority.pin_repository_subject(workspace) as require_current:
        request = _request(
            backend,
            workspace,
            require_workspace_current=require_current,
            argv=("/usr/bin/python3", "-c", script),
            retain_output_tail=retain_tail,
        )
        _require_native(backend, request)
        if not retain_tail:
            with pytest.raises(CommandOutputLimitExceeded) as raised:
                backend.execute(request, timeout=10, cancel_requested=None)
            assert raised.value.supervisor.final_process_group_quiescent
            return
        result = backend.execute(request, timeout=10, cancel_requested=None, stream_output=observe)
    assert result.returncode == 0
    assert result.standard_isolation is not None
    assert result.supervisor.final_process_group_quiescent
    assert len(result.stdout.encode()) <= 1024 * 1024
    assert "omitted" in result.stdout and result.stdout.rstrip().endswith(final.decode().rstrip())
    assert observed_bytes == len(original) > 1024 * 1024
    evidence = result.stdout_evidence
    assert evidence is not None and evidence.byte_count == len(original)
    assert evidence.line_count == 321
    assert evidence.sha256 == observed.hexdigest() == hashlib.sha256(original).hexdigest()


@pytest.mark.parametrize("retain_tail", [1, "true", None])
def test_output_tail_request_requires_explicit_boolean(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, retain_tail: Any
) -> None:
    backend = _backend(tmp_path, monkeypatch)
    with pytest.raises(FleetPlanInvalid, match="resolved workspace and launch intent"):
        _request(backend, tmp_path, retain_output_tail=retain_tail)


@pytest.mark.parametrize("acknowledged,returncode", [(True, 0), (True, 17), (False, 0), (False, 1)])
def test_standard_receipt_requires_native_successful_exec_acknowledgement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, acknowledged: bool, returncode: int
) -> None:
    backend = _backend(tmp_path, monkeypatch)
    request = _request(backend, tmp_path)

    def command(*argv: str, **kwargs: Any) -> CommandResult:
        if "--json-status-fd" not in argv:
            return CommandResult(0, "", "")  # Namespace capability probe.
        fd = int(argv[argv.index("--json-status-fd") + 1])
        assert fd in kwargs["pass_fds"]
        status = b'{"child-pid": 123}\n'
        if acknowledged:
            status += json.dumps({"exit-code": returncode}).encode() + b"\n"
        os.write(fd, status)
        return CommandResult(returncode, "", "")

    monkeypatch.setattr(module, "run_command", command)
    if not acknowledged and returncode == 0:
        with pytest.raises(FleetPlanInvalid, match="without native workload-exec evidence"):
            backend.execute(request, timeout=5, cancel_requested=None)
        return
    result = backend.execute(request, timeout=5, cancel_requested=None)
    assert result.returncode == returncode
    evidence = result.standard_isolation
    if not acknowledged:
        assert evidence is None
    else:
        assert evidence is not None
        assert evidence.backend.configuration_id == request.backend_configuration_id
        assert evidence.backend.kernel_boundary == "shared_host_kernel"
        assert evidence.host_and_external_ip_networking == "denied"
        assert evidence.resource_owner == "none" and evidence.resources is None


@pytest.mark.process
def test_native_systemd_scope_reports_enforced_policy_after_nonzero_workload_exec(
    tmp_path: Path,
) -> None:
    tools = {name: shutil.which(name) for name in ("bwrap", "systemd-run")}
    if not all(tools.values()):
        if os.environ.get("OH_GOON_REQUIRE_NATIVE_ISOLATION") == "1":
            pytest.fail("required native bwrap/systemd-run not installed")
        pytest.skip("native bwrap/systemd-run not installed")
    assert tools["bwrap"] and tools["systemd-run"]
    backend = BubblewrapProcessIsolation(
        tools["bwrap"], standard_resource_executable=tools["systemd-run"]
    )
    repository = tmp_path / "repo"
    repository.mkdir()
    policy = CicdResourcePolicy(cpu_millis=1000, memory_mib=256, max_processes=64)
    with RepositoryWorkspaceAuthority.pin_repository_subject(repository) as require_current:
        request = _request(
            backend,
            repository,
            resources=policy,
            require_workspace_current=require_current,
            argv=("/bin/sh", "-c", "exit 17"),
        )
        _require_native(backend, request)
        result = backend.execute(request, timeout=10, cancel_requested=None)
    assert result.returncode == 17, result.stderr
    evidence = result.standard_isolation
    assert evidence is not None and evidence.resource_owner == "systemd_user_scope"
    assert evidence.resources is not None
    assert evidence.resources.cpu_millis == 1000
    assert evidence.resources.memory_bytes == 256 * 1024 * 1024
    assert evidence.resources.max_processes == 64
    assert (
        evidence.resources.cpu_usage_usec is None and evidence.resources.memory_peak_bytes is None
    )


@pytest.mark.process
def test_real_standard_boundary_denies_ip_host_services_and_foreign_files_but_keeps_workspace(
    tmp_path: Path,
) -> None:
    executable = shutil.which("bwrap")
    if executable is None:
        pytest.skip("native bwrap is not installed")
    backend = BubblewrapProcessIsolation(executable)
    workspace = tmp_path / "repo"
    workspace.mkdir()
    secret = tmp_path / "host-secret"
    secret.write_text("must not cross boundary")
    config = tmp_path / "selected.json"
    config.write_text("selected public configuration")
    script = """
import json, pathlib, socket, subprocess, sys
results = {}
for name, address in [('external', ('192.0.2.1', 443)), ('host-localhost', ('127.0.0.1', int(sys.argv[1])))]:
    try:
        with socket.create_connection(address, timeout=.3): pass
    except OSError: results[name] = 'denied'
    else: results[name] = 'connected'
try: socket.getaddrinfo('example.invalid', 443)
except OSError: results['dns'] = 'denied'
else: results['dns'] = 'resolved'
results['foreign'] = pathlib.Path(sys.argv[2]).exists()
results['config'] = pathlib.Path('/config/native/selected.json').read_text()
try: pathlib.Path('/config/native/selected.json').write_text('changed')
except OSError: results['config-write'] = 'denied'
else: results['config-write'] = 'changed'
pathlib.Path('agent-change.txt').write_text('retained change')
child = subprocess.run([sys.executable, '-c', "import socket; s=socket.socket(); s.settimeout(.3); print(s.connect_ex(('127.0.0.1', " + sys.argv[1] + ")))"], capture_output=True, text=True, check=True)
results['child-host-connect'] = int(child.stdout)
print(json.dumps(results))
"""
    with socket.socket() as host_service:
        host_service.bind(("127.0.0.1", 0))
        host_service.listen()
        port = str(host_service.getsockname()[1])
        with RepositoryWorkspaceAuthority.pin_repository_subject(workspace) as require_current:
            request = _request(
                backend,
                workspace,
                argv=("/usr/bin/python3", "-c", script, port, str(secret)),
                require_workspace_current=require_current,
                read_only_mounts=(
                    StandardReadOnlyMount(config, PurePosixPath("/config/native/selected.json")),
                ),
            )
            _require_native(backend, request)
            result = backend.execute(request, timeout=10, cancel_requested=None)
    assert result.returncode == 0, result.stderr
    observed = json.loads(result.stdout)
    assert observed["external"] == observed["host-localhost"] == observed["dns"] == "denied"
    assert not observed["foreign"]
    assert observed["config"] == "selected public configuration"
    assert observed["config-write"] == "denied"
    assert observed["child-host-connect"] != 0
    assert (workspace / "agent-change.txt").read_text() == "retained change"
    assert config.read_text() == "selected public configuration"


@pytest.mark.process
def test_real_broker_pathname_socket_is_the_only_host_ipc_mount(tmp_path: Path) -> None:
    executable = shutil.which("bwrap")
    if executable is None:
        pytest.skip("native bwrap is not installed")
    backend = BubblewrapProcessIsolation(executable)
    workspace = tmp_path / "repo"
    workspace.mkdir()
    broker = tmp_path / "broker"
    broker.mkdir(mode=0o700)
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
        server.bind(str(broker / "model.sock"))
        server.listen()
        server.settimeout(5)
        received: list[bytes] = []

        def respond() -> None:
            with server.accept()[0] as connection:
                received.append(connection.recv(100))
                connection.sendall(b"approved-model-response")

        with RepositoryWorkspaceAuthority.pin_repository_subject(workspace) as require_current:
            request = _request(
                backend,
                workspace,
                require_workspace_current=require_current,
                broker_directory=broker,
                argv=(
                    "/usr/bin/python3",
                    "-c",
                    "import socket; s=socket.socket(socket.AF_UNIX); s.connect('/run/oh-goon-broker/model.sock'); s.sendall(b'bounded-model-request'); print(s.recv(100).decode()); s.close()",
                ),
            )
            _require_native(backend, request)
            worker = Thread(target=respond)
            worker.start()
            try:
                result = backend.execute(request, timeout=10, cancel_requested=None)
            finally:
                worker.join(timeout=6)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "approved-model-response"
    assert received == [b"bounded-model-request"]


@pytest.mark.process
def test_cancellation_stops_detached_child_inside_the_same_pid_namespace(tmp_path: Path) -> None:
    executable = shutil.which("bwrap")
    if executable is None:
        pytest.skip("native bwrap is not installed")
    backend = BubblewrapProcessIsolation(executable)
    workspace = tmp_path / "repo"
    workspace.mkdir()
    script = """
import os, pathlib, time
if os.fork() == 0:
    os.setsid()
    marker = pathlib.Path('child-progress')
    while True:
        with marker.open('a') as stream: stream.write('running\\n')
        time.sleep(.01)
else:
    while True: time.sleep(1)
"""
    with RepositoryWorkspaceAuthority.pin_repository_subject(workspace) as require_current:
        request = _request(
            backend,
            workspace,
            require_workspace_current=require_current,
            argv=("/usr/bin/python3", "-c", script),
        )
        _require_native(backend, request)
        with pytest.raises(CommandCancelledError):
            backend.execute(
                request,
                timeout=5,
                cancel_requested=lambda: (workspace / "child-progress").is_file(),
            )
    stopped = (workspace / "child-progress").read_bytes()
    time.sleep(0.15)
    assert stopped
    assert (workspace / "child-progress").read_bytes() == stopped


@pytest.mark.process
def test_read_only_role_cannot_write_either_workspace_alias(tmp_path: Path) -> None:
    executable = shutil.which("bwrap")
    if executable is None:
        pytest.skip("native bwrap is not installed")
    backend = BubblewrapProcessIsolation(executable)
    workspace = tmp_path / "repo"
    workspace.mkdir()
    (workspace / "original-path").write_text(str(workspace))
    (workspace / "candidate").write_text("original candidate")
    script = """
import json, pathlib
original = pathlib.Path('/workspace/original-path').read_text()
result = []
for root in (pathlib.Path('/workspace'), pathlib.Path(original)):
    result.append((root / 'candidate').read_text())
    try: (root / 'candidate').write_text('changed')
    except OSError: result.append('denied')
    else: result.append('changed')
print(json.dumps(result))
"""
    with RepositoryWorkspaceAuthority.pin_repository_subject(workspace) as require_current:
        request = _request(
            backend,
            workspace,
            require_workspace_current=require_current,
            read_only_workspace=True,
            argv=("/usr/bin/python3", "-c", script),
        )
        _require_native(backend, request)
        result = backend.execute(request, timeout=5, cancel_requested=None)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == ["original candidate", "denied"] * 2
    assert (workspace / "candidate").read_text() == "original candidate"


@pytest.mark.process
def test_native_terminal_keeps_tty_input_output_and_controlling_terminal(tmp_path: Path) -> None:
    executable = shutil.which("bwrap")
    if executable is None:
        pytest.skip("native bwrap is not installed")
    backend = BubblewrapProcessIsolation(executable)
    workspace = tmp_path / "repo"
    workspace.mkdir()
    inputs = iter((b"native terminal input\n",))
    outputs: list[bytes] = []
    terminal = CommandTerminal(
        read_input=lambda size: next(inputs, None),
        write_output=outputs.append,
    )
    script = """
import os, sys
assert os.isatty(0) and os.isatty(1)
descriptor = os.open('/dev/tty', os.O_RDWR)
try: os.write(descriptor, b'private controlling terminal\\n')
finally: os.close(descriptor)
print('received:' + sys.stdin.readline().strip(), flush=True)
"""
    with RepositoryWorkspaceAuthority.pin_repository_subject(workspace) as require_current:
        request = _request(
            backend,
            workspace,
            require_workspace_current=require_current,
            terminal=terminal,
            argv=("/usr/bin/python3", "-c", script),
        )
        _require_native(backend, request)
        result = backend.execute(request, timeout=5, cancel_requested=None)
    assert result.returncode == 0, result.stderr
    rendered = b"".join(outputs)
    assert b"private controlling terminal" in rendered
    assert b"received:native terminal input" in rendered


@pytest.mark.process
def test_exact_temporary_runtime_alias_does_not_expose_its_host_parent(tmp_path: Path) -> None:
    executable = shutil.which("bwrap")
    if executable is None:
        pytest.skip("native bwrap is not installed")
    backend = BubblewrapProcessIsolation(executable)
    workspace, runtime = tmp_path / "repo", tmp_path / "selected-runtime"
    workspace.mkdir()
    runtime.mkdir()
    (runtime / "native-config").write_text("selected native data")
    (tmp_path / "unrelated-host-data").write_text("not admitted")
    script = """
import json, pathlib, sys
runtime = pathlib.Path(sys.argv[1])
result = {'selected': (runtime / 'native-config').read_text(), 'foreign': (runtime.parent / 'unrelated-host-data').exists()}
try: (runtime / 'native-config').write_text('changed')
except OSError: result['write'] = 'denied'
else: result['write'] = 'changed'
print(json.dumps(result))
"""
    with RepositoryWorkspaceAuthority.pin_repository_subject(workspace) as require_current:
        request = _request(
            backend,
            workspace,
            require_workspace_current=require_current,
            read_only_mounts=(StandardReadOnlyMount(runtime, PurePosixPath(str(runtime))),),
            argv=("/usr/bin/python3", "-c", script, str(runtime)),
        )
        _require_native(backend, request)
        result = backend.execute(request, timeout=5, cancel_requested=None)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "selected": "selected native data",
        "foreign": False,
        "write": "denied",
    }


@pytest.mark.process
def test_native_curl_node_and_stdio_mcp_children_cannot_bypass_the_boundary(tmp_path: Path) -> None:
    selected = {name: shutil.which(name) for name in ("bwrap", "curl", "node")}
    missing = [name for name, path in selected.items() if path is None]
    if missing:
        message = "native conformance requires installed " + ", ".join(missing)
        if os.environ.get("OH_GOON_REQUIRE_NATIVE_ISOLATION") == "1":
            pytest.fail(message)
        pytest.skip(message)
    assert selected["bwrap"] and selected["curl"] and selected["node"]
    backend = BubblewrapProcessIsolation(selected["bwrap"])
    workspace = tmp_path / "repo"
    workspace.mkdir()
    unrelated = tmp_path / "unrelated-repository"
    unrelated.mkdir()
    secret = tmp_path / "host-credential"
    secret.write_text("host credential canary")
    (unrelated / "private-source").write_text("unrelated repository canary")
    (workspace / "mcp.py").write_text("""
import json, pathlib, socket, sys
request = json.loads(sys.stdin.readline())
parameters = request['params']
try:
    with socket.create_connection(('127.0.0.1', parameters['port']), timeout=.5): pass
except OSError: network = 'denied'
else: network = 'connected'
print(json.dumps({'jsonrpc': '2.0', 'id': request['id'], 'result': {'network': network, 'foreign_visible': pathlib.Path(parameters['secret']).exists()}}), flush=True)
""")
    node_script = """
const net = require('net');
const connection = net.createConnection({host:'127.0.0.1', port:Number(process.argv[1])});
connection.setTimeout(500);
connection.once('connect', () => { console.log('connected'); process.exit(23); });
connection.once('error', error => { console.log('denied:' + error.code); process.exit(0); });
connection.once('timeout', () => { console.log('denied:timeout'); connection.destroy(); process.exit(0); });
"""
    coordinator = """
import json, pathlib, subprocess, sys
port, secret, foreign, curl, node = sys.argv[1:]
results = {}
for name, address in [('curl-host', 'http://127.0.0.1:' + port), ('curl-external', 'http://192.0.2.1:443'), ('curl-dns', 'http://example.invalid')]:
    response = subprocess.run([curl, '--noproxy', '*', '--connect-timeout', '.5', '--max-time', '1', '--silent', '--show-error', address], capture_output=True, text=True, timeout=2)
    results[name] = response.returncode
response = subprocess.run([node, '-e', NODE_SCRIPT, port], capture_output=True, text=True, timeout=2)
results['node-code'], results['node-network'] = response.returncode, response.stdout.strip()
request = {'jsonrpc':'2.0', 'id':1, 'method':'tools/call', 'params': {'port':int(port), 'secret':secret}}
response = subprocess.run([sys.executable, 'mcp.py'], input=json.dumps(request) + '\\n', capture_output=True, text=True, timeout=2, check=True)
results['mcp'] = json.loads(response.stdout)['result']
results['credential-visible'] = pathlib.Path(secret).exists()
results['unrelated-source-visible'] = pathlib.Path(foreign).exists()
print(json.dumps(results))
""".replace("NODE_SCRIPT", repr(node_script))

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"host service is reachable outside the sandbox")

        def log_message(self, format: str, *args: Any) -> None:
            pass

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        worker = Thread(target=server.serve_forever)
        worker.start()
        try:
            with socket.create_connection(("127.0.0.1", server.server_port), timeout=1) as control:
                control.sendall(b"GET / HTTP/1.0\r\n\r\n")
                assert b"200" in control.recv(1024)
            curl, node = Path(selected["curl"]).resolve(), Path(selected["node"]).resolve()
            with RepositoryWorkspaceAuthority.pin_repository_subject(workspace) as require_current:
                request = _request(
                    backend,
                    workspace,
                    require_workspace_current=require_current,
                    read_only_mounts=(
                        StandardReadOnlyMount(curl, PurePosixPath("/opt/conformance/curl")),
                        StandardReadOnlyMount(node, PurePosixPath("/opt/conformance/node")),
                    ),
                    argv=(
                        "/usr/bin/python3",
                        "-c",
                        coordinator,
                        str(server.server_port),
                        str(secret),
                        str(unrelated / "private-source"),
                        str(curl),
                        str(node),
                    ),
                )
                _require_native(backend, request)
                result = backend.execute(request, timeout=10, cancel_requested=None)
        finally:
            server.shutdown()
            worker.join(timeout=5)
    assert result.returncode == 0, result.stderr
    observed = json.loads(result.stdout)
    assert all(observed[name] != 0 for name in ("curl-host", "curl-external", "curl-dns"))
    assert observed["node-code"] == 0 and observed["node-network"].startswith("denied:")
    assert observed["mcp"] == {"network": "denied", "foreign_visible": False}
    assert not observed["credential-visible"] and not observed["unrelated-source-visible"]


@pytest.mark.parametrize(
    "attack", ["file", "symlink", "hardlink", "overlap", "protected", "mount-alias"]
)
def test_scoped_native_writes_reject_aliases_and_unsupported_roots_before_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, attack: str
) -> None:
    backend = _backend(tmp_path, monkeypatch)
    workspace = tmp_path / "repo"
    workspace.mkdir()
    allowed = workspace / "allowed"
    allowed.mkdir()
    changes: dict[str, Any] = {"allowed_write_roots": (allowed,)}
    if attack == "file":
        selected = workspace / "source.py"
        selected.write_text("source")
        changes["allowed_write_roots"] = (selected,)
    elif attack == "symlink":
        selected = workspace / "alias"
        selected.symlink_to(allowed, target_is_directory=True)
        changes["allowed_write_roots"] = (selected,)
    elif attack == "hardlink":
        private = tmp_path / "private"
        private.write_text("outside source")
        os.link(private, allowed / "alias")
    elif attack == "overlap":
        nested = allowed / "nested"
        nested.mkdir()
        changes["allowed_write_roots"] = (allowed, nested)
    elif attack == "protected":
        metadata = workspace / ".git"
        metadata.mkdir()
        changes["allowed_write_roots"] = (metadata,)
    else:
        changes["read_only_mounts"] = (StandardReadOnlyMount(allowed, PurePosixPath("/opt/alias")),)
    request = _request(
        backend, workspace, read_only_workspace=True, protected_execution=True, **changes
    )
    monkeypatch.setattr(
        module, "run_command", lambda *a, **kw: pytest.fail("unsafe topology launched")
    )
    with pytest.raises(FleetPlanInvalid):
        backend.require_standard_available(request)


def test_protected_preflight_reports_unavailable_without_untrusted_child_diagnostics(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from oh_goon.core.errors import CapabilityUnavailable

    backend = _backend(tmp_path, monkeypatch)
    calls = []

    def denied(*argv: str, **kwargs: Any) -> CommandResult:
        calls.append(argv)
        return CommandResult(1, "private child stdout", "PRIVATE-secret-path namespace EPERM")

    monkeypatch.setattr(module, "run_command", denied)
    request = _request(backend, tmp_path, read_only_workspace=True, protected_execution=True)
    with pytest.raises(
        CapabilityUnavailable, match="backend=bubblewrap.*mount_namespace_preflight"
    ) as error:
        backend.execute(request, timeout=1, cancel_requested=None)
    assert len(calls) == 1
    assert "PRIVATE" not in str(error.value) and "EPERM" not in str(error.value)


def test_preflight_uses_and_keys_actual_workspace_write_mount_topology(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    backend = _backend(tmp_path, monkeypatch)
    workspace = tmp_path / "repo"
    workspace.mkdir()
    first, second = workspace / "first", workspace / "second"
    first.mkdir()
    second.mkdir()
    calls = []

    def command(*argv: str, **kwargs: Any) -> CommandResult:
        calls.append(argv)
        return CommandResult(0, "", "")

    monkeypatch.setattr(module, "run_command", command)
    request = _request(
        backend,
        workspace,
        read_only_workspace=True,
        protected_execution=True,
        allowed_write_roots=(first,),
    )
    backend.require_standard_available(request)
    backend.require_standard_available(request)
    backend.require_standard_available(replace(request, allowed_write_roots=(second,)))
    assert len(calls) == 2
    for argv, selected in zip(calls, (first, second), strict=True):
        triples = tuple(argv[i : i + 3] for i in range(len(argv) - 2))
        assert ("--ro-bind", str(workspace), "/workspace") in triples
        assert ("--ro-bind", str(workspace), str(workspace)) in triples
        assert ("--bind", str(selected), "/workspace/" + selected.name) in triples
        assert ("--bind", str(selected), str(selected)) in triples
        assert argv[-1] == "/bin/true"


@pytest.mark.process
def test_protected_directory_writes_cannot_reopen_source_git_or_hardlink_alias(
    tmp_path: Path,
) -> None:
    executable = shutil.which("bwrap")
    if executable is None:
        pytest.skip("native Bubblewrap not installed")
    import subprocess

    backend = BubblewrapProcessIsolation(executable)
    workspace = tmp_path / "repo"
    workspace.mkdir()
    allowed = workspace / "allowed"
    allowed.mkdir()
    (workspace / "source").write_text("original")
    (workspace / "original-root").write_text(str(workspace))
    subprocess.run(["git", "init", "-q", str(workspace)], check=True)
    subprocess.run(["git", "-C", str(workspace), "add", "source"], check=True)
    index = (workspace / ".git/index").read_bytes()
    script = r"""
import json,os,pathlib,subprocess
root=pathlib.Path('/workspace'); original=pathlib.Path((root/'original-root').read_text()); facts=[]
for alias in (root,original):
    facts.append((alias/'source').read_text()=='original')
    for operation in (
        lambda:(alias/'source').write_text('bad'),
        lambda:(alias/'new-outside').write_text('bad'),
        lambda:(alias/'source').unlink(),
        lambda:(alias/'source').rename(alias/'moved'),
        lambda:(alias/'.git/config').write_text('bad'),
    ):
        try: operation()
        except OSError: facts.append(True)
        else: facts.append(False)
    command=subprocess.run(['/usr/bin/git','-C',str(alias),'update-index','--force-remove','source'],capture_output=True)
    facts.append(command.returncode!=0)
    try:
        os.link(alias/'source',alias/'allowed/escape')
        (alias/'allowed/escape').write_text('bad')
    except OSError: facts.append(True)
    else: facts.append(False)
    approved=alias/'allowed/new';approved.write_text('permitted');approved.rename(alias/'allowed/moved');(alias/'allowed/moved').unlink()
    facts.append(not (alias/'allowed/moved').exists())
print(json.dumps(facts))
"""
    with RepositoryWorkspaceAuthority.pin_repository_subject(workspace) as require_current:
        request = _request(
            backend,
            workspace,
            require_workspace_current=require_current,
            read_only_workspace=True,
            protected_execution=True,
            allowed_write_roots=(allowed,),
            argv=("/usr/bin/python3", "-c", script),
        )
        _require_native(backend, request)
        result = backend.execute(request, timeout=10, cancel_requested=None)
    assert result.returncode == 0, result.stderr
    assert all(json.loads(result.stdout))
    assert (workspace / "source").read_text() == "original"
    assert (workspace / ".git/index").read_bytes() == index
    assert result.standard_isolation is not None
    assert result.standard_isolation.scoped_write_roots == ("allowed",)
    assert result.standard_isolation.protected_paths == (".git",)
    assert result.standard_isolation.filesystem_cleanup == "verified"


@pytest.mark.process
def test_protected_native_auth_file_refresh_is_in_place_and_exposure_is_explicit(
    tmp_path: Path,
) -> None:
    executable = shutil.which("bwrap")
    if executable is None:
        pytest.skip("native Bubblewrap not installed")
    backend = BubblewrapProcessIsolation(executable)
    workspace, private = tmp_path / "repo", tmp_path / "native-auth"
    workspace.mkdir()
    private.mkdir()
    auth = private / "auth.json"
    auth.write_text("synthetic old authentication")
    auth.chmod(0o600)
    sibling = private / "private-sibling"
    sibling.write_text("unselected private state")
    before = auth.stat()
    (workspace / "original-private").write_text(str(sibling))
    (workspace / "source").write_text("original")
    script = r"""
import json,pathlib,socket,sys
root=pathlib.Path('/workspace');facts=[]
try: (root/'source').write_text('bad')
except OSError: facts.append(True)
else: facts.append(False)
try: pathlib.Path((root/'original-private').read_text()).read_text()
except OSError: facts.append(True)
else: facts.append(False)
auth=pathlib.Path('/oh-goon-codex-home/auth.json')
facts.append(auth.read_text()=='synthetic old authentication')
with auth.open('w') as stream: stream.write('synthetic native refresh');stream.flush()
pathlib.Path('/oh-goon-codex-home/private-state').write_text('disposable')
with socket.create_connection(('127.0.0.1',int(sys.argv[1])),timeout=1): facts.append(True)
facts.append(sys.stdin.buffer.read()==b'full unchanged stdin')
print(json.dumps(facts))
"""
    with ThreadingHTTPServer(("127.0.0.1", 0), BaseHTTPRequestHandler) as server:
        with RepositoryWorkspaceAuthority.pin_repository_subject(workspace) as require_current:
            request = _request(
                backend,
                workspace,
                require_workspace_current=require_current,
                read_only_workspace=True,
                protected_execution=True,
                credential_file=auth,
                host_network=True,
                input_data=b"full unchanged stdin",
                argv=("/usr/bin/python3", "-c", script, str(server.server_port)),
            )
            _require_native(backend, request)
            result = backend.execute(request, timeout=10, cancel_requested=None)
    assert result.returncode == 0, result.stderr
    assert all(json.loads(result.stdout))
    assert auth.read_text() == "synthetic native refresh"
    after = auth.stat()
    assert (before.st_dev, before.st_ino) == (after.st_dev, after.st_ino)
    assert sorted(path.name for path in private.iterdir()) == ["auth.json", "private-sibling"]
    assert (workspace / "source").read_text() == "original"
    evidence = result.standard_isolation
    assert evidence is not None
    assert evidence.host_and_external_ip_networking == "host_unrestricted"
    assert evidence.credential_visibility == "codex_auth_file_read_write"
    assert evidence.preflight == "verified" and evidence.filesystem_cleanup == "verified"


@pytest.mark.parametrize("timed_out", [False, True])
def test_cleanup_failure_cannot_publish_verified_success_or_replace_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, timed_out: bool
) -> None:
    backend = _backend(tmp_path, monkeypatch)
    request = _request(backend, tmp_path)
    timeout = CommandTimeoutExpired(request.argv, 1, supervisor=CommandSupervisorReceipt())

    def command(*argv: str, **kwargs: Any) -> CommandResult:
        _native_exec_ack(argv)
        if timed_out and "--json-status-fd" in argv:
            raise timeout
        return CommandResult(0, "", "")

    monkeypatch.setattr(module, "run_command", command)
    backend.require_standard_available(request)

    class CleanupDenied(tempfile.TemporaryDirectory):
        def __exit__(self, *args):
            super().__exit__(*args)
            raise PermissionError("synthetic cleanup denial")

    monkeypatch.setattr(module.tempfile, "TemporaryDirectory", CleanupDenied)
    if timed_out:
        with pytest.raises(CommandTimeoutExpired) as caught:
            backend.execute(request, timeout=1, cancel_requested=None)
        assert caught.value is timeout
        assert timeout.standard_isolation is not None
        assert timeout.standard_isolation.filesystem_cleanup == "unverified"
    else:
        result = backend.execute(request, timeout=1, cancel_requested=None)
        assert result.returncode == 0
        assert result.standard_isolation is not None
        assert result.standard_isolation.filesystem_cleanup == "unverified"
        assert result.stderr == "Standard sandbox filesystem cleanup is unverified"


def test_protected_preflight_cannot_cache_nonquiescent_native_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from oh_goon.core.errors import CapabilityUnavailable

    backend = _backend(tmp_path, monkeypatch)
    calls = []

    def unsettled(*argv: str, **kwargs: Any) -> CommandResult:
        calls.append(argv)
        return CommandResult(
            0, "", "", supervisor=CommandSupervisorReceipt(final_process_group_quiescent=False)
        )

    monkeypatch.setattr(module, "run_command", unsettled)
    request = _request(backend, tmp_path, read_only_workspace=True, protected_execution=True)
    for _ in range(2):
        with pytest.raises(CapabilityUnavailable, match="process_tree_quiescence_unverified"):
            backend.execute(request, timeout=1, cancel_requested=None)
    assert len(calls) == 2 and all(argv[-1] == "/bin/true" for argv in calls)


def test_protected_native_temp_state_cannot_materialize_under_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from oh_goon.core.errors import CapabilityUnavailable

    backend = _backend(tmp_path, monkeypatch)
    workspace = tmp_path / "repo"
    workspace.mkdir()
    monkeypatch.setattr(module.tempfile, "gettempdir", lambda: str(workspace))
    monkeypatch.setattr(
        module, "run_command", lambda *a, **kw: pytest.fail("overlapping scratch launched")
    )
    request = _request(backend, workspace, read_only_workspace=True, protected_execution=True)
    with pytest.raises(CapabilityUnavailable, match="temporary_state_overlaps_source"):
        backend.require_standard_available(request)
    assert list(workspace.iterdir()) == []


def test_protected_missing_exec_ack_is_a_setup_failure_with_bounded_facts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from oh_goon.core.errors import CapabilityUnavailable

    backend = _backend(tmp_path, monkeypatch)
    calls = []

    def command(*argv: str, **kwargs: Any) -> CommandResult:
        calls.append(argv)
        if "--json-status-fd" not in argv:
            return CommandResult(0, "", "")
        return CommandResult(32, "PRIVATE stdout", "PRIVATE child stderr")

    monkeypatch.setattr(module, "run_command", command)
    request = _request(backend, tmp_path, read_only_workspace=True, protected_execution=True)
    with pytest.raises(
        CapabilityUnavailable, match="native_workload_exec_ack.*child_exit=32"
    ) as caught:
        backend.execute(request, timeout=1, cancel_requested=None)
    assert len(calls) == 2
    assert "PRIVATE" not in str(caught.value)
