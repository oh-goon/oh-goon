"""P1/B27B2B1: original atomic FD kernel identity at the actual launch handoff.

Development-level object custody only; does not certify namespace mounts or
post-teardown absence, and deliberately does not enable Agent Step effects.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path, PurePosixPath
from types import SimpleNamespace

import pytest

from oh_goon.core.errors import ExecutionAuthorityDivergence
from oh_goon.engine.atomic_execution_bytes import (
    SealedExecutionBytes,
    seal_execution_bytes,
    verify_sealed_execution_descriptor,
)
from oh_goon.integrations.fleet_plan_atomic_runtime_authority import (
    AtomicRuntimeAuthorityLease,
    AtomicRuntimeFile,
)
from oh_goon.repository_tooling.architecture.repository_layout import ROOT
from oh_goon.repository_tooling.architecture.x0_single_execution_contract import (
    _check_p1b27b2b1_kernel_fd_custody,
)

PACKAGE = ROOT / "backend/src/oh_goon"


def _sealed(path: Path, contents: bytes) -> SealedExecutionBytes:
    path.write_bytes(contents)
    path.chmod(0o755)
    return seal_execution_bytes(
        path,
        label="test-source",
        expected_identity=("sha256:" + hashlib.sha256(contents).hexdigest()),
    )


def _lease(fd: SealedExecutionBytes, interpreter: SealedExecutionBytes | None = None):
    def runtime_file(value: SealedExecutionBytes, target: str) -> AtomicRuntimeFile:
        return AtomicRuntimeFile(
            value.descriptor,
            PurePosixPath(target),
            value.identity,
            value.byte_count,
            value.mode,
            value.kernel_device,
            value.kernel_inode,
        )

    return AtomicRuntimeAuthorityLease(
        receipt=SimpleNamespace(
            executable_identity=fd.identity,
            interpreter_identity=interpreter.identity if interpreter else None,
        ),
        executable=runtime_file(fd, "/toolchain/bin/executable"),
        interpreter=runtime_file(interpreter, "/toolchain/bin/interpreter")
        if interpreter
        else None,
    )


def test_original_sealed_fd_remains_pinned_after_source_replacement(tmp_path):
    original = _sealed(tmp_path / "source", b"original bytes")
    try:
        with _lease(original) as lease:
            (tmp_path / "source").write_bytes(b"replaced path after sealing")
            lease.verify_kernel_custody()
            lease.rewind_for_bubblewrap()
            assert os.pread(original.descriptor, original.byte_count, 0) == b"original bytes"
            assert os.lseek(original.descriptor, 0, os.SEEK_CUR) == 0
    finally:
        # Lease is the sole FD owner; it has already closed this descriptor.
        pass


def test_foreign_equally_sealed_fd_cannot_replace_original_kernel_object(tmp_path):
    original = _sealed(tmp_path / "original", b"identical")
    foreign = _sealed(tmp_path / "foreign", b"identical")
    try:
        with _lease(original) as lease:
            # Equal hash + valid Linux seals is still the WRONG original inode.
            os.dup2(foreign.descriptor, original.descriptor)
            with pytest.raises(ExecutionAuthorityDivergence, match="kernel FD identity"):
                lease.verify_kernel_custody()
    finally:
        os.close(foreign.descriptor)


def test_unsealed_fd_replacement_fails_closed(tmp_path):
    original = _sealed(tmp_path / "original", b"payload")
    unsealed = os.memfd_create("foreign-unsealed")
    try:
        os.write(unsealed, b"payload")
        os.fchmod(unsealed, original.mode)
        with _lease(original) as lease:
            os.dup2(unsealed, original.descriptor)
            with pytest.raises(ExecutionAuthorityDivergence, match="kernel FD identity"):
                lease.rewind_for_bubblewrap()
    finally:
        os.close(unsealed)


def test_corrupted_identity_size_mode_or_inode_rejected(tmp_path):
    from dataclasses import replace

    sealed = _sealed(tmp_path / "file", b"sample")
    try:
        for variant in (
            replace(sealed, identity="sha256:" + "0" * 64),
            replace(sealed, byte_count=sealed.byte_count + 1),
            replace(sealed, mode=0o600),
            replace(sealed, kernel_inode=sealed.kernel_inode + 1),
        ):
            with pytest.raises(ExecutionAuthorityDivergence, match="kernel FD"):
                verify_sealed_execution_descriptor(variant)
    finally:
        os.close(sealed.descriptor)


def test_interpreter_is_independently_bound_and_receipt_fenced(tmp_path):
    executable = _sealed(tmp_path / "exec", b"exe")
    interpreter = _sealed(tmp_path / "interp", b"interp")
    with _lease(executable, interpreter) as lease:
        lease.verify_kernel_custody()
        lease.receipt.interpreter_identity = "sha256:" + "0" * 64
        with pytest.raises(ExecutionAuthorityDivergence, match="interpreter FD"):
            lease.verify_kernel_custody()
    with pytest.raises(ExecutionAuthorityDivergence, match="already closed"):
        lease.verify_kernel_custody()


def test_overlapping_file_descriptors_rejected(tmp_path):
    original = _sealed(tmp_path / "payload", b"sample")
    with _lease(original) as lease:
        lease.interpreter = AtomicRuntimeFile(
            original.descriptor,
            PurePosixPath("/interpreter"),
            original.identity,
            original.byte_count,
            original.mode,
            original.kernel_device,
            original.kernel_inode,
        )
        lease.receipt.interpreter_identity = original.identity
        with pytest.raises(ExecutionAuthorityDivergence, match="overlap"):
            lease.verify_kernel_custody()


def test_x0_exact_original_custody_call_graph():
    assert _check_p1b27b2b1_kernel_fd_custody(PACKAGE) == []


def test_canonical_wsl_qualification_enrolls_kernel_fd_custody():
    qualifier = (ROOT / "scripts/qualify_b12_development_wsl.sh").read_text()
    selector = "if [[ -f .checkpoint/P1B27B2B1_SOURCE.sha256 ]]; then embedded='.checkpoint/P1B27B2B1_SOURCE.sha256'; fi"
    assert qualifier.count(selector) == 1
    assert qualifier.index(selector) < qualifier.index(
        "if [[ -f .checkpoint/P1B27B2B2A_SOURCE.sha256 ]]"
    )
    assert "--require-module test_p1b27b2b1_kernel_fd_custody" in qualifier
    assert "observer_tests+=(tests/unit/runtime/test_p1b27b2b1_kernel_fd_custody.py)" in qualifier
    assert "uv run --locked --group dev" not in qualifier


@pytest.mark.parametrize(
    "mutation",
    [
        "remove-seals",
        "remove-inode",
        "remove-digest",
        "remove-prelaunch",
        "remove-rewind-check",
        "remove-preargv",
        "dead-branch",
    ],
)
def test_x0_rejects_kernel_fd_custody_drift(tmp_path, mutation):
    targets = {
        "engine/atomic_execution_bytes.py": PACKAGE / "engine/atomic_execution_bytes.py",
        "integrations/fleet_plan_atomic_runtime_authority.py": PACKAGE
        / "integrations/fleet_plan_atomic_runtime_authority.py",
        "integrations/fleet_plan_bubblewrap_isolation.py": PACKAGE
        / "integrations/fleet_plan_bubblewrap_isolation.py",
    }
    for name, source in targets.items():
        dest = tmp_path / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        contents = source.read_text()
        if mutation == "remove-seals" and name.startswith("engine/"):
            contents = contents.replace(
                "fcntl.fcntl(sealed.descriptor, get_seals)", "required_seals"
            )
        elif mutation == "remove-inode" and name.startswith("engine/"):
            contents = contents.replace(
                "(sealed.kernel_device, sealed.kernel_inode)", "(info.st_dev, info.st_ino)", 1
            )
        elif mutation == "remove-digest" and name.startswith("engine/"):
            contents = contents.replace(
                '"sha256:" + hasher.hexdigest() != sealed.identity', "False"
            )
        elif mutation == "remove-prelaunch" and name.endswith("fleet_plan_bubblewrap_isolation.py"):
            before = "                    inherited_fds = _require_original_native_fd_handoff("
            assert before in contents
            contents = contents.replace(before, "                    inherited_fds = tuple(", 1)
        elif mutation == "remove-rewind-check" and name.endswith(
            "fleet_plan_atomic_runtime_authority.py"
        ):
            contents = contents.replace(
                "        self.verify_kernel_custody()\n        for runtime_file",
                "        for runtime_file",
            )
        elif mutation == "remove-preargv" and name.endswith("fleet_plan_bubblewrap_isolation.py"):
            contents = contents.replace(
                "                    atomic_authority.verify_kernel_custody()\n                    argv, env",
                "                argv, env",
            )
        elif mutation == "dead-branch" and name.endswith("fleet_plan_bubblewrap_isolation.py"):
            before = "                    inherited_fds = _require_original_native_fd_handoff(\n                        argv, atomic_authority, mount_source_lease,\n                    )"
            assert before in contents
            contents = contents.replace(
                before,
                "                    if False:\n                        inherited_fds = _require_original_native_fd_handoff(\n                            argv, atomic_authority, mount_source_lease,\n                        )",
                1,
            )
        dest.write_text(contents)
    assert _check_p1b27b2b1_kernel_fd_custody(tmp_path)
