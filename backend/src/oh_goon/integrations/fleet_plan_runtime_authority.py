from __future__ import annotations

import hashlib
import json
import shlex
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Mapping

from oh_goon.core.errors import ExecutionAuthorityDivergence
from oh_goon.core.models.execution_authority_economics import (
    ExecutionAuthorityEconomicsReceipt,
)
from oh_goon.core.path_layouts import SANDBOX_PATHS
from oh_goon.engine.command import RuntimeAuthorityReceipt
from oh_goon.integrations._fleet_plan_host_resources import HostToolBinding
from oh_goon.integrations._fleet_plan_sandbox_launch import (
    SandboxProcessLaunch,
    sandbox_process_launch,
)
from oh_goon.integrations.content_snapshot import (
    SnapshotBinding,
    verify_snapshot,
)
from oh_goon.integrations.fleet_plan_process_isolation import IsolatedProcessRequest


@dataclass(frozen=True, slots=True)
class RuntimeAuthoritySources:
    """Transient source paths behind the single runtime-authority receipt.

    These paths are never authority by themselves. Atomic launch binding must copy/open
    them and prove the observed bytes still match ``receipt`` before workload execution.
    """

    receipt: RuntimeAuthorityReceipt
    executable_host_path: Path
    interpreter_host_path: Path | None = None
    economics: ExecutionAuthorityEconomicsReceipt | None = None


@dataclass(frozen=True, slots=True)
class _ExpectedRuntimeFile:
    host_path: Path
    identity: str
    byte_count: int
    reused: bool
    provenance: str
    recompute_reason: str | None = None


def reconcile_runtime_authority(
    request: IsolatedProcessRequest,
    launch: SandboxProcessLaunch | None = None,
) -> RuntimeAuthorityReceipt:
    """Observe one strict workload launch and reconcile it with admitted/materialized authority.

    This is deliberately a pre-exec reconciliation cut. It proves that the path/provider/
    interpreter the backend is about to attempt still matches the admitted strict attempt.
    Atomic path-to-exec handoff remains owned by the later TOCTOU closure.
    """

    return resolve_runtime_authority_sources(request, launch).receipt


def expected_runtime_authority_sources(
    request: IsolatedProcessRequest,
    launch: SandboxProcessLaunch | None = None,
) -> RuntimeAuthoritySources:
    """Resolve the admitted runtime expectation without re-reading known raw bytes.

    This is safe only when a later consumer independently proves the actual launch
    bytes against the returned identities. Bubblewrap does that while sealing the
    executable/interpreter into memfds; OpenSandbox does it inside the reconstructed
    remote root. The expectation itself is never terminal runtime proof.
    """

    launch = launch or sandbox_process_launch(request)
    if not launch.argv:
        raise _divergence("strict workload launch has empty argv")
    sandbox_path, executable, provider_identity = _resolve_admitted_executable(
        request,
        launch.argv[0],
        launch.env,
        launch.cwd,
    )
    interpreter_path, interpreter = _expected_interpreter(request, executable.host_path)
    expected_files = (executable,) + ((interpreter,) if interpreter is not None else ())
    reused = tuple(item for item in expected_files if item.reused)
    recomputed = tuple(item for item in expected_files if not item.reused)
    return RuntimeAuthoritySources(
        receipt=_runtime_receipt(
            request,
            launch,
            executable_path=sandbox_path,
            executable_identity=executable.identity,
            executable_byte_count=executable.byte_count,
            provider_identity=provider_identity,
            interpreter_path=interpreter_path,
            interpreter_identity=interpreter.identity if interpreter is not None else None,
        ),
        executable_host_path=executable.host_path,
        interpreter_host_path=interpreter.host_path if interpreter is not None else None,
        economics=ExecutionAuthorityEconomicsReceipt(
            scope="runtime-launch",
            candidate_units=len(expected_files),
            reused_units=len(reused),
            recomputed_units=len(recomputed),
            reused_bytes=sum(item.byte_count for item in reused),
            recomputed_bytes=sum(item.byte_count for item in recomputed),
            reuse_provenance=tuple(item.provenance for item in reused),
            reuse_identities=tuple(item.identity for item in reused),
            recompute_reasons=tuple(
                item.recompute_reason or "identity-not-reusable" for item in recomputed
            ),
        ),
    )


