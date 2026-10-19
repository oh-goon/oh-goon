from __future__ import annotations

from pathlib import Path

from oh_goon.core.vocabulary import FleetRunEventType, GameTapeEventType
from tests.factories.local_lifecycle_execution import prepare_local_lifecycle_execution


def test_live_step_lifecycle_is_durable_and_game_tape_reads_same_journal(tmp_path: Path) -> None:
    execution = prepare_local_lifecycle_execution(
        tmp_path,
        plan_text="""version: 1
execution:
  purpose: verification
steps:
  - id: observed
    run: "./lifecycle-step.py"
""",
        script_body="print('observed')",
        fleet_run_id="canonical-lifecycle-ledger",
    )

    execution.execute()
    reconstructed = execution.reconstructed_detail()
    batch = execution.journal.run_events(execution.fleet_run_id)
    event_types = [event.event_type for event in batch.events]

    assert FleetRunEventType.REPOSITORY_STARTED in event_types
    assert FleetRunEventType.STEP_REACHED in event_types
    assert FleetRunEventType.STEP_STARTED in event_types
    assert FleetRunEventType.STEP_ATTEMPT_CREATED in event_types
    assert FleetRunEventType.PROCESS_STARTING in event_types
    assert FleetRunEventType.PROCESS_STARTED in event_types
    assert FleetRunEventType.PROCESS_EXITED in event_types
    assert FleetRunEventType.DESCENDANTS_QUIESCENT in event_types
    assert FleetRunEventType.STEP_SUCCEEDED in event_types
    assert event_types.index(FleetRunEventType.REPOSITORY_STARTED) < event_types.index(
        FleetRunEventType.STEP_REACHED
    )
    assert event_types.index(FleetRunEventType.STEP_REACHED) < event_types.index(
        FleetRunEventType.STEP_STARTED
    )
    assert event_types.index(FleetRunEventType.STEP_STARTED) < event_types.index(
        FleetRunEventType.STEP_ATTEMPT_CREATED
    )
    assert event_types.index(FleetRunEventType.STEP_ATTEMPT_CREATED) < event_types.index(
        FleetRunEventType.PROCESS_STARTING
    )
    assert event_types.index(FleetRunEventType.PROCESS_STARTING) < event_types.index(
        FleetRunEventType.PROCESS_STARTED
    )
    assert event_types.index(FleetRunEventType.PROCESS_STARTED) < event_types.index(
        FleetRunEventType.PROCESS_EXITED
    )
    assert event_types.index(FleetRunEventType.PROCESS_EXITED) < event_types.index(
        FleetRunEventType.DESCENDANTS_QUIESCENT
    )
    assert event_types.index(FleetRunEventType.DESCENDANTS_QUIESCENT) < event_types.index(
        FleetRunEventType.STEP_SUCCEEDED
    )
    assert event_types.index(FleetRunEventType.STEP_SUCCEEDED) < event_types.index(
        FleetRunEventType.REPOSITORY_COMPLETED
    )

    tape_types = [event.event_type for event in reconstructed.game_tape.play_by_play]
    assert tape_types.count(GameTapeEventType.REPOSITORY_STARTED) == 1
    assert tape_types.count(GameTapeEventType.STEP_REACHED) == 1
    assert tape_types.count(GameTapeEventType.STEP_STARTED) == 1
    assert tape_types.count(GameTapeEventType.STEP_ATTEMPT_CREATED) == 1
    assert tape_types.count(GameTapeEventType.PROCESS_STARTING) == 1
    assert tape_types.count(GameTapeEventType.PROCESS_STARTED) == 1
    assert tape_types.count(GameTapeEventType.PROCESS_EXITED) == 1
    assert tape_types.count(GameTapeEventType.DESCENDANTS_QUIESCENT) == 1
    assert tape_types.count(GameTapeEventType.STEP_PASSED) == 1
    attempt_event = next(
        event
        for event in reconstructed.game_tape.play_by_play
        if event.event_type is GameTapeEventType.STEP_ATTEMPT_CREATED
    )
    assert attempt_event.attempt_number == 1
    assert attempt_event.step_attempt_id is not None
    assert attempt_event.authority_sequence > 0
    process_started = next(
        event
        for event in reconstructed.game_tape.play_by_play
        if event.event_type is GameTapeEventType.PROCESS_STARTED
    )
    assert process_started.step_attempt_id == attempt_event.step_attempt_id
    assert process_started.physical_execution_id is not None
    assert process_started.pid is not None
    process_exited = next(
        event
        for event in reconstructed.game_tape.play_by_play
        if event.event_type is GameTapeEventType.PROCESS_EXITED
    )
    assert process_exited.physical_execution_id == process_started.physical_execution_id
    assert process_exited.pid == process_started.pid
    assert process_exited.returncode == 0


def test_step_attempt_is_durable_while_step_is_still_running(tmp_path: Path) -> None:
    import time
    from concurrent.futures import ThreadPoolExecutor

    execution = prepare_local_lifecycle_execution(
        tmp_path,
        plan_text="""version: 1
execution:
  purpose: verification
steps:
  - id: interruptible
    run: "./lifecycle-step.py"
""",
        script_body="import time; time.sleep(2); print('done')",
        fleet_run_id="live-lifecycle-before-result",
    )

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(execution.execute)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            event_types = [
                event.event_type
                for event in execution.journal.run_events(execution.fleet_run_id).events
            ]
            if FleetRunEventType.PROCESS_STARTED in event_types:
                break
            time.sleep(0.02)
        else:
            raise AssertionError(
                "PROCESS_STARTED was not durably observable while execution was live"
            )

        assert FleetRunEventType.REPOSITORY_COMPLETED not in event_types
        live_tape = execution.reconstructed_detail().game_tape
        assert live_tape.repositories[0].status.value == "RUNNING"
        assert live_tape.repositories[0].steps[0].status.value == "RUNNING"
        assert not future.done()
        future.result(timeout=5)
