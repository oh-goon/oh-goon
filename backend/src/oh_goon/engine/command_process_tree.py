from __future__ import annotations

import os
import signal
import subprocess
import time
from threading import Thread

from oh_goon.runtime.process_group import process_group_exists as _process_group_exists
from oh_goon.runtime.termination import ProcessTreeTerminationReceipt, TerminationRequest

POLL_INTERVAL_SECONDS = 0.1
TERMINATION_GRACE_SECONDS = 1.0
OUTPUT_DRAIN_TIMEOUT_SECONDS = 5.0


def _finish_process_output(process: subprocess.Popen[str], readers: tuple[Thread, Thread]) -> bool:
    forced_termination = False
    if os.name != "nt" and _process_group_exists(process.pid):
        # The command leader exited while descendants still own its process group/pipes.
        # A Step must not leave background work behind after its command returns.
        forced_termination = (
            _terminate_process_group(process, close_pipes=False) or forced_termination
        )
    if _join_readers(readers, timeout=OUTPUT_DRAIN_TIMEOUT_SECONDS):
        return forced_termination
    forced_termination = _terminate_process_group(process) or forced_termination
    _close_process_pipes(process)
    if not _join_readers(readers, timeout=TERMINATION_GRACE_SECONDS):
        raise RuntimeError("Command output readers did not stop after process-group cleanup")
    return forced_termination


def _join_readers(readers: tuple[Thread, Thread], *, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    for reader in readers:
        reader.join(timeout=max(0.0, deadline - time.monotonic()))
    return all(not reader.is_alive() for reader in readers)


def _close_process_pipes(process: subprocess.Popen[str]) -> None:
    for pipe in (process.stdin, process.stdout, process.stderr):
        if pipe is not None and not pipe.closed:
            pipe.close()


def terminate_owned_process_tree(
    process: subprocess.Popen[str], request: TerminationRequest
) -> ProcessTreeTerminationReceipt:
    """Apply one bounded TERM -> grace -> KILL authority and prove final quiescence.

    The caller supplies the admitted execution identity/generation in ``request``.
    This owner acts only on the already-owned process group represented by ``process``.
    """
    escalated = _terminate_process_group(
        process, close_pipes=True, grace_seconds=request.grace_seconds
    )
    quiescent = os.name == "nt" or not _process_group_exists(process.pid)
    return ProcessTreeTerminationReceipt(
        request_identity=request.request_identity,
        term_requested=True,
        grace_seconds=request.grace_seconds,
        escalated_to_kill=escalated,
        quiescent=quiescent,
        final_state="quiescent" if quiescent else "survivors-detected",
    )


def _terminate_process_group(
    process: subprocess.Popen[str],
    *,
    close_pipes: bool = True,
    grace_seconds: float = TERMINATION_GRACE_SECONDS,
) -> bool:
    forced = (
        _terminate_windows(process, grace_seconds)
        if os.name == "nt"
        else _terminate_posix(process, grace_seconds)
    )
    process.wait()
    if close_pipes:
        _close_process_pipes(process)
    return forced


def _terminate_windows(process: subprocess.Popen[str], grace_seconds: float) -> bool:
    if process.poll() is not None:
        return False
    process.terminate()
    try:
        process.wait(timeout=grace_seconds)
        return False
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
        return True


def _terminate_posix(process: subprocess.Popen[str], grace_seconds: float) -> bool:
    group = process.pid
    if not _process_group_exists(group):
        return False
    try:
        os.killpg(group, signal.SIGTERM)
    except ProcessLookupError:
        return False
    deadline = time.monotonic() + grace_seconds
    while _process_group_exists(group) and time.monotonic() < deadline:
        # Reap our leader during grace: the portable group probe also sees zombies.
        # Its exit alone does not settle descendants, so keep probing the whole group.
        process.poll()
        time.sleep(POLL_INTERVAL_SECONDS)
    process.poll()
    if not _process_group_exists(group):
        return False
    try:
        os.killpg(group, signal.SIGKILL)
        return True
    except ProcessLookupError:
        return False
