from __future__ import annotations

import os
import sys
import time
from pathlib import Path


PROCESS_GROUP_POLL_INTERVAL_SECONDS = 0.1


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


def wait_for_process_group_quiescence(
    process_group_id: int,
    *,
    timeout: float,
    poll_interval: float = PROCESS_GROUP_POLL_INTERVAL_SECONDS,
) -> bool:
    """Boundedly prove that an owned process group has no executable members."""

    if timeout < 0:
        raise ValueError("process-group quiescence timeout must be non-negative")
    if poll_interval <= 0:
        raise ValueError("process-group poll interval must be positive")
    deadline = time.monotonic() + timeout
    while process_group_exists(process_group_id):
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
            if len(fields) < 3:
                incomplete = True
                continue
            state = fields[0]
            group = int(fields[2])
        except (OSError, ValueError, UnicodeError):
            # A disappearing/inaccessible process may be a member of the
            # group being fenced.  An incomplete /proc inventory cannot
            # authorize physical quiescence; defer to the kernel signal probe.
            incomplete = True
            continue
        inspected = True
        if group == process_group_id and state != "Z":
            return True
    return False if inspected and not incomplete else None
