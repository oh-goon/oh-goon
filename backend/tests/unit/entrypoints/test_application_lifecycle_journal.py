from __future__ import annotations

import json
import shlex
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Any, cast

import pytest

from oh_goon.application import ApplicationRuntime
from oh_goon.core.models.run import FleetRunDetail
from oh_goon.core.models.run_events import ProcessStartingEvent
from oh_goon.core.product_identity import repository_id
from oh_goon.core.vocabulary import FleetRunEventType, FleetRunOutcome, FleetRunStatus
from oh_goon.engine.command import (
    CommandProcessLifecycleEvent,
    CommandProcessObserver,
    CommandResult,
    run_command,
)
from oh_goon.runtime._sqlite_authority_gate import sqlite_authority_gate
from oh_goon.runtime.journal_facts import JournalEntry
from oh_goon.runtime.journal_store import JournalFactStore


@pytest.mark.process
def test_step_lifecycle_reuses_publicly_warmed_journal_cut_and_refreshes_after_append(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = tmp_path / "repos" / "sample"
    repository.mkdir(parents=True)
    (repository / "work.py").write_text(
        "from pathlib import Path\nPath('result.txt').write_text('native work completed')\n"
    )
    source = repository / "goon.yml"
    command = shlex.join((sys.executable, "-I", "work.py"))
    source.write_text(
        "execution: {purpose: maintenance}\nsteps:\n- id: once\n  run: "
        + json.dumps(command)
        + "\n"
    )
    subprocess.run(["git", "init", "-q", str(repository)], check=True, capture_output=True)
    database_path = tmp_path / "state" / "app.sqlite"
    monkeypatch.setenv("OH_GOON_DB", str(database_path))
    for name, directory in (
        ("XDG_CONFIG_HOME", "config"),
        ("XDG_DATA_HOME", "data"),
        ("XDG_STATE_HOME", "state"),
    ):
        monkeypatch.setenv(name, str(tmp_path / directory))

    loads = 0
    original_load = JournalFactStore.load_entries

    def counted_load(connection: sqlite3.Connection) -> list[JournalEntry]:
        nonlocal loads
        loads += 1
        return original_load(connection)

    monkeypatch.setattr(JournalFactStore, "load_entries", staticmethod(counted_load))
    runtime = ApplicationRuntime.build(standard_local=True)
    run_id = "application-journal-reuse"
    readbacks: list[tuple[FleetRunDetail, FleetRunDetail]] = []
    read_counts: list[tuple[int, int, int]] = []
    process_ids: list[str] = []

    def observed_command(*argv: str, **options: Any) -> CommandResult:
        observer = cast(CommandProcessObserver, options["process_observer"])

        def observe(event: CommandProcessLifecycleEvent) -> None:
            if event.kind != "PROCESS_STARTING":
                observer(event)
                return
            with sqlite_authority_gate(database_path, write=True):
                before = runtime.runs.get(run_id)
                warmed_loads = loads
                observer(event)
                callback_loads = loads
                after = runtime.runs.get(run_id)
                readbacks.append((before, after))
                read_counts.append((warmed_loads, callback_loads, loads))
                process_ids.append(event.physical_execution_id)

        return run_command(*argv, **(options | {"process_observer": observe}))

    monkeypatch.setattr(
        "oh_goon.integrations.fleet_plan_step_execution.run_command", observed_command
    )
    try:
        runtime.locations.add(repository.parent)
        runtime.plans.lock(source)
        target = runtime.plans.prepare_execution_target(source)
        started = runtime.runs.start_prepared_plan(
            target, repository_ids={repository_id(repository)}, fleet_run_id=run_id
        )
        detail = runtime.runs.wait_for_terminal(started.fleet_run_id, timeout=30)
        assert detail.run.status is FleetRunStatus.COMPLETED
        assert detail.outcome is FleetRunOutcome.SUCCESS
        assert (repository / "result.txt").read_text() == "native work completed"
        events = runtime.runs.events(run_id).events
        for event_type in (
            FleetRunEventType.PROCESS_STARTING,
            FleetRunEventType.PROCESS_STARTED,
            FleetRunEventType.PROCESS_EXITED,
            FleetRunEventType.DESCENDANTS_QUIESCENT,
        ):
            assert any(event.event_type is event_type for event in events)

        assert len(readbacks) == len(read_counts) == len(process_ids) == 1
        before, after = readbacks[0]
        assert before.run.status is after.run.status is FleetRunStatus.RUNNING
        assert before.journal_revision is not None and after.journal_revision is not None
        assert after.journal_revision == before.journal_revision + 1
        assert any(
            isinstance(event, ProcessStartingEvent)
            and event.physical_execution_id == process_ids[0]
            for event in after.lifecycle_events
        )
        warmed_loads, callback_loads, refreshed_loads = read_counts[0]
        assert callback_loads == warmed_loads
        assert refreshed_loads > callback_loads
    finally:
        runtime.shutdown(wait_for_runs=True)
