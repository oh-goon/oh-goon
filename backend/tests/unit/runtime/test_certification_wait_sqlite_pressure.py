from __future__ import annotations

import hashlib
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

import oh_goon.cli_runtime_plans as plans
from oh_goon.core.models.operation import OperationExecution
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
        report = make_operation_report(repository, action, fleet_run_id=fleet_run_id)
        return OperationExecution(stdout="", stderr="", report=report)


def _repository(root: Path) -> None:
    repository = root / "repo"
    repository.mkdir(parents=True)
    (repository / "pyproject.toml").write_text(
        "[project]\nname='wait-pressure'\nversion='0.1.0'\n",
        encoding="utf-8",
    )
    (repository / ".git").mkdir()


def test_real_wait_progress_avoids_sqlite_while_active_then_reconstructs_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Model certification's live worker + --wait consumer + unrelated filesystem I/O.

    The foreground progress loop must remain process-local while the supervisor owns
    the worker. Once local liveness retires, terminal truth is reconstructed exactly once
    from the durable Journal; successful FleetRunDetail state must not be retained as a
    competing supervisor completion mailbox.
    """

    root = tmp_path / "repos"
    _repository(root)
    database = LocalDatabase(tmp_path / "certification-wait.db")
    state = local_state(database)
    state.add_location(root)
    runner = _BlockingRunner()
    _, manager, _, _ = fleet_stack(state, runner)
    started = manager.start(OperationAction.INIT, fleet_run_id="fleet-certification-wait")
    assert runner.entered.wait(timeout=1)

    main_thread = threading.current_thread()
    original_connect = database._connect
    active_foreground_connects = 0
    inactive_foreground_connects = 0

    def counted_connect():
        nonlocal active_foreground_connects, inactive_foreground_connects
        if threading.current_thread() is main_thread:
            if manager._supervisor.is_active(started.fleet_run_id):  # ownership regression
                active_foreground_connects += 1
            else:
                inactive_foreground_connects += 1
        return original_connect()

    monkeypatch.setattr(database, "_connect", counted_connect)
    monkeypatch.setattr(plans, "_PROGRESS_WAIT_SLICE_SECONDS", 0.002)
    monkeypatch.setattr(plans, "_PROGRESS_HEARTBEAT_SECONDS", 999.0)

    # Keep a separate filesystem reader busy while the foreground wait loop runs. This
    # deliberately does not mutate the DB; it models immutable-input hash/copy pressure.
    pressure_file = tmp_path / "immutable-input.bin"
    pressure_file.write_bytes(b"oh-goon-pressure" * 131_072)
    stop_pressure = threading.Event()
    pressure_started = threading.Event()

    def pressure() -> None:
        pressure_started.set()
        while not stop_pressure.is_set():
            hashlib.sha256(pressure_file.read_bytes()).digest()

    pressure_thread = threading.Thread(target=pressure, name="immutable-input-pressure")
    pressure_thread.start()
    assert pressure_started.wait(timeout=1)

    original_transient = manager.transient_run_activity
    slices = 0

    def transient(run_id: str, *, after: int = 0):
        nonlocal slices
        slices += 1
        if slices == 40:
            runner.release.set()
        return original_transient(run_id, after=after)

    monkeypatch.setattr(manager, "transient_run_activity", transient)
    runtime = SimpleNamespace(runs=manager)
    admitted = SimpleNamespace(target=SimpleNamespace(plan=SimpleNamespace(imports=())))
    try:
        detail = plans._wait_for_terminal_with_progress(
            runtime,
            started.fleet_run_id,
            admitted_run=admitted,
        )
    finally:
        runner.release.set()
        stop_pressure.set()
        pressure_thread.join(timeout=2)

    assert detail.run.status.terminal
    assert slices >= 40
    assert active_foreground_connects == 0
    assert inactive_foreground_connects == 1  # one Journal reconstruction after liveness retires
