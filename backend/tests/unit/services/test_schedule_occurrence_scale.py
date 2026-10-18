from __future__ import annotations

from oh_goon.core.schedule_transitions import begin_schedule_run
from oh_goon.core.time import utc_now
from oh_goon.core.vocabulary import ScheduleOccurrenceFilter, ScheduleRepositoryRunStatus
from oh_goon.runtime.database import LocalDatabase
from oh_goon.runtime.schedule_executions import ScheduleExecutionStore
from oh_goon.services.schedules import ScheduleService, _public_occurrence
from tests.factories.schedules import schedule_definition, scheduled_fleet_run_intent


def test_500_target_occurrence_stays_aggregate_and_repository_reads_are_bounded(tmp_path) -> None:
    database = LocalDatabase(tmp_path / "oh-goon.db")
    repository_ids = tuple(f"repo-{index:03d}" for index in range(500))
    definition = schedule_definition(now=utc_now(), repository_ids=repository_ids)
    execution = begin_schedule_run(
        schedule_run_id="schedule-run-500",
        schedule_id=definition.schedule_id,
        intent=scheduled_fleet_run_intent(definition),
        started_at=utc_now(),
    )
    store = ScheduleExecutionStore(database)
    store.insert(execution, expected_durable_revision=database.durable_revision())
    service = ScheduleService(database, None, store, None, None, None)  # type: ignore[arg-type]

    aggregate = _public_occurrence(execution)
    assert aggregate.expected == 500
    assert aggregate.pending == 500
    assert aggregate.not_started == 0
    assert "repository_runs" not in aggregate.model_dump()

    page = service.occurrence_repositories(
        definition.schedule_id,
        execution.schedule_run_id,
        status=ScheduleOccurrenceFilter.PENDING,
        offset=0,
        limit=50,
    )
    assert page.total == 500
    assert len(page.items) == 50
    assert all(item.status is ScheduleRepositoryRunStatus.PENDING for item in page.items)
    assert tuple(item.repository_id for item in page.items) == repository_ids[:50]
