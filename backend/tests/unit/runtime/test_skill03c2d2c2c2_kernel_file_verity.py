"""SKILL-3C2D2C2C2: Linux file-byte verity, NOT directory or Agent execution custody."""

from __future__ import annotations

import errno
import fcntl
import os
import struct
from pathlib import Path
from types import SimpleNamespace
from typing import Literal

import pytest

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.integrations import fleet_plan_agent_fsverity as verity
from oh_goon.integrations.fleet_plan_bind_source_custody import OriginalBindSourceLease
from tests.factories.fleet_plan_executor import agent_staging_scratch as agent_staging_scratch
from tests.unit.runtime.test_ag02b2b3_original_agent_attempt_staging import (
    attempt,
    original_handoff,
)
from tests.unit.runtime.test_skill03c2d2c2b_staged_descendant_seal import (
    _request_with_native_sources,
)

pytestmark = pytest.mark.usefixtures("agent_staging_scratch")


@pytest.mark.parametrize(
    ("page_size", "filesystem_block_size", "expected_block_size"),
    [(4096, 4096, 4096), (65536, 4096, 4096), (4096, 1024, 1024), (4096, 65536, 4096)],
)
def test_original_linux_fsverity_ioctl_abi_and_kernel_digest(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    page_size: int,
    filesystem_block_size: int,
    expected_block_size: int,
) -> None:
    member = tmp_path / "member"
    member.write_bytes(b"signed bytes")
    commands = []

    def native_page_size(name: str) -> int:
        assert name == "SC_PAGESIZE"
        return page_size

    def native_filesystem(fd: int) -> SimpleNamespace:
        assert os.fstat(fd).st_ino == member.stat().st_ino
        return SimpleNamespace(f_bsize=filesystem_block_size)

    def fake_ioctl(fd, cmd, arg, mutate=False):
        commands.append(cmd)
        if cmd == 0x40806685:
            assert len(arg) == 128
            assert struct.unpack_from("=III", arg) == (1, 1, expected_block_size)
            assert not any(arg[12:])
        elif cmd == 0xC0046686:
            assert mutate is True
            assert struct.unpack_from("=HH", arg) == (1, 32)
            arg[4:36] = b"A" * 32
        else:
            raise AssertionError("Unexpected kernel ioctl")

    monkeypatch.setattr(os, "sysconf", native_page_size)
    monkeypatch.setattr(os, "fstatvfs", native_filesystem)
    monkeypatch.setattr(verity.fcntl, "ioctl", fake_ioctl)
    with member.open("rb") as stream:
        assert verity.enable_original_agent_file_verity(stream.fileno()) == b"A" * 32
    assert commands == [0x40806685, 0xC0046686]


@pytest.mark.parametrize("error", [errno.ENOTTY, errno.EOPNOTSUPP, errno.EPERM, errno.EINVAL])
def test_unsupported_or_unauthorized_kernel_custody_never_downgrades(monkeypatch, tmp_path, error):
    member = tmp_path / "member"
    member.write_bytes(b"staged source")

    def deny(*args, **kwargs):
        raise OSError(error, "unavailable")

    monkeypatch.setattr(verity.fcntl, "ioctl", deny)
    with member.open("rb") as stream:
        with pytest.raises(FleetPlanInvalid, match="unavailable") as enable_error:
            verity.enable_original_agent_file_verity(stream.fileno())
        assert isinstance(enable_error.value.__cause__, OSError)
        assert enable_error.value.__cause__.errno == error
        with pytest.raises(FleetPlanInvalid, match="lacks kernel") as measure_error:
            verity.measure_original_agent_file_verity(stream.fileno())
        assert isinstance(measure_error.value.__cause__, OSError)
        assert measure_error.value.__cause__.errno == error


def test_wrong_kernel_digest_algorithm_is_rejected(monkeypatch, tmp_path):
    p = tmp_path / "stage"
    p.write_bytes(b"bytes")

    def wrong_digest(fd, cmd, arg, mutate=False):
        arg[:4] = struct.pack("=HH", 2, 32)

    monkeypatch.setattr(verity.fcntl, "ioctl", wrong_digest)
    with p.open("rb") as fd:
        with pytest.raises(FleetPlanInvalid, match="unsupported fs-verity"):
            verity.measure_original_agent_file_verity(fd.fileno())


