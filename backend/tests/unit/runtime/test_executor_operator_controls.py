from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from oh_goon.core.models.fleet_executor import FleetExecutorIdentity
from oh_goon.runtime._executor_configuration import (
    ConfiguredFleetExecutor,
    ExecutorPoolConfiguration,
)
from oh_goon.runtime._executor_operations import ExecutorOperatorControls
from oh_goon.runtime.database import LocalDatabase
from oh_goon.runtime.executor_capacity import ExecutorCapacitySettings
from tests.factories.path_authority import TEST_PATH_AUTHORITY


def _controls(database: LocalDatabase, *, generation: int = 1) -> ExecutorOperatorControls:
    candidate = ConfiguredFleetExecutor(
        priority=0,
        backend=SimpleNamespace(capabilities=SimpleNamespace(backend_id="backend-a")),  # type: ignore[arg-type]
        capacity=ExecutorCapacitySettings(
            executor=FleetExecutorIdentity(executor_id="executor-a", generation=generation),
            cpu_millis=1000,
            memory_mib=1024,
            max_processes=256,
        ),
    )
    return ExecutorOperatorControls(
        database,
        ExecutorPoolConfiguration(
            placement_wait_seconds=0,
            placement_queue_limit=64,
            path_authority=TEST_PATH_AUTHORITY,
            executors=(candidate,),
        ),
    )


def _lose_coordination_write_response(database: LocalDatabase, *, supersede: bool = False) -> None:
    original = database.transaction
    lost = False

    @contextmanager
    def ambiguous_transaction(*, write: bool = False, track_revision: bool = True):
        nonlocal lost
        with original(write=write, track_revision=track_revision) as connection:
            yield connection
        if write and not track_revision and not lost:
            lost = True
            if supersede:
                with original(write=True, track_revision=False) as connection:
                    connection.execute(
                        "UPDATE executor_operational_state "
                        "SET admission_mode = 'active', revision = 2 WHERE executor_id = 'executor-a'"
                    )
            raise RuntimeError("executor operator commit response lost after commit")

    database.transaction = ambiguous_transaction  # type: ignore[method-assign]


def test_operator_state_replays_exact_status_after_ambiguous_commit_response(
    tmp_path: Path,
) -> None:
    database = LocalDatabase(tmp_path / "replay.db")
    controls = _controls(database)
    seen = controls.snapshot().executors[0]
    _lose_coordination_write_response(database)

    status = controls.drain(
        "executor-a",
        expected_generation=seen.generation,
        expected_revision=seen.operational_revision,
    )

    assert status.admission_mode.value == "draining"
    assert status.operational_revision == 1
    assert controls.snapshot().executors[0] == status


def test_operator_state_does_not_replay_after_later_conflicting_revision(tmp_path: Path) -> None:
    database = LocalDatabase(tmp_path / "conflict.db")
    controls = _controls(database)
    seen = controls.snapshot().executors[0]
    _lose_coordination_write_response(database, supersede=True)

    with pytest.raises(RuntimeError, match="commit response lost"):
        controls.drain(
            "executor-a",
            expected_generation=seen.generation,
            expected_revision=seen.operational_revision,
        )

    status = controls.snapshot().executors[0]
    assert status.admission_mode.value == "active"
    assert status.operational_revision == 2


def test_operator_state_preserves_failure_before_commit_boundary(tmp_path: Path) -> None:
    database = LocalDatabase(tmp_path / "precommit.db")
    controls = _controls(database)
    seen = controls.snapshot().executors[0]
    original = database.transaction

    @contextmanager
    def failing_transaction(*, write: bool = False, track_revision: bool = True):
        if write and not track_revision:
            raise RuntimeError("operator write failed before commit boundary")
        with original(write=write, track_revision=track_revision) as connection:
            yield connection

    database.transaction = failing_transaction  # type: ignore[method-assign]
    with pytest.raises(RuntimeError, match="before commit boundary"):
        controls.drain(
            "executor-a",
            expected_generation=seen.generation,
            expected_revision=seen.operational_revision,
        )

    status = controls.snapshot().executors[0]
    assert status.admission_mode.value == "active"
    assert status.operational_revision == 0


def test_executor_generation_registry_fences_stale_runtime_after_replacement(
    tmp_path: Path,
) -> None:
    database = LocalDatabase(tmp_path / "generation-fence.db")
    original = _controls(database, generation=1)
    replacement = _controls(database, generation=2)

    with database.transaction() as connection:
        replacement.require_current_generation_in(connection, "executor-a", 2)
        with pytest.raises(Exception, match="generation changed"):
            original.require_current_generation_in(connection, "executor-a", 1)

    with pytest.raises(Exception, match="generation regressed"):
        _controls(database, generation=1)