def resolve_runtime_authority_sources(
    request: IsolatedProcessRequest,
    launch: SandboxProcessLaunch | None = None,
) -> RuntimeAuthoritySources:
    """Resolve the reconciled workload and retain only transient host sources.

    The returned receipt remains the sole runtime authority fact. Host paths are an
    implementation handoff for the atomic-open phase and must never be serialized as
    authority.
    """

    expected = expected_runtime_authority_sources(request, launch)
    observed_executable = _file_identity(expected.executable_host_path)
    if observed_executable != expected.receipt.executable_identity:
        raise _divergence(
            f"runtime executable bytes diverged at {expected.receipt.executable_path}: "
            f"expected {expected.receipt.executable_identity}, observed {observed_executable}"
        )
    if expected.interpreter_host_path is not None:
        observed_interpreter = _file_identity(expected.interpreter_host_path)
        if observed_interpreter != expected.receipt.interpreter_identity:
            raise _divergence(
                f"runtime interpreter bytes diverged at {expected.receipt.interpreter_path}: "
                f"expected {expected.receipt.interpreter_identity}, observed {observed_interpreter}"
            )
    return expected


def _runtime_receipt(
    request: IsolatedProcessRequest,
    launch: SandboxProcessLaunch,
    *,
    executable_path: PurePosixPath,
    executable_identity: str,
    executable_byte_count: int,
    provider_identity: str,
    interpreter_path: str | None,
    interpreter_identity: str | None,
) -> RuntimeAuthorityReceipt:
    return RuntimeAuthorityReceipt(
        executable_path=str(executable_path),
        executable_identity=executable_identity,
        executable_byte_count=executable_byte_count,
        path_authority_identity=_identity({"PATH": launch.env.get("PATH", "")}),
        environment_identity=_identity({"environment": sorted(launch.env.items())}),
        provider_identity=provider_identity,
        runtime_identity=_receipt_sha(request.attempt.runtime_root.identity.sha256),
        interpreter_path=interpreter_path,
        interpreter_identity=interpreter_identity,
        execution_materialization_identity=request.attempt.executable_materialization.receipt_identity,
    )


def observed_runtime_authority_payload(receipt: RuntimeAuthorityReceipt) -> dict[str, str | None]:
    """Transport-safe subset that an executor can independently observe at launch."""

    return {
        "executable_path": receipt.executable_path,
        "executable_identity": receipt.executable_identity,
        "path_authority_identity": receipt.path_authority_identity,
        "environment_identity": receipt.environment_identity,
        "interpreter_path": receipt.interpreter_path,
        "interpreter_identity": receipt.interpreter_identity,
    }


def require_runtime_authority_observation(
    expected: RuntimeAuthorityReceipt,
    observed: Mapping[str, object],
) -> None:
    """Fail closed when a remote executor reports a different workload launch."""

    expected_payload = observed_runtime_authority_payload(expected)
    normalized = {name: observed.get(name) for name in expected_payload}
    if normalized != expected_payload:
        mismatches = ", ".join(
            name for name, value in expected_payload.items() if normalized.get(name) != value
        )
        raise _divergence(
            "remote runtime executable observation differs from admitted/materialized "
            f"authority: {mismatches or 'unknown fields'}"
        )


def _resolve_admitted_executable(
    request: IsolatedProcessRequest,
    value: str,
    env: Mapping[str, str],
    cwd: PurePosixPath,
) -> tuple[PurePosixPath, _ExpectedRuntimeFile, str]:
    candidate = PurePosixPath(value)
    if candidate.is_absolute():
        source, provider = _map_admitted_executable(request, candidate)
        return candidate, source, provider
    if "/" in value or "\\" in value:
        relative = PurePosixPath(value)
        if relative.is_absolute() or ".." in relative.parts:
            raise _divergence(
                f"runtime executable path is not normalized for sandbox launch: {value!r}"
            )
        sandbox = PurePosixPath(cwd) / relative
        source, provider = _map_admitted_executable(request, sandbox)
        return sandbox, source, provider
    for root in env.get("PATH", "").split(":"):
        if not root:
            continue
        sandbox = PurePosixPath(root) / value
        try:
            source, provider = _map_admitted_executable(request, sandbox)
        except ExecutionAuthorityDivergence:
            continue
        if source.host_path.is_file():
            return sandbox, source, provider
    raise _divergence(f"runtime executable is not present under admitted sandbox PATH: {value}")


