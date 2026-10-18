from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from oh_goon.core.models.schedule import ScheduleDefinition
from oh_goon.core.models.schedule_execution import ScheduleRunRecord
from oh_goon.core.time import utc_now
from oh_goon.core.vocabulary import ScheduleState


@dataclass(frozen=True, slots=True)
class ScheduleTimingDecision:
    """Pure wall-clock decision bound to one exact Schedule definition generation."""

    definition_generation_id: str
    due_at: datetime | None
    due: bool


def schedule_timing_decision(
    definition: ScheduleDefinition,
    latest: ScheduleRunRecord | None,
    *,
    now: datetime | None = None,
) -> ScheduleTimingDecision:
    """Decide whether a Schedule occurrence is due without starting repository work."""
    if definition.state is not ScheduleState.ACTIVE:
        return ScheduleTimingDecision(definition.generation_id, None, False)
    if latest is not None and not latest.status.terminal:
        return ScheduleTimingDecision(definition.generation_id, None, False)

    baseline = definition.created_at if latest is None else latest.finished_at
    due_at = baseline + timedelta(minutes=definition.interval_minutes)
    return ScheduleTimingDecision(
        definition.generation_id,
        due_at,
        (now or utc_now()) >= due_at,
    )
