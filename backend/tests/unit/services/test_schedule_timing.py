from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from oh_goon.core.errors import ExecutionBusy
from oh_goon.core.models.schedule import ScheduleDefinition
from oh_goon.core.models.schedule_execution import CompletedScheduleRun
from oh_goon.core.time import utc_now
from oh_goon.core.vocabulary import ScheduleExecutionStatus, ScheduleState
from oh_goon.services.schedule_admission import ScheduleAdmissionPlan
from oh_goon.services.schedule_timing import ScheduleTimingDecision, schedule_timing_decision
from oh_goon.services.schedules import ScheduleService
from tests.factories.schedules import (
    schedule_definition,
    schedule_run_record,
    scheduled_fleet_run_intent,
)


def _definition(*, interval: int = 60, created_minutes_ago: int = 120) -> ScheduleDefinition:
    now = utc_now() - timedelta(minutes=created_minutes_ago)
    return schedule_definition(now=now, interval_minutes=interval)


def _completed(
    definition: ScheduleDefinition,
    *,
    started_minutes_ago: int,
    finished_minutes_ago: int,
) -> CompletedScheduleRun:
    finished = utc_now() - timedelta(minutes=finished_minutes_ago)
    started = utc_now() - timedelta(minutes=started_minutes_ago)
    completed = schedule_run_record(
        "schedule-run-timing",
        ScheduleExecutionStatus.COMPLETED,
        definition=definition,
        started_at=started,
        finished_at=finished,
    )
    assert isinstance(completed, CompletedScheduleRun)
    return completed


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


@pytest.mark.parametrize("has_terminal", (False, True))
def test_paused_schedule_is_not_due_even_after_its_interval(
    has_terminal: bool,
) -> None:
    definition = _definition(interval=1)
    latest = (
        _completed(definition, started_minutes_ago=120, finished_minutes_ago=60)
        if has_terminal
        else None
    )
    baseline = definition.created_at if latest is None else latest.finished_at
    now = baseline + timedelta(days=1)
    assert schedule_timing_decision(definition, latest, now=now).due

    paused = definition.model_copy(update={"state": ScheduleState.PAUSED})
    decision = schedule_timing_decision(paused, latest, now=now)

    assert not decision.due
    assert decision.due_at is None


@pytest.mark.parametrize("has_terminal", (False, True))
def test_missed_intervals_keep_the_original_due_anchor(has_terminal: bool) -> None:
    definition = _definition(interval=60)
    latest = (
        _completed(definition, started_minutes_ago=120, finished_minutes_ago=60)
        if has_terminal
        else None
    )
    baseline = definition.created_at if latest is None else latest.finished_at
    due_at = baseline + timedelta(minutes=definition.interval_minutes)

    decision = schedule_timing_decision(definition, latest, now=due_at + timedelta(days=7))

    assert decision.due
    assert decision.due_at == due_at


def _scheduled_admission(
    definition: ScheduleDefinition, *, now: datetime | None = None
) -> SimpleNamespace:
    def plan_scheduled(
        schedule_id: str,
    ) -> tuple[ScheduleAdmissionPlan, ScheduleTimingDecision] | None:
        assert schedule_id == definition.schedule_id
        decision = schedule_timing_decision(
            definition,
            None,
            now=now or definition.created_at + timedelta(minutes=definition.interval_minutes),
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

    return SimpleNamespace(plan_scheduled=plan_scheduled)


def test_one_tick_admits_one_occurrence_instead_of_replaying_missed_intervals() -> None:
    definition = _definition(interval=1)
    starts: list[tuple[str, str]] = []

    class Definitions:
        def definitions(self) -> list[ScheduleDefinition]:
            return [definition]

    class Engine:
        def advance_all(self, *, allow_effect_admission: bool) -> None:
            assert allow_effect_admission

        def start(self, admission: ScheduleAdmissionPlan, *, schedule_run_id: str) -> None:
            starts.append((admission.schedule_id, schedule_run_id))
            assert len(starts) == 1, "missed intervals cannot become implicit replay"

    service = ScheduleService(
        SimpleNamespace(),  # type: ignore[arg-type]
        Definitions(),  # type: ignore[arg-type]
        SimpleNamespace(),  # type: ignore[arg-type]
        Engine(),  # type: ignore[arg-type]
        _scheduled_admission(definition, now=definition.created_at + timedelta(days=7)),  # type: ignore[arg-type]
        SimpleNamespace(effects_allowed=lambda: True),  # type: ignore[arg-type]
    )

    service.tick()

    assert len(starts) == 1
    assert starts[0][0] == definition.schedule_id
    assert starts[0][1]


def test_transient_busy_is_visible_to_scheduler_instead_of_silently_swallowed() -> None:
    definition = _definition(interval=1)
    attempts = 0

    class Definitions:
        def definitions(self) -> list[ScheduleDefinition]:
            return [definition]

    class Engine:
        def advance_all(self, **_kwargs: object) -> None:
            return None

        def start(self, *_args: object, **_kwargs: object) -> None:
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