def _map_admitted_executable(
    request: IsolatedProcessRequest, sandbox_path: PurePosixPath
) -> tuple[_ExpectedRuntimeFile, str]:
    binding = _toolchain_binding(request, sandbox_path)
    if binding is not None:
        _verify_host_tool_binding(binding)
        host_path = binding.execution_snapshot_path
        reused = bool(binding.execution_sha256)
        identity = binding.execution_sha256 or _file_identity(host_path)
        return (
            _ExpectedRuntimeFile(
                host_path=host_path,
                identity=identity,
                byte_count=host_path.stat().st_size,
                reused=reused,
                provenance=f"prepared-host-tool:{binding.name}",
                recompute_reason=None if reused else f"missing-prepared-raw-sha:{binding.name}",
            ),
            _receipt_sha(binding.identity.sha256),
        )

    workspace = _workspace_executable(request, sandbox_path)
    if workspace is not None:
        return workspace

    managed = _map_managed_binding(request.attempt.sandbox_bindings, sandbox_path)
    if managed is not None:
        host_path, managed_binding = managed
        verify_snapshot(
            managed_binding.snapshot_path,
            managed_binding.identity,
            label=f"runtime managed executable provider {managed_binding.declared_path}",
        )
        identity = _file_identity(host_path)
        return (
            _ExpectedRuntimeFile(
                host_path=host_path,
                identity=identity,
                byte_count=host_path.stat().st_size,
                reused=False,
                provenance=f"managed-snapshot:{managed_binding.declared_path}",
                recompute_reason="managed-child-raw-identity-not-prebound",
            ),
            _receipt_sha(managed_binding.identity.sha256),
        )

    raise _divergence(
        f"runtime selected executable outside admitted execution roots: {sandbox_path}"
    )


def _toolchain_binding(
    request: IsolatedProcessRequest, sandbox_path: PurePosixPath
) -> HostToolBinding | None:
    name: str | None = None
    if sandbox_path == PurePosixPath("/bin/sh"):
        name = "sh"
    else:
        try:
            relative = sandbox_path.relative_to(SANDBOX_PATHS.toolchain)
        except ValueError:
            relative = None
        if relative is not None and len(relative.parts) == 1:
            name = relative.name
    if name is None:
        return None
    binding = next((item for item in request.attempt.host_tools if item.name == name), None)
    if binding is None:
        raise _divergence(f"runtime selected undeclared host executable: {name}")
    return binding


def _workspace_executable(
    request: IsolatedProcessRequest, sandbox_path: PurePosixPath
) -> tuple[_ExpectedRuntimeFile, str] | None:
    try:
        relative = sandbox_path.relative_to(SANDBOX_PATHS.workspace)
    except ValueError:
        return None
    receipt = request.attempt.executable_materialization.direct_workspace_executable
    relative_text = relative.as_posix()
    if receipt is None or receipt.relative_path != relative_text:
        raise _divergence(
            f"runtime selected workspace executable outside materialized authority: {sandbox_path}"
        )
    host_path = request.attempt.paths.workspace / relative
    return (
        _ExpectedRuntimeFile(
            host_path=host_path,
            identity=receipt.sha256,
            byte_count=receipt.byte_count,
            reused=True,
            provenance="materialized-workspace-executable",
        ),
        receipt.sha256,
    )


def _map_managed_binding(
    bindings: tuple[SnapshotBinding, ...], sandbox_path: PurePosixPath
) -> tuple[Path, SnapshotBinding] | None:
    for binding in bindings:
        root = binding.sandbox_path
        if root is None:
            continue
        if binding.snapshot_path.is_file():
            if sandbox_path == root:
                return binding.snapshot_path, binding
            continue
        try:
            relative = sandbox_path.relative_to(root)
        except ValueError:
            continue
        candidate = binding.snapshot_path.joinpath(*relative.parts)
        if candidate.is_file():
            return candidate, binding
    return None


