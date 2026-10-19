"""Original prework rollback: never a post-admission teardown or Agent grant."""

from __future__ import annotations

import errno
import os

import pytest

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.integrations import fleet_plan_agent_dir_immutable as immutable
from oh_goon.integrations import fleet_plan_agent_fsverity as verity
from tests.unit.runtime.test_ag02b2b3_original_agent_attempt_staging import (
    attempt,
    original_handoff,
)
from tests.unit.runtime.test_skill03c2d2c2c3_kernel_directory_custody import _kernel_flags


def _stage(tmp_path):
    stage = tmp_path / "agent-source-prework"
    (stage / "repository").mkdir(parents=True)
    (stage / "repository" / "source.txt").write_text("immutable bytes")
    (stage / "repository" / "source.txt").chmod(0o400)
    (stage / "repository").chmod(0o500)
    stage.chmod(0o500)
    return stage


def test_original_rollback_clears_only_directory_flags_and_does_not_delete(monkeypatch, tmp_path):
    stage = _stage(tmp_path)
    values, calls = _kernel_flags(monkeypatch)
    before = {str(p): (p.stat().st_dev, p.stat().st_ino) for p in (stage, stage / "repository")}
    for path in (stage, stage / "repository"):
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
        try:
            immutable.enable_original_agent_directory_immutable(fd)
        finally:
            os.close(fd)
    fd = os.open(stage, os.O_RDONLY | os.O_DIRECTORY)
    try:
        immutable.rollback_unpublished_agent_immutable_stage(fd)
    finally:
        os.close(fd)
    for path in (stage, stage / "repository"):
        assert (path.stat().st_dev, path.stat().st_ino) == before[str(path)]
        assert values[before[str(path)]] == 0x20
    assert (stage / "repository" / "source.txt").read_text() == "immutable bytes"
    assert len([1 for op, _ in calls if op == immutable._FS_IOC_SETFLAGS]) == 4


def test_refuse_unexpected_symlink_before_unsealing_anything(monkeypatch, tmp_path):
    stage = _stage(tmp_path)
    (stage / "repository").chmod(0o700)
    (stage / "repository" / "escape").symlink_to("/tmp")
    (stage / "repository").chmod(0o500)
    values, _ = _kernel_flags(monkeypatch)
    for path in (stage, stage / "repository"):
        values[(path.stat().st_dev, path.stat().st_ino)] = immutable._FS_IMMUTABLE_FL
    fd = os.open(stage, os.O_RDONLY | os.O_DIRECTORY)
    try:
        with pytest.raises(FleetPlanInvalid, match="Unsafe Agent staged file"):
            immutable.rollback_unpublished_agent_immutable_stage(fd)
    finally:
        os.close(fd)
    assert all(value & immutable._FS_IMMUTABLE_FL for value in values.values())


def test_refuse_unsupported_unseal_without_claiming_cleanup(monkeypatch, tmp_path):
    stage = _stage(tmp_path)
    values, _ = _kernel_flags(monkeypatch)
    for path in (stage, stage / "repository"):
        values[(path.stat().st_dev, path.stat().st_ino)] = immutable._FS_IMMUTABLE_FL
    good_ioctl = immutable.fcntl.ioctl

    def denied_unseal(fd, operation, argument, mutate=False):
        if operation == immutable._FS_IOC_SETFLAGS:
            raise OSError(errno.EPERM, "privilege revoked")
        return good_ioctl(fd, operation, argument, mutate)

    monkeypatch.setattr(immutable.fcntl, "ioctl", denied_unseal)
    fd = os.open(stage, os.O_RDONLY | os.O_DIRECTORY)
    try:
        with pytest.raises(FleetPlanInvalid, match="rollback unavailable"):
            immutable.rollback_unpublished_agent_immutable_stage(fd)
    finally:
        os.close(fd)
    assert stage.exists()
    assert any(value & immutable._FS_IMMUTABLE_FL for value in values.values())


def test_failed_prework_seal_rolls_back_only_owned_unpublished_stage(monkeypatch, tmp_path):
    handoff = original_handoff(tmp_path)
    owner = attempt(tmp_path, handoff)
    values, calls = _kernel_flags(monkeypatch)
    monkeypatch.setattr(verity, "enable_original_agent_file_verity", lambda fd: b"V" * 32)
    real_enable = immutable.enable_original_agent_directory_immutable
    enabled_count = 0

    def fail_after_one(fd):
        nonlocal enabled_count
        enabled_count += 1
        if enabled_count == 2:
            raise FleetPlanInvalid("simulated kernel failure after partial immutable stage")
        real_enable(fd)

    monkeypatch.setattr(immutable, "enable_original_agent_directory_immutable", fail_after_one)
    with pytest.raises(FleetPlanInvalid, match="simulated kernel failure"):
        owner._stage_agent_prework_bytes(
            handoff,
            require_kernel_file_verity=True,
            require_kernel_directory_immutable=True,
        )
    assert enabled_count == 2
    stage = owner.paths.root / "agent-source-prework"
    assert stage.is_dir()  # never destructively reused or deleted
    assert all(not value & immutable._FS_IMMUTABLE_FL for value in values.values())
    assert any(op == immutable._FS_IOC_SETFLAGS for op, _ in calls)
    with pytest.raises(FleetPlanInvalid, match="failed closed"):
        owner._stage_agent_prework_bytes(handoff)


def test_unseal_requires_private_ownership_before_any_kernel_operation(monkeypatch, tmp_path):
    stage = _stage(tmp_path)
    stage.chmod(0o755)
    _, calls = _kernel_flags(monkeypatch)
    fd = os.open(stage, os.O_RDONLY | os.O_DIRECTORY)
    try:
        with pytest.raises(FleetPlanInvalid, match="not privately owned"):
            immutable.rollback_unpublished_agent_immutable_stage(fd)
    finally:
        os.close(fd)
    assert calls == []


def test_stager_exposes_failed_rollback_as_recovery_obligation(monkeypatch, tmp_path):
    handoff = original_handoff(tmp_path)
    owner = attempt(tmp_path, handoff)
    _kernel_flags(monkeypatch)
    monkeypatch.setattr(verity, "enable_original_agent_file_verity", lambda fd: b"V" * 32)
    real_enable = immutable.enable_original_agent_directory_immutable
    real_disable = immutable.disable_original_agent_directory_immutable
    count = 0

    def interrupted_seal(fd):
        nonlocal count
        count += 1
        if count == 2:
            raise FleetPlanInvalid("stage interrupted")
        real_enable(fd)

    def denied_rollback(fd):
        raise FleetPlanInvalid("CAP_LINUX_IMMUTABLE lost during rollback")

    monkeypatch.setattr(immutable, "enable_original_agent_directory_immutable", interrupted_seal)
    monkeypatch.setattr(immutable, "disable_original_agent_directory_immutable", denied_rollback)
    with pytest.raises(FleetPlanInvalid, match="privileged recovery required"):
        owner._stage_agent_prework_bytes(
            handoff,
            require_kernel_file_verity=True,
            require_kernel_directory_immutable=True,
        )
    assert count == 2
    assert (owner.paths.root / "agent-source-prework").exists()
    monkeypatch.setattr(immutable, "disable_original_agent_directory_immutable", real_disable)
    with pytest.raises(FleetPlanInvalid, match="failed closed"):
        owner._stage_agent_prework_bytes(handoff)
