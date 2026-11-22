from __future__ import annotations

from collections.abc import Callable

from oh_goon.core.models.recovery_status import RecoveryStatus
from oh_goon.core.models.runtime_safety import RuntimeSafetyAssessment
from oh_goon.services.effect_recovery import RepositoryEffectRecovery
from oh_goon.services.runtime_safety import RuntimeSafetyService


class RecoveryService:
    """Operator-facing recovery for unresolved repository effects."""

    def __init__(
        self,
        recovery: RepositoryEffectRecovery,
        runtime_safety: RuntimeSafetyService,
        *,
        settle_worker_resources: Callable[[str], None] | None = None,
    ) -> None:
        self._recovery = recovery
        self._runtime_safety = runtime_safety
        self._settle_worker_resources = settle_worker_resources

    def status(self) -> RecoveryStatus:
        return self._status_from(self._runtime_safety.assess())

    def recover_worker_exit(
        self, fleet_run_id: str, *, quiescence_timeout_seconds: float = 0.0
    ) -> RecoveryStatus:
        """Reconcile one controlled worker exit without redispatch or a global sweep."""
        self._recovery.recover(
            fleet_run_id=fleet_run_id, quiescence_timeout_seconds=quiescence_timeout_seconds
        )
        if self._settle_worker_resources is not None:
            self._settle_worker_resources(fleet_run_id)
        return self._status_from(self._runtime_safety.assess())

    def reconcile(self, operation_id: str, *, expected_record_version: int) -> RecoveryStatus:
        self._recovery.reconcile(operation_id, expected_record_version=expected_record_version)
        return self._status_from(self._runtime_safety.assess())

    def acknowledge(
        self, operation_id: str, *, note: str, expected_record_version: int
    ) -> RecoveryStatus:
        self._recovery.acknowledge(
            operation_id, note=note, expected_record_version=expected_record_version
        )
        return self._status_from(self._runtime_safety.assess())

    @staticmethod
    def _status_from(assessment: RuntimeSafetyAssessment) -> RecoveryStatus:
        return RecoveryStatus(
            effect_quarantines=assessment.effect_quarantines,
            repair_plan=assessment.repair_plan,
            runtime_safety=assessment.proof,
        )
