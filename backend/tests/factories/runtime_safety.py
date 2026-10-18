from __future__ import annotations

from oh_goon.core.models.recovery import InvariantAudit
from oh_goon.runtime.database import LocalDatabase
from oh_goon.runtime.safety import RuntimeSafetyGate
from oh_goon.services.runtime_safety import RuntimeSafetyService


class _StableAuditor:
    def __init__(self, database: LocalDatabase | None = None) -> None:
        self._database = database

    def current_durable_revision(self) -> int:
        return self._database.durable_revision() if self._database is not None else 0

    def audit(self) -> InvariantAudit:
        return InvariantAudit(durable_revision=self.current_durable_revision(), journal_revision=0)


def proven_runtime_safety(database: LocalDatabase | None = None) -> RuntimeSafetyService:
    gate = RuntimeSafetyGate()
    service = RuntimeSafetyService(_StableAuditor(database), gate)  # type: ignore[arg-type]
    service.refresh()
    return service
