from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

import pytest

from oh_goon.core.errors import ExecutionBusy
from oh_goon.core.time import utc_now
from oh_goon.core.vocabulary import ScheduleExecutionStatus
from oh_goon.services.schedule_admission import ScheduleAdmissionPlan
from oh_goon.services.schedule_timing import schedule_timing_decision
from oh_goon.services.schedules import ScheduleService
from tests.factories.schedules import (
    schedule_definition,
    schedule_run_record,
    scheduled_fleet_run_intent,
)


def _definition(*, interval: int = 60, created_minutes_ago: int = 120):
    now = utc_now() - timedelta(minutes=created_minutes_ago)
    return schedule_definition(now=now, interval_minutes=interval)


def _completed(
    definition,
    *,
    started_minutes_ago: int,
    finished_minutes_ago: int,
):
    finished = utc_now() - timedelta(minutes=finished_minutes_ago)
    started = utc_now() - timedelta(minutes=started_minutes_ago)
    return schedule_run_record(
        "schedule-run-timing",
        ScheduleExecutionStatus.COMPLETED,
        definition=definition,
        started_at=started,
        finished_at=finished,
    )


def test_new_schedule_waits_one_full_interval_from_creation() -> None:
    definition = _definition(interval=60, created_minutes_ago=0)

    before = schedule_timing_decision(
        definition,
        None,
        now=definition.created_at + timedelta(minutes=59),
    )
    assert not before.due
    assert before.due_at == definition.created_at + timedelta(minutes=60)

    due = schedule_timing_decision(
        definition,
        None,
        now=definition.created_at + timedelta(minutes=60),
    )
    assert due.due
    assert due.due_at == definition.created_at + timedelta(minutes=60)


def test_interval_is_anchored_to_terminal_completion_not_start_time() -> None:
    definition = _definition(interval=60)
    latest = _completed(definition, started_minutes_ago=120, finished_minutes_ago=5)

    before = schedule_timing_decision(
        definition,
        latest,
        now=latest.finished_at + timedelta(minutes=59),
    )
    assert not before.due

    due = schedule_timing_decision(
        definition,
        latest,
        now=latest.finished_at + timedelta(minutes=60),
    )
    assert due.due
    assert due.due_at == latest.finished_at + timedelta(minutes=60)


def test_active_execution_is_never_interval_due() -> None:
    definition = _definition(interval=1)
    active = schedule_run_record(
        "schedule-run-active",
        ScheduleExecutionStatus.RUNNING,
        definition=definition,
        started_at=utc_now() - timedelta(hours=1),
    )

    decision = schedule_timing_decision(definition, active, now=utc_now())

    assert not decision.due
    assert decision.due_at is None


def _scheduled_admission(definition):
    class Admission:
        def plan_scheduled(self, schedule_id: str):
            assert schedule_id == definition.schedule_id
            decision = schedule_timing_decision(
                definition,
                None,
                now=definition.created_at + timedelta(minutes=definition.interval_minutes),
            )
            if not decision.due:
                return None
            return (
                ScheduleAdmissionPlan(
                    durable_revision=0,
                    schedule_id=definition.schedule_id,
                    intent=scheduled_fleet_run_intent(definition),
                ),
                decision,
            )

    return Admission()


def test_transient_busy_is_visible_to_scheduler_instead_of_silently_swallowed() -> None:
    definition = _definition(interval=1)
    attempts = 0

    class Definitions:
        def definitions(self):
            return [definition]

    class Engine:
        def advance_all(self, **_kwargs) -> None:
            return None

        def start(self, *_args, **_kwargs):
            nonlocal attempts
            attempts += 1
            raise ExecutionBusy("busy")

    service = ScheduleService(
        SimpleNamespace(),  # type: ignore[arg-type]
        Definitions(),  # type: ignore[arg-type]
        SimpleNamespace(),  # type: ignore[arg-type]
        Engine(),  # type: ignore[arg-type]
        _scheduled_admission(definition),  # type: ignore[arg-type]
        SimpleNamespace(effects_allowed=lambda: True),  # type: ignore[arg-type]
    )

    with pytest.raises(ExecutionBusy, match="busy"):
        service.tick()
    assert attempts == 1
