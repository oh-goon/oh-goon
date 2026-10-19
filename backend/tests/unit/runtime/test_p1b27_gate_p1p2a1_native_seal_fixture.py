"""Regression: real Bubblewrap witness must use the original Linux UAPI fallback.

The B3B native WSL witness (2026-10-14) failed before bwrap launched because
CPython 3.14.7 did not expose fcntl.F_ADD_SEALS. Keep one production-defined
seal operation contract, even for mechanical test fixture creation.
"""

from __future__ import annotations

import fcntl
import os

import pytest

from oh_goon.engine.atomic_execution_bytes import _linux_seal_operations
from tests.native.test_p1b27b2b3b2b3a_effectful_mount import _AtomicMechanicalFixture


@pytest.mark.skipif(not hasattr(os, "memfd_create"), reason="Linux memfd required")
def test_effectful_witness_seals_with_missing_python_uapi_names(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for attr in (
        "F_ADD_SEALS",
        "F_GET_SEALS",
        "F_SEAL_WRITE",
        "F_SEAL_GROW",
        "F_SEAL_SHRINK",
        "F_SEAL_SEAL",
    ):
        monkeypatch.delattr(fcntl, attr, raising=False)
    add, get, required = _linux_seal_operations()
    assert (add, get, required) == (1033, 1034, 0x000F)
    fd = os.memfd_create("b27-fixture-seal-regression", os.MFD_CLOEXEC | os.MFD_ALLOW_SEALING)
    try:
        os.write(fd, b"sealed-FD-data-v1\n")
        fcntl.fcntl(fd, add, required)
        fixture = _AtomicMechanicalFixture(fd)
        fixture.verify_kernel_custody()
        fixture.rewind_for_bubblewrap()
        assert os.read(fd, 128) == b"sealed-FD-data-v1\n"
    finally:
        os.close(fd)


@pytest.mark.skipif(not hasattr(os, "memfd_create"), reason="Linux memfd required")
def test_effectful_witness_rejects_unsealed_descriptor(monkeypatch: pytest.MonkeyPatch) -> None:
    for attr in ("F_GET_SEALS", "F_SEAL_WRITE", "F_SEAL_GROW", "F_SEAL_SHRINK", "F_SEAL_SEAL"):
        monkeypatch.delattr(fcntl, attr, raising=False)
    fd = os.memfd_create("b27-fixture-unsealed-regression", os.MFD_CLOEXEC | os.MFD_ALLOW_SEALING)
    try:
        os.write(fd, b"not-sealed")
        with pytest.raises(AssertionError):
            _AtomicMechanicalFixture(fd).verify_kernel_custody()
    finally:
        os.close(fd)
