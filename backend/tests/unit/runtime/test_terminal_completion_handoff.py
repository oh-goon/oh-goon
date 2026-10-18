from __future__ import annotations

import threading
from pathlib import Path

from oh_goon.core.models.operation import OperationExecution
from oh_goon.core.models.run import FleetRunStatus
from oh_goon.core.vocabulary import OperationAction
from oh_goon.runtime.database import LocalDatabase
from tests.factories.local_state import local_state
from tests.factories.operation_reports import make_operation_report
from tests.factories.runtime_stack import fleet_stack


class _BlockingRunner:
    def __init__(self) -> None:
        self.entered = threading.Event()
        self.release = threading.Event()

    def run(
        self,
        repository: Path,
        action: OperationAction,
        *,
        fleet_run_id: str | None = None,
        cancel_requested: object = None,
    ) -> OperationExecution:
        self.entered.set()
        assert self.release.wait(timeout=2)
        return OperationExecution(
            stdout="",
            stderr="",
            report=make_operation_report(repository, action, fleet_run_id=fleet_run_id),
        )


def _repository(root: Path) -> None:
    root.mkdir(parents=True)
    (root / "pyproject.toml").write_text("[project]\nname='x'\nversion='0.1.0'\n")
    (root / ".git").mkdir()


def test_local_wait_reconstructs_terminal_truth_from_fresh_sqlite_after_worker_retires(
    tmp_path: Path, monkeypatch,
) -> None:
    repositories = tmp_path / "repos"
    _repository(repositories / "a")
    database = LocalDatabase(tmp_path / "state.db")
    state = local_state(database)
    state.add_location(repositories)
    runner = _BlockingRunner()
    _, manager, _, _ = fleet_stack(state, runner)

    started = manager.start(OperationAction.INIT)
    assert runner.entered.wait(timeout=2)

    original_connect = database._connect
    reconnects = 0

    def observe_fresh_connect():
        nonlocal reconnects
        reconnects += 1
        return original_connect()

    monkeypatch.setattr(database, "_connect", observe_fresh_connect)
    runner.release.set()
    detail = manager.wait_for_terminal(started.fleet_run_id, timeout=2)

    assert detail.run.status is FleetRunStatus.COMPLETED
    assert len(detail.operations) == 1
    assert reconnects == 1
    manager.shutdown()
