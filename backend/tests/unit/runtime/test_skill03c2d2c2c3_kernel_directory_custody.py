"""Original Fleet/LACE optional Linux immutable directory-entry custody.

Mock ioctls test protocol only; they do NOT provide native kernel evidence.
"""

from __future__ import annotations

import errno
import os
import struct
from contextlib import contextmanager

import pytest

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.integrations import fleet_plan_agent_dir_immutable as imm
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


@contextmanager
def _directory_fd(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        yield fd
    finally:
        os.close(fd)


def _kernel_flags(monkeypatch):
    by_inode: dict[tuple[int, int], int] = {}
    calls: list[tuple[int, int]] = []

    def ioctl(fd, operation, argument, mutate=False):
        st = os.fstat(fd)
        inode = (st.st_dev, st.st_ino)
        calls.append((operation, st.st_ino))
        if operation == imm._FS_IOC_GETFLAGS:
            assert mutate is True
            assert len(argument) == struct.calcsize("@l")
            argument[:4] = struct.pack("=I", by_inode.get(inode, 0x20))
        elif operation == imm._FS_IOC_SETFLAGS:
            flags = struct.unpack("@l", argument)[0]
            assert len(argument) == struct.calcsize("@l")
            assert flags & 0x20  # unrelated existing inode flags are preserved
            by_inode[inode] = flags
        else:
            raise AssertionError(f"Unexpected immutable-directory ioctl: {operation}")
        return 0

    monkeypatch.setattr(imm.fcntl, "ioctl", ioctl)
    return by_inode, calls


def test_original_linux_immutable_directory_preserves_existing_flags_and_remeasures(
    monkeypatch, tmp_path
):
    values, calls = _kernel_flags(monkeypatch)
    with _directory_fd(tmp_path) as fd:
        with pytest.raises(FleetPlanInvalid, match="lacks kernel immutable"):
            imm.require_original_agent_directory_immutable(fd)
        imm.enable_original_agent_directory_immutable(fd)
        imm.require_original_agent_directory_immutable(fd)
        imm.enable_original_agent_directory_immutable(fd)
        assert values[(os.fstat(fd).st_dev, os.fstat(fd).st_ino)] == 0x30
        assert [op for op, _ in calls].count(imm._FS_IOC_SETFLAGS) == 1


@pytest.mark.parametrize("errno_value", [errno.EPERM, errno.EOPNOTSUPP, errno.ENOTTY, errno.EINVAL])
def test_unsupported_kernel_directory_immutability_fails_closed(monkeypatch, tmp_path, errno_value):
    def deny(*args, **kwargs):
        raise OSError(errno_value, "denied")

    monkeypatch.setattr(imm.fcntl, "ioctl", deny)
    with _directory_fd(tmp_path) as fd:
        with pytest.raises(FleetPlanInvalid, match="flags unavailable"):
            imm.require_original_agent_directory_immutable(fd)
        with pytest.raises(FleetPlanInvalid, match="flags unavailable"):
            imm.enable_original_agent_directory_immutable(fd)


def test_non_directory_refused_even_if_kernel_mocked(tmp_path):
    p = tmp_path / "file"
    p.write_text("data")
    with p.open("rb") as f, pytest.raises(FleetPlanInvalid, match="requires a directory"):
        imm.enable_original_agent_directory_immutable(f.fileno())


def test_stager_requires_file_verity_and_directory_custody_before_work(tmp_path):
    handoff = original_handoff(tmp_path)
    owner = attempt(tmp_path, handoff)
    with pytest.raises(FleetPlanInvalid, match="also requires file fs-verity"):
        owner._stage_agent_prework_bytes(handoff, require_kernel_directory_immutable=True)
    assert not (owner.paths.root / "agent-source-prework").exists()


def test_stager_seals_directories_bottom_up_without_grant(monkeypatch, tmp_path):
    handoff = original_handoff(tmp_path)
    owner = attempt(tmp_path, handoff)
    values, calls = _kernel_flags(monkeypatch)
    monkeypatch.setattr(verity, "enable_original_agent_file_verity", lambda fd: b"V" * 32)
    stage = owner._stage_agent_prework_bytes(
        handoff,
        require_kernel_file_verity=True,
        require_kernel_directory_immutable=True,
    )
    from pathlib import Path

    stage_dir = Path(owner.paths.root) / "agent-source-prework"
    dirs = [stage_dir, *(p for p in stage_dir.rglob("*") if p.is_dir())]
    assert len(dirs) >= 3
    assert all(values[(p.stat().st_dev, p.stat().st_ino)] & imm._FS_IMMUTABLE_FL for p in dirs)
    assert len([op for op, _ in calls if op == imm._FS_IOC_SETFLAGS]) == len(dirs)
    assert stage["effect_permission"] == "NONE"
    assert stage["physical_sandbox_proven"] is False


def test_stager_refusal_never_returns_reusable_agent_receipt(monkeypatch, tmp_path):
    handoff = original_handoff(tmp_path)
    owner = attempt(tmp_path, handoff)
    monkeypatch.setattr(verity, "enable_original_agent_file_verity", lambda fd: b"V" * 32)
    monkeypatch.setattr(
        imm,
        "enable_original_agent_directory_immutable",
        lambda fd: (_ for _ in ()).throw(FleetPlanInvalid("kernel denied")),
    )
    with pytest.raises(FleetPlanInvalid, match="kernel denied"):
        owner._stage_agent_prework_bytes(
            handoff,
            require_kernel_file_verity=True,
            require_kernel_directory_immutable=True,
        )
    with pytest.raises(FleetPlanInvalid, match="failed closed"):
        owner._stage_agent_prework_bytes(handoff)


def test_lace_requires_both_file_and_directory_custody_before_fd_pin(tmp_path):
    request = _request_with_native_sources(tmp_path)
    with pytest.raises(FleetPlanInvalid, match="also requires file fs-verity"):
        OriginalBindSourceLease.pin_prepared(request, require_agent_kernel_directory_immutable=True)


def test_lace_measures_each_directory_at_pin_and_before_argv(monkeypatch, tmp_path):
    request = _request_with_native_sources(tmp_path)
    values, calls = _kernel_flags(monkeypatch)
    monkeypatch.setattr(verity, "measure_original_agent_file_verity", lambda fd: b"V" * 32)
    # Simulate pre-existing kernel seals on already staged immutable descendants.
    for base, dirs, _ in os.walk(request.agent_source_mount.source):
        for path in [base, *(os.path.join(base, d) for d in dirs)]:
            st = os.stat(path)
            values[(st.st_dev, st.st_ino)] = imm._FS_IMMUTABLE_FL
    with OriginalBindSourceLease.pin_prepared(
        request,
        require_agent_kernel_file_verity=True,
        require_agent_kernel_directory_immutable=True,
    ) as lease:
        seen = len(calls)
        assert seen >= 3
        assert next(
            p for p in lease.pins if p.target == "/opt/oh-goon/agent-source"
        ).kernel_directory_immutable_required
        lease.verify_kernel_custody()
        assert len(calls) > seen
        args = [
            "bwrap",
            *[v for p in lease.pins for v in (p.option, p.source, p.target)],
            "--",
            "/bin/true",
        ]
        lease.consume_validated_argv(args)
        assert len(calls) > seen * 2
        # The original descriptor stays bound to the same filesystem inode;
        # losing one directory's kernel protection invalidates the lease.
        st = os.stat(os.path.join(request.agent_source_mount.source, "repository"))
        values[(st.st_dev, st.st_ino)] = 0x20
        with pytest.raises(FleetPlanInvalid, match="lacks kernel immutable"):
            lease.verify_kernel_custody()


def test_lease_missing_directory_immutability_refused_even_when_files_verified(
    monkeypatch, tmp_path
):
    request = _request_with_native_sources(tmp_path)
    monkeypatch.setattr(verity, "measure_original_agent_file_verity", lambda fd: b"V" * 32)
    _kernel_flags(monkeypatch)  # unprotected directories
    with pytest.raises(FleetPlanInvalid, match="lacks kernel immutable"):
        OriginalBindSourceLease.pin_prepared(
            request,
            require_agent_kernel_file_verity=True,
            require_agent_kernel_directory_immutable=True,
        )
    with OriginalBindSourceLease.pin_prepared(request) as lease:
        lease.verify_kernel_custody()  # legacy nonexecutable staging is compatible
