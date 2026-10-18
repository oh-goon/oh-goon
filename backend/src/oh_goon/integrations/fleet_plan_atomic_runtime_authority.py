from __future__ import annotations

import os
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath

from oh_goon.core.errors import ExecutionAuthorityDivergence
from oh_goon.core.models.execution_authority_economics import (
    ExecutionAuthorityEconomicsReceipt,
)
from oh_goon.engine.atomic_execution_bytes import (
    SealedExecutionBytes,
    seal_execution_bytes,
    verify_sealed_execution_descriptor,
)
from oh_goon.engine.command import RuntimeAuthorityReceipt
from oh_goon.integrations._fleet_plan_sandbox_launch import (
    SandboxProcessLaunch,
    sandbox_process_launch,
)
from oh_goon.integrations.fleet_plan_process_isolation import IsolatedProcessRequest
from oh_goon.integrations.fleet_plan_runtime_authority import (
    RuntimeAuthoritySources,
    expected_runtime_authority_sources,
)


@dataclass(frozen=True, slots=True)
class AtomicRuntimeFile:
    """One sealed descriptor containing the exact sandbox execution bytes."""

    descriptor: int
    sandbox_path: PurePosixPath
    identity: str
    byte_count: int
    mode: int
    kernel_device: int
    kernel_inode: int


@dataclass(slots=True)
class AtomicRuntimeAuthorityLease:
    """Lifetime owner for exact executable/interpreter descriptors.

    The receipt remains the only runtime-authority fact. These descriptors are merely
    the atomic handoff that makes the bytes named by that receipt the bytes exposed to
    the sandbox for kernel execution.
    """

    receipt: RuntimeAuthorityReceipt
    executable: AtomicRuntimeFile
    interpreter: AtomicRuntimeFile | None = None
    economics: ExecutionAuthorityEconomicsReceipt | None = None
    _closed: bool = False

    @property
    def pass_fds(self) -> tuple[int, ...]:
        values = [self.executable.descriptor]
        if self.interpreter is not None:
            values.append(self.interpreter.descriptor)
        return tuple(values)

    @property
    def bubblewrap_bindings(self) -> tuple[AtomicRuntimeFile, ...]:
        values = [self.executable]
        if self.interpreter is not None:
            values.append(self.interpreter)
        return tuple(values)

    def rewind_for_bubblewrap(self) -> None:
        """Position inherited sealed descriptors for Bubblewrap FD-data consumption."""
        self.verify_kernel_custody()
        for runtime_file in self.bubblewrap_bindings:
            os.lseek(runtime_file.descriptor, 0, os.SEEK_SET)

    def verify_kernel_custody(self) -> None:
        """Prove original sealed executable/interpreter FD objects survive handoff."""
        if self._closed:
            raise _divergence("atomic runtime kernel FD lease already closed")
        files = self.bubblewrap_bindings
        if len(set(self.pass_fds)) != len(files):
            raise _divergence("atomic runtime kernel FD descriptors overlap")
        if self.receipt.executable_identity != self.executable.identity:
            raise _divergence("atomic runtime executable FD not bound to original receipt")
        if self.receipt.interpreter_identity != (
            self.interpreter.identity if self.interpreter is not None else None
        ):
            raise _divergence("atomic runtime interpreter FD not bound to original receipt")
        for runtime_file in files:
            verify_sealed_execution_descriptor(
                SealedExecutionBytes(
                    descriptor=runtime_file.descriptor,
                    identity=runtime_file.identity,
                    byte_count=runtime_file.byte_count,
                    mode=runtime_file.mode,
                    kernel_device=runtime_file.kernel_device,
                    kernel_inode=runtime_file.kernel_inode,
                )
            )

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        for descriptor in self.pass_fds:
            try:
                os.close(descriptor)
            except OSError:
                pass

    def __enter__(self) -> AtomicRuntimeAuthorityLease:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()


def bind_atomic_runtime_authority(
    request: IsolatedProcessRequest,
    launch: SandboxProcessLaunch | None = None,
) -> AtomicRuntimeAuthorityLease:
    """Freeze reconciled launch bytes into sealed descriptors before sandbox spawn."""

    if not hasattr(os, "memfd_create") or not getattr(os, "MFD_ALLOW_SEALING", 0):
        raise _divergence("atomic executable binding requires Linux memfd sealing")

    launch = launch or sandbox_process_launch(request)
    sources = expected_runtime_authority_sources(request, launch)
    executable = _sealed_runtime_file(
        sources.executable_host_path,
        PurePosixPath(sources.receipt.executable_path),
        expected_identity=sources.receipt.executable_identity,
        label="runtime executable",
    )
    interpreter: AtomicRuntimeFile | None = None
    try:
        interpreter = _bind_interpreter(sources)
        terminal_bytes = executable.byte_count + (
            interpreter.byte_count if interpreter is not None else 0
        )
        return AtomicRuntimeAuthorityLease(
            receipt=sources.receipt,
            executable=executable,
            interpreter=interpreter,
            economics=(
                replace(sources.economics, terminal_proof_bytes=terminal_bytes)
                if sources.economics is not None
                else None
            ),
        )
    except BaseException:
        os.close(executable.descriptor)
        if interpreter is not None:
            os.close(interpreter.descriptor)
        raise


def _bind_interpreter(sources: RuntimeAuthoritySources) -> AtomicRuntimeFile | None:
    receipt = sources.receipt
    if receipt.interpreter_path is None:
        return None
    if sources.interpreter_host_path is None or receipt.interpreter_identity is None:
        raise _divergence("runtime interpreter source is missing from reconciled authority")
    return _sealed_runtime_file(
        sources.interpreter_host_path,
        PurePosixPath(receipt.interpreter_path),
        expected_identity=receipt.interpreter_identity,
        label="runtime interpreter",
    )


def _sealed_runtime_file(
    source: Path,
    sandbox_path: PurePosixPath,
    *,
    expected_identity: str,
    label: str,
) -> AtomicRuntimeFile:
    sealed = seal_execution_bytes(
        source,
        expected_identity=expected_identity,
        label=label,
    )
    return AtomicRuntimeFile(
        sealed.descriptor,
        sandbox_path,
        sealed.identity,
        sealed.byte_count,
        sealed.mode,
        sealed.kernel_device,
        sealed.kernel_inode,
    )


def _divergence(message: str) -> ExecutionAuthorityDivergence:
    return ExecutionAuthorityDivergence(message)
