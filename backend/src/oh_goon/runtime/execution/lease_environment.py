from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import Protocol

from oh_goon.core.process_birth import observe_process_birth, observe_process_liveness


class LeaseEnvironment(Protocol):
    def now(self) -> datetime: ...
    def process_id(self) -> int: ...
    def process_alive(self, pid: int) -> bool: ...
    def process_identity(self, pid: int) -> str | None: ...


class SystemLeaseEnvironment:
    def now(self) -> datetime:
        return datetime.now(UTC)

    def process_id(self) -> int:
        return os.getpid()

    def process_alive(self, pid: int) -> bool:
        observed = observe_process_liveness(pid)
        if observed is not None:
            return observed
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except OSError:
            # EPERM, platform observability failures and unknown errno are NOT
            # proof that the predecessor is dead. Preserve its lease instead.
            return True
        return True

    def process_identity(self, pid: int) -> str | None:
        """Return the canonical process-birth identity when the host can prove it."""

        birth = observe_process_birth(pid)
        if birth is None:
            return None
        return f"{birth.provider}:{birth.boot_id}:{birth.start_ticks}"
