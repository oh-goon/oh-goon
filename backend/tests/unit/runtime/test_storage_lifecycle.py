from __future__ import annotations

import errno
import os
import select
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from oh_goon.core.errors import ExecutionBusy
from oh_goon.core.path_layouts import ApplicationStatePathLayout
from oh_goon.runtime.database import LocalDatabase
from oh_goon.runtime.execution.sqlite_lease import SQLiteExecutionLeaseStore
from oh_goon.runtime.storage_lifecycle import (
    measure_storage,
    prune_generic_storage,
    storage_inventory,
)


def _environment(tmp_path: Path) -> dict[str, str]:
    return {
        "XDG_STATE_HOME": str(tmp_path / "state"),
        "XDG_DATA_HOME": str(tmp_path / "data"),
        "XDG_CACHE_HOME": str(tmp_path / "cache"),
    }


def test_storage_inventory_keeps_certification_journal_without_dependency_supply_owner(
    tmp_path: Path,
) -> None:
    items = {item.storage_id: item for item in storage_inventory(_environment(tmp_path))}

    assert not any(item.storage_id == "certification.control-state" for item in items.values())
    assert "certification.sealed-supply" not in items
    assert "certification.acquisition-cache" not in items
    assert items["configuration.league-folders"].durability == "durable-user-configuration"
    assert not items["configuration.shared-plans"].generic_prune
    assert not items["configuration.lineups"].generic_prune
    assert items["self-certification.evidence"].durability == "durable-verification-evidence"
    assert not items["self-certification.evidence"].generic_prune


