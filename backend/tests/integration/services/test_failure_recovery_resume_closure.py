from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from oh_goon.core.game_tape_projection import project_game_tape
from oh_goon.core.models.run_events import (
    ReconciliationCompletedEvent,
    ReconciliationRequiredEvent,
    ReconciliationStartedEvent,
)
from oh_goon.core.vocabulary import FleetRunStatus, GameTapeEventType, GameTapeStatus, Verdict
from oh_goon.engine.git import GitService
from oh_goon.runtime.database import LocalDatabase
from oh_goon.runtime.effect_attempts import RepositoryEffectAttemptStore
from oh_goon.runtime.execution import ExecutionCoordinator
from oh_goon.runtime.journal import Journal
from tests.factories.local_lifecycle_execution import prepare_local_lifecycle_execution
from tests.factories.repository_effect_runtime import effect_recovery


def _script_plan() -> str:
    return '''version: 1
execution:
  purpose: verification
steps:
  - id: recovery
    run: "./lifecycle-step.py"
'''


class _RestartLeaseEnvironment:
    def __init__(self, pid: int = 515151) -> None:
        self.alive = True
        self.pid = pid

    def now(self):
        return datetime(2026, 9, 11, tzinfo=UTC)

    def process_id(self) -> int:
        return self.pid

    def process_alive(self, pid: int) -> bool:
        return self.alive and pid == self.pid

    def process_identity(self, pid: int) -> str | None:
        return f"failure-recovery-{pid}" if self.process_alive(pid) else None


def _recover(execution, environment: _RestartLeaseEnvironment):
    database = LocalDatabase(execution.database_path)
    journal = Journal(database)
    attempts = RepositoryEffectAttemptStore(database)
    coordinator = ExecutionCoordinator(database, lease_environment=environment)
    recovery = effect_recovery(database, attempts, journal, coordinator, GitService())
    recovery.recover()
    return database, journal, attempts, coordinator, recovery


def test_restart_after_admission_before_step_terminalizes_once_from_durable_truth(
    tmp_path: Path,
) -> None:
    environment = _RestartLeaseEnvironment()
    execution = prepare_local_lifecycle_execution(
        tmp_path,
        plan_text=_script_plan(),
        script_body="raise AssertionError('must not execute')",
        fleet_run_id="recovery-before-step",
        lease_environment=environment,
    )
    admitted = execution.journal.read_view().required_run_detail(execution.fleet_run_id)
    assert admitted.run.status is FleetRunStatus.QUEUED

    environment.alive = False
    _database, journal, attempts, coordinator, recovery = _recover(execution, environment)
    recovered = journal.read_view().required_run_detail(execution.fleet_run_id)
    revision = journal.read_view().revision

    assert recovered.run.status is FleetRunStatus.FAILED
    assert recovered.run.failure is not None
    assert recovered.run.failure.code == "RUN_INTERRUPTED_BY_RESTART"
    assert recovered.operations == ()
    assert attempts.all() == []
    assert coordinator.active_lease() is None
    tape = project_game_tape(recovered)
    assert tape.status is GameTapeStatus.INTERRUPTED
    reconciliation = [
        event
        for event in recovered.lifecycle_events
        if isinstance(
            event,
            (ReconciliationRequiredEvent, ReconciliationStartedEvent, ReconciliationCompletedEvent),
        )
    ]
    assert [type(event) for event in reconciliation] == [
        ReconciliationRequiredEvent,
        ReconciliationStartedEvent,
        ReconciliationCompletedEvent,
    ]
    assert [event.sequence for event in reconciliation] == sorted(
        event.sequence for event in reconciliation
    )
    tape_types = [event.event_type for event in tape.play_by_play]
    assert GameTapeEventType.RECONCILIATION_REQUIRED in tape_types
    assert GameTapeEventType.RECONCILIATION_STARTED in tape_types
    assert GameTapeEventType.RECONCILIATION_COMPLETED in tape_types

    recovery.recover()
    assert journal.read_view().revision == revision
    assert journal.read_view().required_run_detail(execution.fleet_run_id) == recovered


def test_restart_after_completed_real_operation_before_terminal_publication_reconstructs_complete(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    environment = _RestartLeaseEnvironment(pid=525252)
    execution = prepare_local_lifecycle_execution(
        tmp_path,
        plan_text=_script_plan(),
        script_body=(
            "from pathlib import Path; "
            "p=Path('execution-count.txt'); "
            "n=int(p.read_text()) if p.exists() else 0; "
            "p.write_text(str(n+1)); print('completed-before-terminal')"
        ),
        fleet_run_id="recovery-after-operation",
        lease_environment=environment,
    )

    def crash_before_terminal(*_args, **_kwargs):
        raise RuntimeError("simulated process loss before terminal publication")

    monkeypatch.setattr(execution.lifecycle, "finish_detail", crash_before_terminal)
    with pytest.raises(RuntimeError, match="before terminal publication"):
        execution.execute()

    live = execution.journal.read_view().required_run_detail(execution.fleet_run_id)
    assert live.run.status is FleetRunStatus.RUNNING
    assert len(live.operations) == 1
    assert live.operations[0].outcome.verdict is Verdict.READY
    assert (execution.repository.path / "execution-count.txt").read_text() == "1"

    environment.alive = False
    _database, journal, attempts, coordinator, recovery = _recover(execution, environment)
    recovered = journal.read_view().required_run_detail(execution.fleet_run_id)
    revision = journal.read_view().revision

    assert recovered.run.status is FleetRunStatus.COMPLETED
    assert len(recovered.operations) == 1
    assert attempts.all() == []
    assert coordinator.active_lease() is None
    assert (execution.repository.path / "execution-count.txt").read_text() == "1"
    assert project_game_tape(recovered).status is GameTapeStatus.PASSED

    recovery.recover()
    assert journal.read_view().revision == revision
    assert (execution.repository.path / "execution-count.txt").read_text() == "1"


def test_interrupted_parent_can_only_continue_as_fresh_rerun_with_explicit_lineage(
    tmp_path: Path,
) -> None:
    environment = _RestartLeaseEnvironment(pid=535353)
    parent = prepare_local_lifecycle_execution(
        tmp_path,
        plan_text=_script_plan(),
        script_body="raise AssertionError('parent must not execute')",
        fleet_run_id="recovery-rerun-parent",
        lease_environment=environment,
    )
    session = parent.lifecycle.activate(parent.ticket)
    parent.lifecycle.begin(session)
    environment.alive = False
    _database, journal, _attempts, _coordinator, _recovery_service = _recover(parent, environment)
    parent_detail = journal.read_view().required_run_detail(parent.fleet_run_id)
    assert parent_detail.run.status is FleetRunStatus.FAILED

    child = prepare_local_lifecycle_execution(
        tmp_path,
        plan_text=_script_plan(),
        script_body="print('fresh-rerun-after-restart')",
        fleet_run_id="recovery-rerun-child",
        rerun_from_run_id=parent.fleet_run_id,
    )
    child_detail = child.execute()

    assert child_detail.run.status is FleetRunStatus.COMPLETED
    assert child_detail.execution.rerun_from_run_id == parent.fleet_run_id
    assert child_detail.run.fleet_run_id != parent_detail.run.fleet_run_id
    assert child_detail.operations[0].outcome.verdict is Verdict.READY
    assert project_game_tape(child.reconstructed_detail()).status is GameTapeStatus.PASSED
