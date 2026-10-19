"""M183: administrative executor controls publish no authority before their CAS."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from oh_goon.core.errors import ExecutorNotFound, StateConflict
from oh_goon.core.models.fleet_executor import FleetExecutorIdentity
from oh_goon.runtime._executor_configuration import (
    ConfiguredFleetExecutor,
    ExecutorPoolConfiguration,
)
from oh_goon.runtime._executor_operations import ExecutorOperatorControls
from oh_goon.runtime.database import LocalDatabase
from oh_goon.runtime.executor_capacity import ExecutorCapacitySettings
from oh_goon.runtime.executor_pool import FleetExecutorPool
from tests.factories.path_authority import TEST_PATH_AUTHORITY


def _pool(database: LocalDatabase, generation: int) -> FleetExecutorPool:
    candidates = tuple(
        ConfiguredFleetExecutor(
            priority=0,
            backend=SimpleNamespace(capabilities=SimpleNamespace(backend_id=f"backend-{name}")),  # type: ignore[arg-type]
            capacity=ExecutorCapacitySettings(
                executor=FleetExecutorIdentity(executor_id=name, generation=generation),
                cpu_millis=1000,
                memory_mib=1024,
                max_processes=256,
            ),
        )
        for name in ("executor-a", "executor-b")
    )
    return FleetExecutorPool(
        database,
        ExecutorPoolConfiguration(
            placement_wait_seconds=0,
            placement_queue_limit=64,
            path_authority=TEST_PATH_AUTHORITY,
            executors=candidates,
        ),
    )


def _authority(database: LocalDatabase) -> tuple[tuple[str, int], ...]:
    with database.transaction() as connection:
        return tuple(
            (str(row["executor_id"]), int(row["generation"]))
            for row in connection.execute(
                "SELECT executor_id, generation FROM executor_configured_generation ORDER BY executor_id"
            )
        )


def _operation(database: LocalDatabase) -> tuple[tuple[str, str, str, int, int], ...]:
    with database.transaction() as connection:
        return tuple(
            (
                str(row["executor_id"]),
                str(row["admission_mode"]),
                str(row["trust_state"]),
                int(row["revision"]),
                int(row["trust_epoch"]),
            )
            for row in connection.execute(
                "SELECT executor_id, admission_mode, trust_state, revision, trust_epoch "
                "FROM executor_operational_state ORDER BY executor_id"
            )
        )


_MUTATIONS = (
    "drain_executor",
    "activate_executor",
    "revoke_executor_trust",
    "restore_executor_trust",
)


@pytest.mark.parametrize("method", _MUTATIONS)
@pytest.mark.parametrize(
    "executor_id,generation,revision,expected_error",
    [
        ("executor-a", 1, 0, StateConflict),  # old expected generation
        ("executor-a", 2, 55, StateConflict),  # stale operational revision
        ("executor-missing", 2, 0, ExecutorNotFound),
    ],
)
def test_rejected_admin_operations_cannot_publish_generations_or_state(
    tmp_path: Path,
    method: str,
    executor_id: str,
    generation: int,
    revision: int,
    expected_error: type[Exception],
) -> None:
    database = LocalDatabase(tmp_path / "controls.db")
    old = _pool(database, 1)
    old.executor_snapshot()  # publishes the original authoritative inventory
    upgraded = _pool(database, 2)
    before = (_authority(database), _operation(database))
    with pytest.raises(expected_error):
        getattr(upgraded, method)(
            executor_id, expected_generation=generation, expected_revision=revision
        )
    assert (_authority(database), _operation(database)) == before


@pytest.mark.parametrize("method", _MUTATIONS)
def test_accepted_admin_mutation_publishes_only_its_target_in_same_cut(
    tmp_path: Path,
    method: str,
) -> None:
    database = LocalDatabase(tmp_path / "accepted.db")
    old = _pool(database, 1)
    old.executor_snapshot()
    upgraded = _pool(database, 2)
    status = getattr(upgraded, method)("executor-a", expected_generation=2, expected_revision=0)
    assert status.generation == 2
    assert _authority(database) == (("executor-a", 2), ("executor-b", 1))
    with pytest.raises(StateConflict, match="generation regressed"):
        old.drain_executor(
            "executor-a",
            expected_generation=1,
            expected_revision=status.operational_revision,
        )
    assert _authority(database) == (("executor-a", 2), ("executor-b", 1))


def test_admin_rolls_back_generation_publication_when_status_write_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = LocalDatabase(tmp_path / "rollback.db")
    _pool(database, 1).executor_snapshot()
    upgraded = _pool(database, 2)
    before = (_authority(database), _operation(database))

    # Fail after the generation and operational writes but before commit.
    # No schema mutation is introduced: LocalDatabase correctly pins its schema.
    def deny_status_projection(*args: object, **kwargs: object) -> None:
        raise RuntimeError("status projection denied")

    monkeypatch.setattr(ExecutorOperatorControls, "_status_in", deny_status_projection)
    with pytest.raises(RuntimeError, match="status projection denied"):
        upgraded.drain_executor("executor-a", expected_generation=2, expected_revision=0)
    assert (_authority(database), _operation(database)) == before
