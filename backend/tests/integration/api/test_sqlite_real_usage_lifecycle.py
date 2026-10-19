from __future__ import annotations

import hashlib
import sqlite3
import threading
import time
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace

import pytest

import oh_goon.cli_runtime_plans as plans
from oh_goon.core.models.operation import OperationExecution
from oh_goon.core.models.run import FleetRunDetail, FleetRunOverview
from oh_goon.core.models.run_events import FleetRunEvent
from oh_goon.core.vocabulary import FleetRunStatus, OperationAction
from oh_goon.runtime.database import LocalDatabase
from oh_goon.runtime.state import LocalState
from oh_goon.services.fleet_run_control_plane import FleetRunControlPlane
from tests.factories.local_state import local_state
from tests.factories.operation_reports import make_operation_report
from tests.factories.runtime_stack import fleet_stack


class _BlockingReadyRunner:
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
        assert self.release.wait(timeout=5)
        return OperationExecution(
            stdout="ready",
            stderr="",
            report=make_operation_report(repository, action, fleet_run_id=fleet_run_id),
        )


class _ShortReadyRunner:
    def run(
        self,
        repository: Path,
        action: OperationAction,
        *,
        fleet_run_id: str | None = None,
        cancel_requested: object = None,
    ) -> OperationExecution:
        time.sleep(0.003)
        return OperationExecution(
            stdout="ready",
            stderr="",
            report=make_operation_report(repository, action, fleet_run_id=fleet_run_id),
        )


def _seed_repository(root: Path, name: str = "repo") -> Path:
    repository = root / name
    repository.mkdir(parents=True)
    (repository / ".git").mkdir()
    (repository / "pyproject.toml").write_text(
        f"[project]\nname='{name}'\nversion='0.1.0'\n",
        encoding="utf-8",
    )
    return repository


def _stack(database_path: Path, runner: object):
    database = LocalDatabase(database_path)
    state = local_state(database)
    fleet, manager, _, _ = fleet_stack(state, runner)
    return database, state, fleet, manager


def _assert_sqlite_is_healthy(database_path: Path) -> None:
    with closing(sqlite3.connect(database_path)) as connection, connection:
        assert connection.execute("PRAGMA quick_check").fetchone() == ("ok",)
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


class _RealUsagePressure:
    def __init__(self, state: LocalState, pressure_file: Path) -> None:
        self._state = state
        self._pressure_file = pressure_file
        self._stop = threading.Event()
        self.errors: list[BaseException] = []
        self._threads = (
            threading.Thread(target=self._read_loop, name="sqlite-real-reader"),
            threading.Thread(target=self._hash_loop, name="sqlite-real-hash"),
        )

    def start(self) -> None:
        for thread in self._threads:
            thread.start()

    def close(self) -> None:
        self._stop.set()
        for thread in self._threads:
            thread.join(timeout=2)

    def _read_loop(self) -> None:
        try:
            while not self._stop.is_set():
                self._state.journal.read_view().run_overview()
                time.sleep(0.001)
        except BaseException as exc:  # pragma: no cover - surfaced by the scenario
            self.errors.append(exc)

    def _hash_loop(self) -> None:
        try:
            while not self._stop.is_set():
                hashlib.sha256(self._pressure_file.read_bytes()).digest()
        except BaseException as exc:  # pragma: no cover - surfaced by the scenario
            self.errors.append(exc)


