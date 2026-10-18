from __future__ import annotations

from dataclasses import dataclass

from oh_goon.core.models.effect_attempt import RepositoryEffectAttempt
from oh_goon.core.models.schedule_execution import ScheduleRunRecord
from oh_goon.runtime.execution.sqlite_lease import ExecutionLease
from oh_goon.runtime.journal_view import JournalReadView


@dataclass(frozen=True, slots=True)
class _RuntimeSafetyCutBase:
    """Facts that remain meaningful even when Journal integrity cannot be established."""

    durable_revision: int
    active_lease: ExecutionLease | None
    effect_attempts: tuple[RepositoryEffectAttempt, ...]
    active_schedules: tuple[ScheduleRunRecord, ...]


@dataclass(frozen=True, slots=True)
class ConsistentRuntimeSafetyCut(_RuntimeSafetyCutBase):
    """One transactionally consistent safety cut with a validated Journal view."""

    journal: JournalReadView


@dataclass(frozen=True, slots=True)
class JournalCorruptRuntimeSafetyCut(_RuntimeSafetyCutBase):
    """One safety cut whose Journal could not be validated and therefore cannot admit effects."""

    journal_error: str

    def __post_init__(self) -> None:
        if not self.journal_error:
            raise ValueError("Journal-corrupt runtime safety cut requires an integrity error")


RuntimeSafetyCut = ConsistentRuntimeSafetyCut | JournalCorruptRuntimeSafetyCut
