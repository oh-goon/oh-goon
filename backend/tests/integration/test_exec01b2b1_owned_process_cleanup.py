"""Standard Linux/WSL process cleanup with real subprocesses, no Hashmarks substitute.

The existing command supervisor owns process groups; this is functional evidence,
not a host isolation or high-security certification gate.
"""
from __future__ import annotations

import os
import time

import pytest

from oh_goon.engine.command import CommandCancelledError, CommandTimeoutExpired, run_command

pytestmark = pytest.mark.skipif(os.name != "posix", reason="Linux/WSL process-group semantics")


def test_real_host_deadline_terminates_owned_shell_and_child():
    started = time.monotonic()
    with pytest.raises(CommandTimeoutExpired) as info:
        run_command("sh", "-c", "sleep 30 & wait", timeout=0.25,
                    require_process_group_quiescence=True)
    receipt = info.value.supervisor
    assert receipt.process_group_cleanup_requested
    assert receipt.cleanup_reason == "timeout"
    assert receipt.termination_receipt is not None
    assert receipt.termination_receipt.term_requested
    assert receipt.final_process_group_quiescent
    assert time.monotonic() - started < 6


def test_real_host_cancel_terminates_owned_shell_and_child():
    started = time.monotonic()
    with pytest.raises(CommandCancelledError) as info:
        run_command("sh", "-c", "sleep 30 & wait", timeout=None,
                    cancel_requested=lambda: time.monotonic() - started >= 0.25,
                    require_process_group_quiescence=True)
    receipt = info.value.supervisor
    assert receipt.process_group_cleanup_requested
    assert receipt.cleanup_reason == "cancel"
    assert receipt.termination_receipt is not None
    assert receipt.final_process_group_quiescent
    assert time.monotonic() - started < 6


def test_real_host_completed_command_reaps_background_child():
    started = time.monotonic()
    # Leader exits with a helper still running: it must not extend command lifetime.
    result = run_command("sh", "-c", "sleep 30 & echo done", timeout=4,
                         require_process_group_quiescence=True)
    assert result.returncode == 0
    assert result.stdout == "done"
    assert result.supervisor is not None
    assert result.supervisor.leader_exited_before_group_quiescence
    assert result.supervisor.process_group_cleanup_requested
    assert result.supervisor.final_process_group_quiescent
    assert time.monotonic() - started < 6