def _wait_through_real_progress(
    manager: FleetRunControlPlane,
    runner: _BlockingReadyRunner,
    run_id: str,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[FleetRunDetail, tuple[FleetRunEvent, ...], int]:
    monkeypatch.setattr(plans, "_PROGRESS_WAIT_SLICE_SECONDS", 0.002)
    monkeypatch.setattr(plans, "_PROGRESS_HEARTBEAT_SECONDS", 999.0)
    original_transient = manager.transient_run_activity
    slices = 0

    def transient(current_run_id: str, *, after: int = 0):
        nonlocal slices
        slices += 1
        if slices == 30:
            runner.release.set()
        return original_transient(current_run_id, after=after)

    monkeypatch.setattr(manager, "transient_run_activity", transient)
    runtime = SimpleNamespace(runs=manager)
    admitted = SimpleNamespace(target=SimpleNamespace(plan=SimpleNamespace(imports=())))
    detail = plans._wait_for_terminal_with_progress(runtime, run_id, admitted_run=admitted)
    return detail, manager.events(run_id).events, slices


def _restart_and_read(
    database_path: Path, run_id: str
) -> tuple[FleetRunDetail, tuple[FleetRunEvent, ...], FleetRunOverview]:
    _, _, _, restarted = _stack(database_path, _ShortReadyRunner())
    try:
        return restarted.get(run_id), restarted.events(run_id).events, restarted.overview()
    finally:
        restarted.shutdown()


def test_active_wait_terminal_handoff_and_restart_share_one_real_sqlite_lifecycle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Prove active wait, terminal handoff, teardown, restart, and durable reread together."""

    repositories = tmp_path / "repos"
    _seed_repository(repositories)
    database_path = tmp_path / "state.sqlite3"
    runner = _BlockingReadyRunner()
    _, state, _, manager = _stack(database_path, runner)
    state.add_location(repositories)
    started = manager.start(OperationAction.INIT, fleet_run_id="sqlite-real-lifecycle")
    assert runner.entered.wait(timeout=2)

    pressure_file = tmp_path / "immutable-input.bin"
    pressure_file.write_bytes(b"sqlite-real-usage" * 131_072)
    pressure = _RealUsagePressure(state, pressure_file)
    pressure.start()
    try:
        local_detail, events_before, slices = _wait_through_real_progress(
            manager, runner, started.fleet_run_id, monkeypatch
        )
    finally:
        runner.release.set()
        pressure.close()

    assert pressure.errors == []
    assert slices >= 30
    assert local_detail.run.status is FleetRunStatus.COMPLETED
    assert len(local_detail.operations) == 1
    assert events_before[-1].event_type.value == "RUN_COMPLETED"
    operation_ids = tuple(row.provenance.operation_id for row in local_detail.operations)
    manager.shutdown()

    durable_detail, events_after, overview = _restart_and_read(database_path, started.fleet_run_id)
    assert durable_detail.run.status is FleetRunStatus.COMPLETED
    assert tuple(row.provenance.operation_id for row in durable_detail.operations) == operation_ids
    assert events_after == events_before
    assert (overview.running, overview.queued, overview.cancelling) == (0, 0, 0)
    assert (overview.total, overview.successful) == (1, 1)
    _assert_sqlite_is_healthy(database_path)


def test_repeated_real_runs_and_runtime_restarts_leave_sqlite_authority_healthy(
    tmp_path: Path,
) -> None:
    """Model repeated developer/certification use instead of a one-shot database fixture."""

    repositories = tmp_path / "repos"
    _seed_repository(repositories)
    database_path = tmp_path / "state.sqlite3"
    expected_run_ids: list[str] = []

    database, state, _, manager = _stack(database_path, _ShortReadyRunner())
    state.add_location(repositories)
    try:
        for index in range(12):
            run_id = f"sqlite-real-repeat-{index:02d}"
            expected_run_ids.append(run_id)
            started = manager.start(OperationAction.INIT, fleet_run_id=run_id)
            detail = manager.wait_for_terminal(started.fleet_run_id, timeout=2)
            assert detail.run.status is FleetRunStatus.COMPLETED
            assert manager.events(run_id).events[-1].event_type.value == "RUN_COMPLETED"

            # Real usage repeatedly tears down and recreates application/runtime ownership.
            if index in {3, 7}:
                manager.shutdown()
                database, state, _, manager = _stack(database_path, _ShortReadyRunner())

            # Fresh read surfaces are exercised throughout, not only at the very end.
            assert manager.get(run_id).run.status is FleetRunStatus.COMPLETED
            _assert_sqlite_is_healthy(database_path)
    finally:
        manager.shutdown()

    _, _, _, final_reader = _stack(database_path, _ShortReadyRunner())
    try:
        history_ids = {row.run.fleet_run_id for row in final_reader.history(limit=50).items}
        assert set(expected_run_ids) <= history_ids
        overview = final_reader.overview()
        assert overview.running == 0
        assert overview.queued == 0
        assert overview.cancelling == 0
    finally:
        final_reader.shutdown()
    _assert_sqlite_is_healthy(database_path)