def _expected_interpreter(
    request: IsolatedProcessRequest,
    executable: Path,
) -> tuple[str | None, _ExpectedRuntimeFile | None]:
    shebang = _shebang(executable)
    if shebang is None:
        return None, None
    interpreter = PurePosixPath(shebang[0])
    if not interpreter.is_absolute():
        raise _divergence(f"runtime executable has non-absolute shebang interpreter: {shebang[0]}")
    expected = _map_interpreter(request, interpreter)
    return str(interpreter), expected


def _map_interpreter(
    request: IsolatedProcessRequest, interpreter: PurePosixPath
) -> _ExpectedRuntimeFile:
    attempt = request.attempt
    if interpreter == PurePosixPath("/bin/sh"):
        binding = next((item for item in attempt.host_tools if item.name == "sh"), None)
        if binding is None:
            raise _divergence("runtime shebang requires /bin/sh without admitted sh authority")
        _verify_host_tool_binding(binding)
        host_path = binding.execution_snapshot_path
        reused = bool(binding.execution_sha256)
        return _ExpectedRuntimeFile(
            host_path=host_path,
            identity=binding.execution_sha256 or _file_identity(host_path),
            byte_count=host_path.stat().st_size,
            reused=reused,
            provenance="prepared-host-tool:sh",
            recompute_reason=None if reused else "missing-prepared-raw-sha:sh",
        )
    try:
        relative = interpreter.relative_to(SANDBOX_PATHS.toolchain)
    except ValueError:
        relative = None
    if relative is not None and len(relative.parts) == 1:
        binding = next((item for item in attempt.host_tools if item.name == relative.name), None)
        if binding is None:
            raise _divergence(f"runtime shebang selected undeclared interpreter: {interpreter}")
        _verify_host_tool_binding(binding)
        host_path = binding.execution_snapshot_path
        reused = bool(binding.execution_sha256)
        return _ExpectedRuntimeFile(
            host_path=host_path,
            identity=binding.execution_sha256 or _file_identity(host_path),
            byte_count=host_path.stat().st_size,
            reused=reused,
            provenance=f"prepared-host-tool:{relative.name}",
            recompute_reason=(None if reused else f"missing-prepared-raw-sha:{relative.name}"),
        )

    runtime_path = attempt.runtime_root.root.joinpath(*interpreter.relative_to("/").parts)
    if not runtime_path.is_file():
        raise _divergence(
            f"runtime shebang interpreter is absent from immutable runtime root: {interpreter}"
        )
    return _ExpectedRuntimeFile(
        host_path=runtime_path,
        identity=_file_identity(runtime_path),
        byte_count=runtime_path.stat().st_size,
        reused=False,
        provenance=f"runtime-root:{interpreter}",
        recompute_reason="runtime-root-interpreter-raw-identity-not-prebound",
    )


def _shebang(path: Path) -> tuple[str, ...] | None:
    try:
        with path.open("rb") as handle:
            first = handle.readline(4096)
    except OSError as exc:
        raise _divergence(f"cannot inspect runtime executable interpreter: {path}") from exc
    if not first.startswith(b"#!"):
        return None
    try:
        text = first[2:].decode("utf-8", errors="strict").strip()
        parts = tuple(shlex.split(text))
    except (UnicodeDecodeError, ValueError) as exc:
        raise _divergence(f"runtime executable has invalid shebang: {path}") from exc
    if not parts:
        raise _divergence(f"runtime executable has empty shebang: {path}")
    return parts


def _verify_host_tool_binding(binding: HostToolBinding) -> None:
    verify_snapshot(
        binding.snapshot_path,
        binding.identity,
        label=f"runtime admitted host tool {binding.name}",
    )
    if (
        binding.execution_snapshot_path == binding.snapshot_path
        and binding.execution_identity == binding.identity
    ):
        return
    verify_snapshot(
        binding.execution_snapshot_path,
        binding.execution_identity,
        label=f"runtime admitted host tool launcher {binding.name}",
    )


def _file_identity(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return "sha256:" + digest.hexdigest()


def _receipt_sha(value: str) -> str:
    return value if value.startswith("sha256:") else "sha256:" + value


def _identity(payload: object) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _divergence(message: str) -> ExecutionAuthorityDivergence:
    return ExecutionAuthorityDivergence(message)
