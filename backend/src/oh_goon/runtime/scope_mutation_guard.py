from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from oh_goon.core.errors import DurableStateBusy
from oh_goon.runtime.execution import ExecutionCoordinator
from oh_goon.runtime.journal import Journal


@dataclass(frozen=True, slots=True)
class DurableScopeMutationGuard:
    """Own the rule that configured Fleet scope mutates only while runtime work is idle."""

    execution: ExecutionCoordinator
    journal: Journal

    def require_idle(self, connection: sqlite3.Connection) -> None:
        self.execution.assert_idle(connection)
        active = self.journal.read_view_from_connection(connection).active_run()
        if active is not None:
            raise DurableStateBusy(
                f"Cannot change durable fleet state while run {active.fleet_run_id} is active"
            )
