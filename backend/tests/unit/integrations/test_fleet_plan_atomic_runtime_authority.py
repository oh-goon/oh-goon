from __future__ import annotations

import errno
import fcntl
import hashlib
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from oh_goon.core.errors import ExecutionAuthorityDivergence, FleetPlanInvalid
from oh_goon.core.path_layouts import AttemptPathLayout
from oh_goon.integrations import fleet_plan_runtime_authority
from oh_goon.integrations._fleet_plan_host_resources import HostToolBinding
from oh_goon.integrations._fleet_plan_runtime_root import RuntimeRootBinding
from oh_goon.integrations._fleet_plan_sandbox_launch import sandbox_process_launch
from oh_goon.integrations.content_snapshot import snapshot_identity
from oh_goon.integrations.executable_materialization_authority import (
    StepExecutableMaterializationReceipt,
)
from oh_goon.integrations.fleet_plan_atomic_runtime_authority import (
    bind_atomic_runtime_authority,
)
from oh_goon.integrations.fleet_plan_process_isolation import IsolatedProcessRequest


def _host_tool(tmp_path: Path, name: str, payload: bytes) -> HostToolBinding:
    tools = tmp_path / "tools"
    tools.mkdir(exist_ok=True)
    path = tools / name
    path.write_bytes(payload)
    path.chmod(0o755)
    identity = snapshot_identity(path)
    return HostToolBinding(
        name=name,
        source_path=path,
        snapshot_path=path,
        identity=identity,
        execution_sha256="sha256:" + hashlib.sha256(payload).hexdigest(),
    )


def _attempt(tmp_path: Path, tools: tuple[HostToolBinding, ...]):
    paths = AttemptPathLayout.from_root(tmp_path / "attempt")
    for path in (paths.workspace, paths.home, paths.tmp, paths.cache, paths.config, paths.etc):
        path.mkdir(parents=True, exist_ok=True)
    runtime_root = tmp_path / "runtime-root"
    runtime_root.mkdir()
    return SimpleNamespace(
        paths=paths,
        host_tools=tools,
        sandbox_bindings=(),
        runtime_root=RuntimeRootBinding(
            root=runtime_root, identity=snapshot_identity(runtime_root)
        ),
        executable_materialization=StepExecutableMaterializationReceipt(),
    )


def _request(attempt, argv: tuple[str, ...]) -> IsolatedProcessRequest:
    return IsolatedProcessRequest(
        attempt=attempt,
        argv=argv,
        cwd=attempt.paths.workspace,
        env={"PATH": "/ignored/host/path"},
    )


def _fd_bytes(descriptor: int) -> bytes:
    size = os.fstat(descriptor).st_size
    return os.pread(descriptor, size, 0)


def test_atomic_binding_seals_exact_executable_bytes_against_source_replacement(
    tmp_path: Path,
) -> None:
    original = b"\x7fELFatomic-original"
    tool = _host_tool(tmp_path, "true", original)
    attempt = _attempt(tmp_path, (tool,))
    request = _request(attempt, ("true",))
    launch = sandbox_process_launch(request)

    with bind_atomic_runtime_authority(request, launch) as lease:
        tool.execution_snapshot_path.write_bytes(b"\x7fELFreplaced-after-bind")
        assert _fd_bytes(lease.executable.descriptor) == original
        assert lease.receipt.executable_identity == "sha256:" + hashlib.sha256(original).hexdigest()
        assert lease.bubblewrap_bindings == (lease.executable,)
        assert lease.executable.sandbox_path.as_posix() == "/toolchain/bin/true"
        assert lease.economics is not None
        assert lease.economics.candidate_units == 1
        assert lease.economics.reused_units == 1
        assert lease.economics.recomputed_units == 0
        assert lease.economics.reused_bytes == len(original)
        assert lease.economics.recomputed_bytes == 0
        assert lease.economics.terminal_proof_bytes == len(original)
        assert lease.economics.reuse_provenance == ("prepared-host-tool:true",)
        get_seals = getattr(fcntl, "F_GET_SEALS", 1034)
        required = (
            getattr(fcntl, "F_SEAL_WRITE", 0x0008)
            | getattr(fcntl, "F_SEAL_GROW", 0x0004)
            | getattr(fcntl, "F_SEAL_SHRINK", 0x0002)
            | getattr(fcntl, "F_SEAL_SEAL", 0x0001)
        )
        seals = fcntl.fcntl(lease.executable.descriptor, get_seals)
        assert seals & required == required
        with pytest.raises(OSError) as caught:
            os.pwrite(lease.executable.descriptor, b"x", 0)
        assert caught.value.errno in {errno.EPERM, errno.EBADF}