def test_original_stager_requires_kernel_verity_explicitly_and_does_not_mint_permission(
    monkeypatch, tmp_path
):
    handoff = original_handoff(tmp_path)
    owner = attempt(tmp_path, handoff)
    calls = []

    def record(fd):
        calls.append(os.fstat(fd).st_ino)
        return b"D" * 32

    monkeypatch.setattr(verity, "enable_original_agent_file_verity", record)
    stage = owner._stage_agent_prework_bytes(handoff, require_kernel_file_verity=True)
    assert len(calls) == stage["file_count"]
    assert stage["effect_permission"] == "NONE"
    assert stage["physical_sandbox_proven"] is False
    assert owner._inspect_agent_prework_readonly_mount(handoff, stage).target == (
        "/opt/oh-goon/agent-source"
    )


def test_fail_closed_stager_has_no_reusable_receipt_on_kernel_refusal(monkeypatch, tmp_path):
    handoff = original_handoff(tmp_path)
    owner = attempt(tmp_path, handoff)

    def deny(_fd):
        raise FleetPlanInvalid("no kernel fs-verity")

    monkeypatch.setattr(verity, "enable_original_agent_file_verity", deny)
    with pytest.raises(FleetPlanInvalid, match="no kernel fs-verity"):
        owner._stage_agent_prework_bytes(handoff, require_kernel_file_verity=True)
    with pytest.raises(FleetPlanInvalid, match="failed closed"):
        owner._stage_agent_prework_bytes(handoff)


def test_original_fd_lease_explicit_verity_mode_measures_at_pin_and_each_consumption(
    monkeypatch, tmp_path
):
    request = _request_with_native_sources(tmp_path)
    calls = []

    def digest(fd):
        calls.append(os.fstat(fd).st_ino)
        return b"V" * 32

    monkeypatch.setattr(verity, "measure_original_agent_file_verity", digest)
    with OriginalBindSourceLease.pin_prepared(
        request,
        require_agent_kernel_file_verity=True,
    ) as lease:
        initial = len(calls)
        assert initial >= 2
        assert any(source.kernel_file_verity_required for source in lease.pins)
        lease.verify_kernel_custody()
        assert len(calls) > initial
        before = len(calls)
        lease.verify_kernel_custody()
        assert len(calls) > before


def test_verity_lease_refuses_any_unverified_descendant_at_pin(monkeypatch, tmp_path):
    request = _request_with_native_sources(tmp_path)

    def deny(_fd):
        raise FleetPlanInvalid("Agent staged file lacks kernel fs-verity custody")

    monkeypatch.setattr(verity, "measure_original_agent_file_verity", deny)
    with pytest.raises(FleetPlanInvalid, match="lacks kernel"):
        OriginalBindSourceLease.pin_prepared(
            request,
            require_agent_kernel_file_verity=True,
        )
    # Default, existing no-effect prework remains backward compatible.
    with OriginalBindSourceLease.pin_prepared(request) as lease:
        lease.verify_kernel_custody()


@pytest.mark.certification
@pytest.mark.skipif(__import__("sys").platform != "linux", reason="Linux UAPI only")
def test_native_filesystem_verity_is_kernel_enforced_when_supported(tmp_path, monkeypatch):
    """Physical POSITIVE witness only on a filesystem that truly supports it.

    EOPNOTSUPP/ENOTTY is a SKIP, never a simulated kernel PASS.
    Directory replacement remains an explicitly separate open obligation.
    """
    member = tmp_path / "kernel-source"
    member.write_bytes(b"read-only kernel protected source")
    native_ioctl = fcntl.ioctl
    enabled_by_kernel = False

    def observe_ioctl(
        fd: int, request: int, arg: bytes | bytearray, *mutate_flags: Literal[True]
    ) -> int | bytes:
        nonlocal enabled_by_kernel
        result: int | bytes
        if isinstance(arg, bytes):
            result = native_ioctl(fd, request, arg, *mutate_flags)
        else:
            result = native_ioctl(fd, request, arg, *mutate_flags)
        if request == 0x40806685:
            enabled_by_kernel = True
        return result

    # Delegate the real ioctl unchanged and record when the kernel accepts
    # enable. Measurement failures after that point must fail, never skip.
    monkeypatch.setattr(fcntl, "ioctl", observe_ioctl)
    try:
        with member.open("rb") as src:
            enabled = verity.enable_original_agent_file_verity(src.fileno())
    except FleetPlanInvalid as exc:
        cause = exc.__cause__
        if (
            enabled_by_kernel
            or not isinstance(cause, OSError)
            or cause.errno not in {errno.EOPNOTSUPP, errno.ENOTTY}
        ):
            raise
        pytest.skip(f"native fs-verity not available on this mounted filesystem: {exc}")
    assert enabled_by_kernel
    with member.open("rb") as src:
        assert enabled == verity.measure_original_agent_file_verity(src.fileno())
    with pytest.raises(OSError):
        member.write_bytes(b"hostile content")
    with member.open("rb") as src:
        assert verity.measure_original_agent_file_verity(src.fileno()) == enabled
