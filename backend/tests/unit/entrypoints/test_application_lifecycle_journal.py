from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from oh_goon.application import ApplicationRuntime
from oh_goon.core.models.run_events import ProcessStartingEvent
from oh_goon.core.product_identity import repository_id
from oh_goon.core.vocabulary import FleetRunEventType, FleetRunOutcome, FleetRunStatus
from oh_goon.runtime.execution.run_supervisor import RunExecutionSupervisor, RunWorkerLaunch

_OBSERVED_JOURNAL_WORKER = r"""
import json, os, runpy, sys
from pathlib import Path

from oh_goon import _application_worker_runtime as worker_runtime
from oh_goon.integrations import fleet_plan_step_execution as step_owner
from oh_goon.core.models.run_events import ProcessStartingEvent
from oh_goon.runtime._sqlite_authority_gate import sqlite_authority_gate
from oh_goon.runtime.journal_codec import JournalFactCodec

header, database_path, observation_path, run_id = sys.argv[1:]
decodes = verifications = 0
original_decode = JournalFactCodec.decode
original_verify = JournalFactCodec.verify
original_build = worker_runtime.build_claimed_run_execution
original_journal = worker_runtime.Journal
original_command = step_owner.run_command

def counted_decode(**fields):
    global decodes
    decodes += 1
    return original_decode(**fields)

def counted_verify(**fields):
    global verifications
    verifications += 1
    return original_verify(**fields)

JournalFactCodec.decode = staticmethod(counted_decode)
JournalFactCodec.verify = staticmethod(counted_verify)

def observed_build(database, claimed_ticket, configuration, **options):
    assert claimed_ticket.require_fleet_run_id() == run_id
    journals = []

    def observe_journal(selected_database):
        assert selected_database is database
        journal = original_journal(selected_database)
        journals.append(journal)
        return journal

    # Keep the original composition and its exact cache-bearing Journal.
    worker_runtime.Journal = observe_journal
    try:
        execution = original_build(database, claimed_ticket, configuration, **options)
    finally:
        worker_runtime.Journal = original_journal
    assert len(journals) == 1
    journal = journals[0]

    def observed_command(*argv, **command_options):
        observer = command_options['process_observer']

        def observe(event):
            if event.kind != 'PROCESS_STARTING':
                observer(event)
                return
            with sqlite_authority_gate(Path(database_path), write=True):
                before = journal.read_view().required_run_detail(run_id)
                warmed_counts = (decodes, verifications)
                observer(event)
                callback_counts = (decodes, verifications)
                after = journal.read_view().required_run_detail(run_id)
                refreshed_counts = (decodes, verifications)
                payload = {
                    'run_id': run_id,
                    'worker_pid': os.getpid(),
                    'physical_execution_id': event.physical_execution_id,
                    'before_status': before.run.status,
                    'after_status': after.run.status,
                    'before_revision': before.journal_revision,
                    'after_revision': after.journal_revision,
                    'before_process_starts': [
                        item.model_dump(mode='json') for item in before.lifecycle_events
                        if type(item) is ProcessStartingEvent
                    ],
                    'after_process_starts': [
                        item.model_dump(mode='json') for item in after.lifecycle_events
                        if type(item) is ProcessStartingEvent
                    ],
                    'read_counts': (warmed_counts, callback_counts, refreshed_counts),
                }
                encoded = json.dumps(payload).encode('utf-8')
                assert len(encoded) <= 64 * 1024
                descriptor = os.open(observation_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(descriptor, 'wb') as stream:
                    stream.write(encoded)

        return original_command(*argv, **(command_options | {'process_observer': observe}))

    step_owner.run_command = observed_command
    return execution

worker_runtime.build_claimed_run_execution = observed_build
sys.argv = ['oh_goon._application_worker', '--handoff', header]
runpy.run_module('oh_goon._application_worker', run_name='__main__', alter_sys=True)
"""


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

    run_id = "application-journal-reuse"
    observation_root = tmp_path / "worker-observation"
    observation_root.mkdir(mode=0o700)
    observation_path = observation_root / "journal.json"
    original_submit = RunExecutionSupervisor.submit

    def observed_submit(
        supervisor: RunExecutionSupervisor, fleet_run_id: str, launch: RunWorkerLaunch
    ) -> None:
        assert fleet_run_id == run_id
        original_submit(
            supervisor,
            fleet_run_id,
            replace(
                launch,
                argv=(
                    launch.argv[0],
                    "-I",
                    "-c",
                    _OBSERVED_JOURNAL_WORKER,
                    launch.argv[-1],
                    str(database_path),
                    str(observation_path),
                    run_id,
                ),
            ),
        )

    monkeypatch.setattr(RunExecutionSupervisor, "submit", observed_submit)
    runtime = ApplicationRuntime.build(standard_local=True)
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

        with observation_path.open("rb") as stream:
            encoded = stream.read(64 * 1024 + 1)
        assert len(encoded) <= 64 * 1024
        observed = json.loads(encoded)
        assert observed["run_id"] == run_id
        assert observed["worker_pid"] > 0 and observed["worker_pid"] != os.getpid()
        assert observed["before_status"] == observed["after_status"] == FleetRunStatus.RUNNING
        assert observed["before_revision"] is not None and observed["after_revision"] is not None
        assert observed["after_revision"] == observed["before_revision"] + 1
        process_id = observed["physical_execution_id"]
        before = [
            ProcessStartingEvent.model_validate(item) for item in observed["before_process_starts"]
        ]
        after = [
            ProcessStartingEvent.model_validate(item) for item in observed["after_process_starts"]
        ]
        assert not any(event.physical_execution_id == process_id for event in before)
        appended = [event for event in after if event.physical_execution_id == process_id]
        assert len(appended) == 1 and appended[0] in detail.lifecycle_events
        warmed_counts, callback_counts, refreshed_counts = observed["read_counts"]
        assert callback_counts == warmed_counts
        assert refreshed_counts[0] == callback_counts[0] + 1
        assert refreshed_counts[1] - callback_counts[1] == observed["after_revision"]
    finally:
        runtime.shutdown(wait_for_runs=True)
