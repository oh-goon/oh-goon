from __future__ import annotations

import os
import sys
import time
from collections.abc import Callable
from pathlib import Path

PROCESS_GROUP_POLL_INTERVAL_SECONDS = 0.1


def reap_process_group_children(process_group_id: int) -> None:
    """Reap only this owner's adopted, exited children in one owned group.

    The caller first reaps its Popen leader and retains the command's native
    session pin throughout this loop. That pin prevents numeric PGID reuse;
    P_PGID alone would not establish ownership after the original group retires.
    """
    if process_group_id <= 0:
        raise ValueError("Owned process-group identity must be positive")
    if not sys.platform.startswith("linux"):
        return
    while True:
        try:
            status = os.waitid(os.P_PGID, process_group_id, os.WEXITED | os.WNOHANG)
        except ChildProcessError:
            return
        if status is None:
            return


def process_group_exists(process_group_id: int) -> bool:
    """Return whether an owned POSIX process group still has executable work."""

    if os.name == "nt":
        return False
    if sys.platform.startswith("linux"):
        observed = _linux_process_group_has_live_member(process_group_id)
        if observed is not None:
            return observed
    try:
        os.killpg(process_group_id, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def process_session_exists(session_id: int) -> bool:
    """Observe executable members of one original Linux command session.

    Session identity includes the passive native parent guard after the workload
    group exits. Unavailable inventory remains live/unverified, never absence.
    """
    if session_id <= 0:
        raise ValueError("Owned process-session identity must be positive")
    if not sys.platform.startswith("linux"):
        return True
    observed = _linux_process_scope_has_live_member(session_id, session=True)
    return observed is not False


def wait_for_process_group_quiescence(
    process_group_id: int,
    *,
    timeout: float,
    poll_interval: float = PROCESS_GROUP_POLL_INTERVAL_SECONDS,
) -> bool:
    """Boundedly prove that an owned process group has no executable members."""

    return _wait_for_scope_quiescence(
        lambda: process_group_exists(process_group_id), timeout=timeout, poll_interval=poll_interval
    )


def wait_for_process_session_quiescence(
    session_id: int,
    *,
    timeout: float,
    poll_interval: float = PROCESS_GROUP_POLL_INTERVAL_SECONDS,
) -> bool:
    """Boundedly observe full owned-session retirement, including its guard."""
    return _wait_for_scope_quiescence(
        lambda: process_session_exists(session_id), timeout=timeout, poll_interval=poll_interval
    )


def _wait_for_scope_quiescence(
    exists: Callable[[], bool], *, timeout: float, poll_interval: float
) -> bool:
    if timeout < 0:
        raise ValueError("process-group quiescence timeout must be non-negative")
    if poll_interval <= 0:
        raise ValueError("process-group poll interval must be positive")
    deadline = time.monotonic() + timeout
    while exists():
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        time.sleep(min(poll_interval, remaining))
    return True


def _linux_process_group_has_live_member(process_group_id: int) -> bool | None:
    """Return whether /proc shows a non-zombie member of one process group.

    ``killpg(pgid, 0)`` reports success for zombie-only groups. Those processes can
    no longer execute or retain output-pipe writers, so they are quiescent for the
    stale-work boundary. ``None`` means /proc could not be inspected and callers
    should fall back to the portable signal probe.
    """

    return _linux_process_scope_has_live_member(process_group_id, session=False)


def _linux_process_scope_has_live_member(scope_id: int, *, session: bool) -> bool | None:
    """One existing native inventory owner, projected by group or session."""
    proc = Path("/proc")
    try:
        entries = tuple(proc.iterdir())
    except OSError:
        return None
    inspected = False
    incomplete = False
    for entry in entries:
        if not entry.name.isdigit():
            continue
        try:
            raw = (entry / "stat").read_text(encoding="ascii")
            closing = raw.rfind(")")
            if closing < 0:
                incomplete = True
                continue
            fields = raw[closing + 2 :].split()
            if len(fields) < (4 if session else 3):
                incomplete = True
                continue
            state = fields[0]
            identity = int(fields[3 if session else 2])
        except (OSError, ValueError, UnicodeError):
            # A disappearing/inaccessible process may be a member of the
            # group being fenced.  An incomplete /proc inventory cannot
            # authorize physical quiescence; defer to the kernel signal probe.
            incomplete = True
            continue
        inspected = True
        if identity == scope_id and state != "Z":
            return True
    return False if inspected and not incomplete else None