def test_measure_storage_does_not_follow_symlinked_directory(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret").write_bytes(b"x" * 100)
    root = tmp_path / "root"
    root.mkdir()
    (root / "link").symlink_to(outside, target_is_directory=True)

    measured = measure_storage(root)

    assert measured is not None
    assert measured.entries == 2
    assert measured.bytes < 100 + root.lstat().st_size + outside.lstat().st_size


@pytest.mark.parametrize("boundary", ["metadata", "directory"])
def test_measure_storage_refuses_incomplete_usage_instead_of_reporting_a_partial_total(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, boundary: str
) -> None:
    root = tmp_path / "usage"
    blocked = root / "unavailable"
    blocked.mkdir(parents=True)
    (blocked / "payload").write_bytes(b"retained model" * 100)
    native_stat = Path.lstat
    native_scandir = os.scandir

    def metadata(path: Path) -> os.stat_result:
        if boundary == "metadata" and path == root:
            raise PermissionError(errno.EACCES, "injected metadata denial", str(path))
        return native_stat(path)

    def directory(path: str | os.PathLike[str] | int) -> Any:
        if boundary == "directory" and not isinstance(path, int) and Path(path) == blocked:
            raise PermissionError(errno.EACCES, "injected directory denial", str(path))
        return native_scandir(path)

    # Establish a complete native baseline before introducing the precise denial.
    measured = measure_storage(root)
    assert measured is not None and measured.bytes >= (blocked / "payload").stat().st_size
    monkeypatch.setattr(Path, "lstat", metadata)
    monkeypatch.setattr(os, "scandir", directory)
    with pytest.raises(PermissionError) as caught:
        measure_storage(root)
    assert caught.value.errno == errno.EACCES


def test_measure_storage_distinguishes_missing_from_unavailable(tmp_path: Path) -> None:
    assert measure_storage(None) is None
    assert measure_storage(tmp_path / "absent") is None
    path = tmp_path / "model.json"
    path.write_bytes(b"model parameters")
    measured = measure_storage(path)
    assert measured is not None
    assert measured.entries == 1 and measured.bytes == path.stat().st_size


def test_measure_storage_closes_open_directory_handles_after_a_walk_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "observed"
    blocked = root / "blocked"
    blocked.mkdir(parents=True)
    assert measure_storage(root) is not None
    native_scandir = os.scandir
    observed: list[Any] = []

    class Scan:
        def __init__(self, iterator: Any) -> None:
            self.iterator = iterator
            self.closed = False

        def __iter__(self) -> Any:
            return self

        def __next__(self) -> Any:
            return next(self.iterator)

        def __enter__(self) -> Any:
            return self

        def __exit__(self, *_args: object) -> None:
            self.close()

        def close(self) -> None:
            self.iterator.close()
            self.closed = True

    def directory(path: str | os.PathLike[str] | int) -> Any:
        if not isinstance(path, int) and Path(path) == blocked:
            raise PermissionError(errno.EACCES, "injected directory denial", str(path))
        scan = Scan(native_scandir(path))
        observed.append(scan)
        return scan

    monkeypatch.setattr(os, "scandir", directory)
    with pytest.raises(PermissionError):
        measure_storage(root)
    assert observed and all(scan.closed for scan in observed)


def test_generic_prune_refuses_run_workspaces_without_database_authority(tmp_path: Path) -> None:
    env = _environment(tmp_path)
    database_path = Path(env["XDG_DATA_HOME"]) / "oh-goon" / "oh-goon.db"
    layout = ApplicationStatePathLayout.from_database(database_path)
    workspace = layout.run_workspaces / "oh-goon-plan-orphan"
    workspace.mkdir(parents=True)

    report = prune_generic_storage(env)

    assert report.removed_run_workspaces == ()
    assert report.refused_run_workspaces == ("oh-goon-plan-orphan",)
    assert workspace.exists()


def test_generic_prune_removes_workspace_only_while_execution_is_idle(tmp_path: Path) -> None:
    env = _environment(tmp_path)
    database_path = Path(env["XDG_DATA_HOME"]) / "oh-goon" / "oh-goon.db"
    database = LocalDatabase(database_path)
    layout = ApplicationStatePathLayout.from_database(database_path)
    workspace = layout.run_workspaces / "oh-goon-plan-orphan"
    workspace.mkdir(parents=True)

    report = prune_generic_storage(env)
    assert report.removed_run_workspaces == ("oh-goon-plan-orphan",)
    assert not workspace.exists()

    workspace.mkdir(parents=True)
    leases = SQLiteExecutionLeaseStore(database)
    lease, _ = leases.acquire_direct_with(
        expected_durable_revision=database.durable_revision(), mutate=lambda _connection: None
    )
    try:
        with pytest.raises(Exception, match="execution"):
            prune_generic_storage(env)
        assert workspace.exists()
    finally:
        leases.finish_as_owner(lease, lambda _connection: None)


def test_active_workspace_refusal_precedes_completed_log_deletion(tmp_path: Path) -> None:
    from oh_goon.runtime.run_log_retention import MAX_RETAINED_RUN_LOG_FILES

    def prepared(root: Path) -> tuple[dict[str, str], LocalDatabase, Path]:
        environment = _environment(root)
        database_path = Path(environment["XDG_DATA_HOME"]) / "oh-goon" / "oh-goon.db"
        database = LocalDatabase(database_path)
        layout = ApplicationStatePathLayout.from_database(database_path)
        (layout.run_workspaces / "oh-goon-plan-candidate").mkdir(parents=True)
        logs = Path(environment["XDG_STATE_HOME"]) / "oh-goon/run-logs/repository/test"
        logs.mkdir(parents=True)
        for index in range(MAX_RETAINED_RUN_LOG_FILES + 2):
            (logs / f"20261031T000000Z-{1000 + index}.log").write_text("completed\n")
        return environment, database, logs

    baseline, _, _ = prepared(tmp_path / "idle")
    assert prune_generic_storage(baseline).removed_run_logs == 2

    environment, database, logs = prepared(tmp_path / "active")
    before = {path.name: path.read_bytes() for path in logs.iterdir()}
    leases = SQLiteExecutionLeaseStore(database)
    lease, _ = leases.acquire_direct_with(
        expected_durable_revision=database.durable_revision(), mutate=lambda _connection: None
    )
    try:
        with pytest.raises(Exception, match="execution"):
            prune_generic_storage(environment)
        assert {path.name: path.read_bytes() for path in logs.iterdir()} == before
    finally:
        leases.finish_as_owner(lease, lambda _connection: None)
    report = prune_generic_storage(environment)
    assert report.removed_run_logs == 2
    assert report.removed_run_workspaces == ("oh-goon-plan-candidate",)


@pytest.mark.process
@pytest.mark.skipif(sys.platform != "linux", reason="native Linux process visibility proof")
@pytest.mark.parametrize("visibility", ["missing", "transient-missing"])
def test_missing_proc_view_cannot_retire_a_live_workspace_execution_owner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, visibility: str
) -> None:
    environment = _environment(tmp_path)
    database_path = Path(environment["XDG_DATA_HOME"]) / "oh-goon/oh-goon.db"
    database = LocalDatabase(database_path)
    workspace = (
        ApplicationStatePathLayout.from_database(database_path).run_workspaces
        / "oh-goon-plan-live-child"
    )
    workspace.mkdir(parents=True)
    script = """\
import sys
from pathlib import Path
from oh_goon.runtime.database import LocalDatabase
from oh_goon.runtime.execution.sqlite_lease import SQLiteExecutionLeaseStore
database = LocalDatabase(Path(sys.argv[1]))
store = SQLiteExecutionLeaseStore(database)
lease, _ = store.acquire_direct_with(
    expected_durable_revision=database.durable_revision(), mutate=lambda connection: None
)
print("owner-ready", flush=True)
sys.stdin.readline()
store.finish_as_owner(lease, lambda connection: None)
"""
    with subprocess.Popen(
        [sys.executable, "-c", script, str(database_path)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    ) as child:
        assert child.stdout is not None
        try:
            assert select.select([child.stdout], [], [], 10)[0]
            assert child.stdout.readline().strip() == "owner-ready"
            with pytest.raises(ExecutionBusy):
                prune_generic_storage(environment)
            native_read = Path.read_text
            masked = False

            def hidden_stat(path: Path, *args: Any, **kwargs: Any) -> str:
                nonlocal masked
                if path == Path(f"/proc/{child.pid}/stat") and (
                    visibility == "missing" or not masked
                ):
                    masked = True
                    raise FileNotFoundError(errno.ENOENT, "injected unavailable view", str(path))
                return native_read(path, *args, **kwargs)

            monkeypatch.setattr(Path, "read_text", hidden_stat)
            assert child.poll() is None
            os.kill(child.pid, 0)
            with pytest.raises(ExecutionBusy):
                prune_generic_storage(environment)
            assert workspace.is_dir()
            with database.transaction() as connection:
                assert (
                    connection.execute("SELECT owner_pid FROM execution_lease").fetchone()[0]
                    == child.pid
                )
        finally:
            try:
                stdout, stderr = child.communicate(input="release\n", timeout=10)
            except subprocess.TimeoutExpired:
                child.kill()
                stdout, stderr = child.communicate(timeout=10)
        assert child.returncode == 0, (stdout, stderr)
    assert prune_generic_storage(environment).removed_run_workspaces == (workspace.name,)


def test_generic_storage_prune_does_not_claim_unclassified_cache_authority(tmp_path: Path) -> None:
    env = _environment(tmp_path)
    unclassified = Path(env["XDG_CACHE_HOME"]) / "oh-goon" / "certification-dependencies"
    unclassified.mkdir(parents=True)
    (unclassified / "sentinel").write_text("unclassified", encoding="utf-8")

    prune_generic_storage(env)

    assert (unclassified / "sentinel").read_text(encoding="utf-8") == "unclassified"


def test_storage_census_reports_unknown_oh_goon_cache_and_state_without_cleanup_authority(
    tmp_path: Path,
) -> None:
    from oh_goon.runtime.storage_lifecycle import storage_census

    env = _environment(tmp_path)
    legacy_cache = Path(env["XDG_CACHE_HOME"]) / "oh-goon" / "certification"
    unknown_state = Path(env["XDG_STATE_HOME"]) / "oh-goon" / "mystery"
    legacy_cache.mkdir(parents=True)
    unknown_state.mkdir(parents=True)

    census = storage_census(env)

    assert {(item.root_kind, item.path.name) for item in census.unclassified} == {
        ("cache", "certification"),
        ("state", "mystery"),
    }
    assert all("no automatic cleanup authority" in item.reason for item in census.unclassified)


def test_storage_census_accounts_for_pytest_receipts_without_claiming_package_manager_caches(
    tmp_path: Path,
) -> None:
    from oh_goon.runtime.storage_lifecycle import storage_census

    census = storage_census(_environment(tmp_path))
    items = {item.storage_id: item for item in census.inventory}

    assert "developer.frontend-cache" not in items
    assert "developer.python-locks" not in items
    assert items["pytest.member-receipts"].durability == "identity-keyed-execution-evidence"
    assert census.unclassified == ()


def test_storage_census_exposes_every_unowned_cache_top_level(tmp_path: Path) -> None:
    from oh_goon.runtime.storage_lifecycle import storage_census

    env = _environment(tmp_path)
    cache = Path(env["XDG_CACHE_HOME"]) / "oh-goon"
    (cache / "bootstrap").mkdir(parents=True)
    (cache / "developer" / "mystery").mkdir(parents=True)
    (cache / "certification-dependencies" / "schema-6" / "current").mkdir(parents=True)

    census = storage_census(env)
    residual = {item.path.relative_to(cache).as_posix() for item in census.unclassified}

    assert residual == {"bootstrap", "developer", "certification-dependencies"}
    assert all("no automatic cleanup authority" in item.reason for item in census.unclassified)


def test_storage_inventory_exposes_repository_certification_run_workspaces(tmp_path: Path) -> None:
    from oh_goon.runtime.database import DATABASE_SCHEMA_VERSION

    env = _environment(tmp_path)
    state = (
        Path(env["XDG_STATE_HOME"])
        / "oh-goon"
        / "certification"
        / ("repo-" + "a" * 20)
        / f"schema-{DATABASE_SCHEMA_VERSION}"
        / "state"
    )
    workspace = state / "run-workspaces" / "oh-goon-plan-stale"
    workspace.mkdir(parents=True)

    items = {item.storage_id: item for item in storage_inventory(env)}
    item = items["certification.run-workspaces.repo-" + "a" * 20]

    assert item.path == state / "run-workspaces"
    assert item.durability == "ephemeral-certification-execution"
    assert item.generic_prune is True
    assert "reconciled before certification" in item.retention


def test_old_certification_schema_is_unclassified_not_cleanup_authority(tmp_path: Path) -> None:
    from oh_goon.runtime.database import DATABASE_SCHEMA_VERSION
    from oh_goon.runtime.storage_lifecycle import storage_census

    env = _environment(tmp_path)
    repository = "repo-" + "b" * 20
    old_schema = max(1, DATABASE_SCHEMA_VERSION - 1)
    schema = (
        Path(env["XDG_STATE_HOME"])
        / "oh-goon"
        / "certification"
        / repository
        / f"schema-{old_schema}"
    )
    workspace = schema / "state" / "run-workspaces" / "oh-goon-plan-stale"
    workspace.mkdir(parents=True)

    items = {item.storage_id: item for item in storage_inventory(env)}
    assert not any(
        storage_id.startswith(f"certification.run-workspaces.{repository}") for storage_id in items
    )
    census = storage_census(env)
    assert schema in {item.path for item in census.unclassified}
    assert workspace.exists()


def test_storage_census_treats_old_developer_cache_tree_as_unclassified(tmp_path: Path) -> None:
    from oh_goon.runtime.storage_lifecycle import storage_census

    env = _environment(tmp_path)
    developer = Path(env["XDG_CACHE_HOME"]) / "oh-goon" / "developer"
    python_locks = developer / "python-locks"
    python_locks.mkdir(parents=True)
    (python_locks / "sentinel").write_text("unclassified", encoding="utf-8")

    census = storage_census(env)

    assert developer in {item.path for item in census.unclassified}
    prune_generic_storage(env)
    assert (python_locks / "sentinel").read_text(encoding="utf-8") == "unclassified"


def test_generic_prune_reports_cleanup_incomplete_without_claiming_removal(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    env = _environment(tmp_path)
    database_path = Path(env["XDG_DATA_HOME"]) / "oh-goon" / "oh-goon.db"
    LocalDatabase(database_path)
    layout = ApplicationStatePathLayout.from_database(database_path)
    workspace = layout.run_workspaces / "oh-goon-plan-orphan"
    workspace.mkdir(parents=True)

    def fail_remove(_path: Path) -> None:
        raise OSError("simulated cleanup failure")

    monkeypatch.setattr(
        "oh_goon.runtime.run_workspace_reconciliation._remove_owned_workspace",
        fail_remove,
    )

    report = prune_generic_storage(env)

    assert report.removed_run_workspaces == ()
    assert report.refused_run_workspaces == ()
    assert report.cleanup_incomplete_run_workspaces == ("oh-goon-plan-orphan",)
    assert workspace.exists()


def test_generic_prune_treats_parallel_cleanup_absence_as_success(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    env = _environment(tmp_path)
    database_path = Path(env["XDG_DATA_HOME"]) / "oh-goon" / "oh-goon.db"
    LocalDatabase(database_path)
    layout = ApplicationStatePathLayout.from_database(database_path)
    workspace = layout.run_workspaces / "oh-goon-plan-raced"
    workspace.mkdir(parents=True)

    def parallel_remove(path: Path) -> None:
        path.rmdir()
        raise FileNotFoundError(path)

    monkeypatch.setattr(
        "oh_goon.runtime.run_workspace_reconciliation.shutil.rmtree",
        parallel_remove,
    )

    report = prune_generic_storage(env)

    assert report.removed_run_workspaces == ("oh-goon-plan-raced",)
    assert report.refused_run_workspaces == ()
    assert report.cleanup_incomplete_run_workspaces == ()
    assert not workspace.exists()


def test_certification_workspace_cleanup_failure_is_not_misreported_as_refusal(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from oh_goon.services.certification_bootstrap import reconcile_certification_run_workspaces
    from oh_goon.services.certification_workspace_retention import certification_state_root

    source = tmp_path / "source"
    source.mkdir()
    state_home = tmp_path / "state"
    state = certification_state_root(source_root=source, state_home=state_home)
    LocalDatabase(state / "oh-goon.db")
    workspace = state / "run-workspaces" / "oh-goon-plan-orphan"
    workspace.mkdir(parents=True)

    def fail_remove(_path: Path) -> None:
        raise OSError("simulated cleanup failure")

    monkeypatch.setattr(
        "oh_goon.runtime.run_workspace_reconciliation._remove_owned_workspace",
        fail_remove,
    )

    result = reconcile_certification_run_workspaces(state_home=state_home)

    assert result.removed == ()
    assert result.refused == ()
    assert len(result.cleanup_incomplete) == 1
    assert result.cleanup_incomplete[0].endswith("/oh-goon-plan-orphan")
    assert workspace.exists()