def test_atomic_binding_rewinds_sealed_descriptor_for_bubblewrap_data_mount(
    tmp_path: Path,
) -> None:
    payload = b"\x7fELFrewind-before-bwrap"
    tool = _host_tool(tmp_path, "true", payload)
    request = _request(_attempt(tmp_path, (tool,)), ("true",))

    with bind_atomic_runtime_authority(request) as lease:
        assert os.read(lease.executable.descriptor, len(payload)) == payload
        assert os.read(lease.executable.descriptor, 1) == b""
        lease.rewind_for_bubblewrap()
        assert os.read(lease.executable.descriptor, len(payload)) == payload


def test_atomic_binding_reuses_prepared_raw_identity_before_terminal_seal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = b"\x7fELFprepared-identity"
    tool = _host_tool(tmp_path, "true", payload)
    attempt = _attempt(tmp_path, (tool,))
    request = _request(attempt, ("true",))

    def unexpected_hash(_path: Path) -> str:
        raise AssertionError("prepared host-tool raw identity must be reused")

    monkeypatch.setattr(fleet_plan_runtime_authority, "_file_identity", unexpected_hash)

    with bind_atomic_runtime_authority(request) as lease:
        assert _fd_bytes(lease.executable.descriptor) == payload
        assert lease.economics is not None
        assert lease.economics.reused_units == 1
        assert lease.economics.terminal_proof_bytes == len(payload)


def test_atomic_binding_seals_executable_and_shebang_interpreter(tmp_path: Path) -> None:
    node_bytes = b"\x7fELFnode-atomic"
    npm_bytes = b"#!/toolchain/bin/node\nrequire('npm')\n"
    node = _host_tool(tmp_path, "node", node_bytes)
    npm = _host_tool(tmp_path, "npm", npm_bytes)
    attempt = _attempt(tmp_path, (node, npm))
    request = _request(attempt, ("npm", "run", "test"))

    with bind_atomic_runtime_authority(request) as lease:
        assert lease.interpreter is not None
        assert lease.receipt.interpreter_path == "/toolchain/bin/node"
        assert _fd_bytes(lease.executable.descriptor) == npm_bytes
        assert _fd_bytes(lease.interpreter.descriptor) == node_bytes
        assert lease.pass_fds == (lease.executable.descriptor, lease.interpreter.descriptor)
        assert lease.bubblewrap_bindings[-1] is lease.interpreter
        assert lease.interpreter.sandbox_path.as_posix() == "/toolchain/bin/node"
        assert lease.economics is not None
        assert lease.economics.candidate_units == 2
        assert lease.economics.reused_units == 2
        assert lease.economics.reused_bytes == len(npm_bytes) + len(node_bytes)
        assert lease.economics.terminal_proof_bytes == len(npm_bytes) + len(node_bytes)


def test_atomic_binding_invalidates_reused_identity_when_source_bytes_change(
    tmp_path: Path,
) -> None:
    tool = _host_tool(tmp_path, "true", b"\x7fELFadmitted")
    attempt = _attempt(tmp_path, (tool,))
    request = _request(attempt, ("true",))
    tool.execution_snapshot_path.write_bytes(b"\x7fELFchanged-before-seal")

    with pytest.raises(
        (ExecutionAuthorityDivergence, FleetPlanInvalid),
        match="changed",
    ):
        bind_atomic_runtime_authority(request)


def test_atomic_binding_recomputes_when_prepared_raw_identity_is_unavailable(
    tmp_path: Path,
) -> None:
    payload = b"\x7fELFcold-fallback"
    prepared = _host_tool(tmp_path, "true", payload)
    tool = HostToolBinding(
        name=prepared.name,
        source_path=prepared.source_path,
        snapshot_path=prepared.snapshot_path,
        identity=prepared.identity,
    )
    attempt = _attempt(tmp_path, (tool,))
    request = _request(attempt, ("true",))

    with bind_atomic_runtime_authority(request) as lease:
        assert lease.economics is not None
        assert lease.economics.candidate_units == 1
        assert lease.economics.reused_units == 0
        assert lease.economics.recomputed_units == 1
        assert lease.economics.recomputed_bytes == len(payload)
        assert lease.economics.terminal_proof_bytes == len(payload)
        assert lease.economics.recompute_reasons == ("missing-prepared-raw-sha:true",)
