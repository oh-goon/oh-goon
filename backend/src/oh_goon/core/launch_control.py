from __future__ import annotations

import os
import signal
from collections.abc import Callable
from dataclasses import dataclass

from oh_goon.core.launch_manifest import LaunchManifest
from oh_goon.core.process_birth import ProcessBirthIdentity, observe_process_birth

ProcessObserver = Callable[[int], ProcessBirthIdentity | None]
PidfdOpen = Callable[[int, int], int]
PidfdSignal = Callable[[int, int, object | None, int], None]
FdCloser = Callable[[int], None]


@dataclass(frozen=True, slots=True)
class LaunchStopTarget:
    launch_id: str
    owner_generation: int
    expected_birth: ProcessBirthIdentity


def safe_launch_stop_target(
    manifest: LaunchManifest,
    *,
    process_observer: ProcessObserver = observe_process_birth,
) -> LaunchStopTarget | None:
    """Resolve the exact owner process eligible for stop; never infer lifecycle from absence."""

    if manifest.lifecycle.is_terminal:
        return None
    owner = manifest.observed.owner_process
    if owner is None or owner.birth_identity is None:
        raise ValueError("launch owner birth identity is unavailable; stop refused")
    expected = owner.birth_identity.value
    if process_observer(expected.pid) != expected:
        raise ValueError("launch owner birth identity cannot be re-proven; stop refused")
    return LaunchStopTarget(
        launch_id=manifest.identity.launch_id,
        owner_generation=manifest.identity.owner_generation,
        expected_birth=expected,
    )


def signal_launch_stop(
    target: LaunchStopTarget,
    *,
    process_observer: ProcessObserver = observe_process_birth,
    pidfd_open: PidfdOpen | None = None,
    pidfd_send_signal: PidfdSignal | None = None,
    close_fd: FdCloser = os.close,
) -> None:
    """Signal the already-proven owner through a stable pidfd process handle.

    The caller never writes lifecycle events. The launch owner receives SIGTERM and
    remains the only authority that can publish stopping/stopped/aborted truth.
    """

    open_pidfd = pidfd_open or getattr(os, "pidfd_open", None)
    send_pidfd_signal = pidfd_send_signal or getattr(signal, "pidfd_send_signal", None)
    if open_pidfd is None or send_pidfd_signal is None:
        raise ValueError("stable pidfd stop authority is unavailable on this platform")

    expected = target.expected_birth
    try:
        pidfd = open_pidfd(expected.pid, 0)
    except OSError as exc:
        raise ValueError("stable launch owner process handle is unavailable; stop refused") from exc
    try:
        # Close the PID-reuse race between the initial proof and pidfd acquisition.
        # If the PID changed before pidfd_open, this re-observation fails. If it exits
        # after pidfd_open, the pidfd can never retarget a subsequently reused PID.
        if process_observer(expected.pid) != expected:
            raise ValueError(
                "launch owner changed before stable stop handle was proven; stop refused"
            )
        try:
            send_pidfd_signal(pidfd, signal.SIGTERM, None, 0)
        except OSError as exc:
            raise ValueError("launch owner could not accept stop request") from exc
    finally:
        close_fd(pidfd)
