"""SKILL-3C2D2C2C4B: local LACE FD release, explicitly not physical teardown."""

from __future__ import annotations

import errno
import os
from pathlib import Path

import pytest

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.integrations import fleet_plan_bind_source_custody as custody
from oh_goon.integrations.fleet_plan_bind_source_custody import OriginalBindSourceLease
from oh_goon.repository_tooling.architecture.repository_layout import ROOT
from tests.unit.runtime.test_p1b27b2b2a_pinned_bind_source import _argv, _prepared


def test_verified_local_fd_release_after_native_handoff(tmp_path: Path):
    request = _prepared(tmp_path)
    with OriginalBindSourceLease.pin_prepared(request) as lease:
        argv = _argv(lease)
        lease.consume_validated_argv(argv)
        fds = lease.pass_fds
        assert fds
        lease.release_pins_after_process()
        assert all(_is_closed(fd) for fd in fds)
        with pytest.raises(FleetPlanInvalid, match="live owned pins"):
            lease.release_pins_after_process()
        with pytest.raises(FleetPlanInvalid, match="closed"):
            lease.verify_kernel_custody()


def _is_closed(fd: int) -> bool:
    try:
        os.fstat(fd)
    except OSError as exc:
        return exc.errno == errno.EBADF
    return False


def test_altered_kernel_pin_still_closes_entire_roster_but_denies_success(tmp_path: Path):
    with OriginalBindSourceLease.pin_prepared(_prepared(tmp_path)) as lease:
        fd = lease.pass_fds[0]
        other = tmp_path / "changed"
        other.mkdir()
        replacement = custody._open_absolute_no_symlinks(str(other))
        try:
            os.dup2(replacement, fd)
            with pytest.raises(FleetPlanInvalid, match="release not verified"):
                lease.release_pins_after_process()
            assert all(_is_closed(pin.fd) for pin in lease.pins)
        finally:
            os.close(replacement)


def test_failed_native_fd_close_cannot_be_reported_as_release(monkeypatch, tmp_path: Path):
    with OriginalBindSourceLease.pin_prepared(_prepared(tmp_path)) as lease:
        victim = lease.pass_fds[0]
        close = custody.os.close
        raised = False

        def refuse_once(fd: int) -> None:
            nonlocal raised
            if fd == victim and not raised:
                raised = True
                raise OSError(errno.EIO, "injected native close failure")
            close(fd)

        monkeypatch.setattr(custody.os, "close", refuse_once)
        try:
            with pytest.raises(FleetPlanInvalid, match="release not verified"):
                lease.release_pins_after_process()
            assert raised
            assert all(_is_closed(pin.fd) for pin in lease.pins[1:])
        finally:
            if not _is_closed(victim):
                close(victim)


def test_prelaunch_exception_cleanup_cannot_be_upgraded_to_verified_release(tmp_path: Path):
    lease = OriginalBindSourceLease.pin_prepared(_prepared(tmp_path))
    fds = lease.pass_fds
    lease.close()
    assert all(_is_closed(fd) for fd in fds)
    with pytest.raises(FleetPlanInvalid, match="live owned pins"):
        lease.release_pins_after_process()


def test_original_synchronous_native_owner_consumes_release_before_return():
    source = (
        ROOT / "backend/src/oh_goon/integrations/fleet_plan_bubblewrap_isolation.py"
    ).read_text()
    process = source.index("                    result = run_command(")
    release = source.index(
        "                    mount_source_lease.release_pins_after_process()", process
    )
    returned = source.index("                    return replace(", process)
    assert process < release < returned
    assert source.count("mount_source_lease.release_pins_after_process()") == 1


def test_reused_fd_number_at_local_close_is_not_a_release_observation(monkeypatch, tmp_path: Path):
    with OriginalBindSourceLease.pin_prepared(_prepared(tmp_path)) as lease:
        victim = lease.pass_fds[0]
        close = custody.os.close
        reused = False

        def close_and_reuse(fd: int) -> None:
            nonlocal reused
            close(fd)
            if fd == victim and not reused:
                reused = True
                other = os.open("/dev/null", os.O_RDONLY | os.O_CLOEXEC)
                if other != victim:
                    os.dup2(other, victim)
                    close(other)

        monkeypatch.setattr(custody.os, "close", close_and_reuse)
        try:
            with pytest.raises(FleetPlanInvalid, match="release not verified"):
                lease.release_pins_after_process()
            assert reused
        finally:
            if not _is_closed(victim):
                close(victim)
