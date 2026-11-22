"""Original process birth attribution survives Step observation and Journal replay."""

from __future__ import annotations

import sys
from dataclasses import replace

import pytest

from oh_goon.core.errors import JournalConflict
from oh_goon.core.models.repository import RepositoryIdentity
from oh_goon.core.models.run_events import ProcessStartingEvent
from oh_goon.core.vocabulary import FleetRunEventType, OperationAction
from oh_goon.engine.command import run_command
from oh_goon.integrations.fleet_plan_step_lifecycle_observation import lifecycle_command_runner
from oh_goon.runtime.database import LocalDatabase
from oh_goon.runtime.journal import Journal
from tests.factories.capabilities import admit_test_run
from tests.factories.journal import start_repository, start_run


def test_workspace_launch_attribution_uses_original_process_birth_and_durable_step_owner(tmp_path):
    database = LocalDatabase(tmp_path / "state/control.sqlite")
    journal = Journal(database)
    repository = RepositoryIdentity(path=tmp_path)
    admit_test_run(journal, "writer-run", OperationAction.INIT, [repository])
    start_run(journal, "writer-run")
    operation_id = start_repository(journal, "writer-run", repository)
    observer = journal.execution_lifecycle_observer(
        "writer-run", repository.repository_id, operation_id
    )
    observer("native", 1, "STEP_REACHED")
    observer("native", 1, "STEP_STARTED")
    observer("native", 1, "STEP_ATTEMPT_CREATED", attempt_number=1)

    def backend(*argv, process_observer, **options):
        def attribute_birth(event):
            process_observer(
                replace(event, workspace_materialization_id="original-materialization")
                if event.kind == "PROCESS_STARTING"
                else event
            )

        return run_command(*argv, process_observer=attribute_birth, **options)

    runner = lifecycle_command_runner(
        backend, step_id="native", step_order=1, attempt_number=1, observer=observer
    )
    result = runner(sys.executable, "-c", "print('completed')", cwd=tmp_path)
    assert result.returncode == 0
    events = Journal(database).run_events("writer-run")
    births = [event for event in events.events if type(event) is ProcessStartingEvent]
    assert len(births) == 1
    birth = births[0]
    assert birth.workspace_materialization_id == "original-materialization"
    assert birth.step_id == "native" and birth.operation_id == operation_id
    for event in events.events:
        if isinstance(event, ProcessStartingEvent) and event is not birth:
            assert "workspace_materialization_id" not in event.model_dump(mode="json")
    assert ProcessStartingEvent.model_validate_json(birth.model_dump_json()) == birth
    with pytest.raises(JournalConflict, match="Conflicting lifecycle replay"):
        journal.record_execution_lifecycle(
            fleet_run_id="writer-run",
            repository_id=repository.repository_id,
            operation_id=operation_id,
            event_type=FleetRunEventType.PROCESS_STARTING,
            step_id="native",
            step_order=1,
            attempt_number=1,
            physical_execution_id=birth.physical_execution_id,
            workspace_materialization_id="foreign-materialization",
        )
    assert journal.run_events("writer-run") == events
