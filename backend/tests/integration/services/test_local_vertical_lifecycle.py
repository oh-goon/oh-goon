from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

from oh_goon.core.game_tape_projection import project_game_tape
from oh_goon.core.models.fleet_plan_evidence import FailedPlanStepExecution
from oh_goon.core.vocabulary import FleetRunStatus, GameTapeStatus, Verdict
from oh_goon.engine.git import GitService
from oh_goon.runtime.database import LocalDatabase
from oh_goon.runtime.effect_attempts import RepositoryEffectAttemptStore
from oh_goon.runtime.execution import ExecutionCoordinator
from oh_goon.runtime.journal import Journal
from tests.factories.local_lifecycle_execution import prepare_local_lifecycle_execution
from tests.factories.repository_effect_runtime import effect_recovery


def _script_plan(*, retry: str = "") -> str:
    return f"""version: 1
execution:
  purpose: verification
steps:
  - id: lifecycle
    run: "./lifecycle-step.py"
{retry}"""


def test_real_local_success_reconstructs_from_fresh_durable_owners(tmp_path: Path) -> None:
    execution = prepare_local_lifecycle_execution(
        tmp_path,
        plan_text=_script_plan(),
        script_body="print('vertical-success')",
    )

    live_detail = execution.execute()
    live_tape = project_game_tape(live_detail)
    reconstructed = execution.reconstructed_detail()
    reconstructed_tape = project_game_tape(reconstructed)

    assert live_detail.run.status is FleetRunStatus.COMPLETED
    assert len(live_detail.operations) == 1
    assert live_detail.operations[0].outcome.verdict is Verdict.READY
    step = live_detail.operations[0].evidence.plan.steps[0]
    assert step.stdout_evidence.byte_count == len(b"vertical-success")
    assert step.stdout_evidence.line_count == 1
    assert len(step.attempts) == 1
    assert step.attempts[0].returncode == 0
    assert len(execution.attempts.all()) == 0
    assert reconstructed == live_detail
    assert reconstructed_tape == live_tape
    assert reconstructed_tape.status is GameTapeStatus.PASSED


def test_real_local_step_failure_is_durable_and_projects_failed_game_tape(tmp_path: Path) -> None:
    execution = prepare_local_lifecycle_execution(
        tmp_path,
        plan_text=_script_plan(),
        script_body="import sys; print('vertical-failure'); sys.exit(23)",
        fleet_run_id="local-lifecycle-failure",
    )

    live_detail = execution.execute()
    reconstructed = execution.reconstructed_detail()
    operation = reconstructed.operations[0]
    step = operation.evidence.plan.steps[0]

    assert live_detail.run.status is FleetRunStatus.COMPLETED
    assert reconstructed == live_detail
    assert operation.outcome.verdict is Verdict.FAILED
    assert isinstance(step, FailedPlanStepExecution)
    assert step.returncode == 23
    assert step.attempts[-1].returncode == 23
    assert project_game_tape(reconstructed).status is GameTapeStatus.ATTENTION_REQUIRED
    assert len(execution.attempts.all()) == 0


def test_real_local_failed_attempt_can_be_rerun_by_fresh_admission(tmp_path: Path) -> None:
    parent = prepare_local_lifecycle_execution(
        tmp_path,
        plan_text=_script_plan(),
        script_body="import sys; print('attempt-1'); sys.exit(75)",
        fleet_run_id="local-lifecycle-retry-parent",
    )
    parent_detail = parent.execute()
    assert parent_detail.operations[0].outcome.verdict is Verdict.FAILED

    child = prepare_local_lifecycle_execution(
        tmp_path,
        plan_text=_script_plan(),
        script_body="print('attempt-2')",
        fleet_run_id="local-lifecycle-retry-child",
        rerun_from_run_id=parent.fleet_run_id,
    )
    child_detail = child.execute()
    reconstructed = child.reconstructed_detail()

    assert child_detail.run.status is FleetRunStatus.COMPLETED
    assert child_detail.execution.rerun_from_run_id == parent.fleet_run_id
    assert child_detail.operations[0].outcome.verdict is Verdict.READY
    assert reconstructed == child_detail
    retry_step = reconstructed.operations[0].evidence.plan.steps[0]
    assert retry_step.stdout_evidence.byte_count == len(b"attempt-2")
    assert retry_step.stdout_evidence.line_count == 1
    assert project_game_tape(reconstructed).status is GameTapeStatus.PASSED
    assert len(child.attempts.all()) == 0


def test_real_local_cancellation_stops_subprocess_and_reconstructs_terminal_fact(
    tmp_path: Path,
) -> None:
    started = tmp_path / "repository" / "started.txt"
    code = (
        "from pathlib import Path; import time; "
        "Path('started.txt').write_text('started'); "
        "print('before-cancel', flush=True); time.sleep(30)"
    )
    execution = prepare_local_lifecycle_execution(
        tmp_path,
        plan_text=_script_plan(),
        script_body=code,
        fleet_run_id="local-lifecycle-cancel",
    )

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(execution.execute)
        deadline = time.monotonic() + 10
        while not started.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert started.exists(), "real subprocess never reached its observable workload"
        execution.lifecycle.cancel(execution.fleet_run_id)
        live_detail = future.result(timeout=10)

    reconstructed = execution.reconstructed_detail()
    assert live_detail.run.status is FleetRunStatus.CANCELLED
    assert reconstructed == live_detail
    assert project_game_tape(reconstructed).status is GameTapeStatus.CANCELLED
    unresolved = execution.attempts.all()
    assert len(unresolved) == 1
    assert unresolved[0].status.value == "OUTCOME_UNKNOWN"


class _RestartLeaseEnvironment:
    def __init__(self) -> None:
        self.alive = True
        self.pid = 424242

    def now(self):
        return datetime(2026, 9, 11, tzinfo=UTC)

    def process_id(self) -> int:
        return self.pid

    def process_alive(self, pid: int) -> bool:
        return self.alive and pid == self.pid

    def process_identity(self, pid: int) -> str | None:
        return f"restart-proof-{pid}" if self.process_alive(pid) else None


def test_process_restart_recovery_terminalizes_orphan_from_durable_truth(tmp_path: Path) -> None:
    environment = _RestartLeaseEnvironment()
    execution = prepare_local_lifecycle_execution(
        tmp_path,
        plan_text=_script_plan(),
        script_body="print('should-not-run')",
        fleet_run_id="local-lifecycle-restart",
        lease_environment=environment,
    )
    session = execution.lifecycle.activate(execution.ticket)
    execution.lifecycle.begin(session)
    assert (
        execution.journal.read_view().required_run_detail(execution.fleet_run_id).run.status
        is FleetRunStatus.RUNNING
    )

    environment.alive = False
    database = LocalDatabase(execution.database_path)
    journal = Journal(database)
    attempts = RepositoryEffectAttemptStore(database)
    recovered_execution = ExecutionCoordinator(database, lease_environment=environment)
    effect_recovery(database, attempts, journal, recovered_execution, GitService()).recover()

    recovered = journal.read_view().required_run_detail(execution.fleet_run_id)
    assert recovered.run.status is FleetRunStatus.FAILED
    assert project_game_tape(recovered).status is GameTapeStatus.INTERRUPTED
    assert recovered.run.failure is not None
    assert recovered.run.failure.stage.value == "RECOVERY"
    assert recovered_execution.active_lease() is None
