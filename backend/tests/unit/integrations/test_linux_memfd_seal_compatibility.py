from __future__ import annotations

import hashlib
import os

import pytest

from oh_goon.engine import atomic_execution_bytes as atomic
from oh_goon.repository_tooling import opensandbox_attempt_runner as remote_runner


_SEAL_NAMES = (
    "F_ADD_SEALS",
    "F_GET_SEALS",
    "F_SEAL_WRITE",
    "F_SEAL_GROW",
    "F_SEAL_SHRINK",
    "F_SEAL_SEAL",
)


def _require_linux_memfd() -> None:
    if not hasattr(os, "memfd_create") or not getattr(os, "MFD_ALLOW_SEALING", 0):
        pytest.skip("Linux memfd sealing is unavailable")


def _hide_python_seal_names(monkeypatch: pytest.MonkeyPatch, module) -> None:
    for name in _SEAL_NAMES:
        monkeypatch.delattr(module.fcntl, name, raising=False)


def test_atomic_binding_uses_linux_uapi_when_python_omits_fcntl_seal_names(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _require_linux_memfd()
    source = tmp_path / "tool"
    source.write_bytes(b"#!/bin/sh\nexit 0\n")
    source.chmod(0o755)
    identity = "sha256:" + hashlib.sha256(source.read_bytes()).hexdigest()

    _hide_python_seal_names(monkeypatch, atomic)
    add, get, required = atomic._linux_seal_operations()
    assert (add, get, required) == (1033, 1034, 0x000F)

    sealed = atomic.seal_execution_bytes(
        source,
        expected_identity=identity,
        label="runtime executable",
    )
    try:
        assert sealed.identity == identity
    finally:
        os.close(sealed.descriptor)


def test_opensandbox_runner_uses_linux_uapi_when_python_omits_fcntl_seal_names(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _require_linux_memfd()
    _hide_python_seal_names(monkeypatch, remote_runner)

    descriptor = os.memfd_create("oh-goon-seal-regression", flags=os.MFD_ALLOW_SEALING)
    try:
        os.write(descriptor, b"sealed")
        remote_runner._seal_descriptor(descriptor, label="executable")
        applied = remote_runner.fcntl.fcntl(descriptor, 1034)
        assert applied & 0x000F == 0x000F
    finally:
        os.close(descriptor)
