"""SKILL-3C2D2C2C4B: local LACE FD release, explicitly not physical teardown."""

from __future__ import annotations

import ast
import errno
import inspect
import os
from pathlib import Path
from textwrap import dedent

import pytest

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.engine.command import run_command
from oh_goon.integrations import fleet_plan_bind_source_custody as custody
from oh_goon.integrations.fleet_plan_bind_source_custody import OriginalBindSourceLease
from oh_goon.integrations.fleet_plan_bubblewrap_isolation import BubblewrapProcessIsolation
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
    # This is a governed strict-owner ordering contract. Ordinary execution in
    # another method must not participate in the assertion, and local binding
    # names and source formatting are not execution authority.
    execute = BubblewrapProcessIsolation.execute
    owner = ast.parse(dedent(inspect.getsource(execute)))
    bindings = execute.__globals__
    contexts = [
        (node, item)
        for node in ast.walk(owner)
        if isinstance(node, ast.With)
        for item in node.items
        if isinstance(item.context_expr, ast.Call)
        and isinstance(item.context_expr.func, ast.Attribute)
        and isinstance(item.context_expr.func.value, ast.Name)
        and bindings.get(item.context_expr.func.value.id) is OriginalBindSourceLease
        and item.context_expr.func.attr == "pin_prepared"
    ]
    assert len(contexts) == 1
    scope, binding = contexts[0]
    assert isinstance(binding.optional_vars, ast.Name)
    lease_name = binding.optional_vars.id
    launches = [
        node
        for node in ast.walk(scope)
        if isinstance(node, ast.Assign)
        and isinstance(node.value, ast.Call)
        and isinstance(node.value.func, ast.Name)
        and bindings.get(node.value.func.id) is run_command
    ]
    assert len(launches) == 1
    launch = launches[0]
    blocks = [
        statements
        for node in ast.walk(scope)
        for _, statements in ast.iter_fields(node)
        if isinstance(statements, list) and any(statement is launch for statement in statements)
    ]
    assert len(blocks) == 1
    statements = blocks[0]
    release_calls = [
        node
        for node in ast.walk(scope)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == lease_name
        and node.func.attr == "release_pins_after_process"
    ]
    assert len(release_calls) == 1
    releases = [
        index
        for index, statement in enumerate(statements)
        if isinstance(statement, ast.Expr) and statement.value is release_calls[0]
    ]
    assert len(releases) == 1, "The original lease must release unconditionally after native return"
    release = releases[0]
    process = statements.index(launch)
    assert process < release
    assert not any(
        isinstance(node, ast.Return)
        for statement in statements[process:release]
        for node in ast.walk(statement)
    ), "A successful native result cannot return before the original FD release"
    assert any(
        isinstance(node, ast.Return)
        for statement in statements[release + 1 :]
        for node in ast.walk(statement)
    )


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
