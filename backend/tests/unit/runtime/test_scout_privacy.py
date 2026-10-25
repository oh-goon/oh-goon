from __future__ import annotations

import errno
import os
import sqlite3
import stat
from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path

import pytest

from oh_goon.core.time import utc_now
from oh_goon.runtime.database import LocalDatabase
from oh_goon.scout.database import ScoutAnalyticalDatabase, scout_analytical_database_path
from oh_goon.scout.evidence import ScoutEvidenceSnapshot
from oh_goon.scout.store import ScoutObservationStore

_RUN_COLUMNS = {
    "run_id",
    "repository_id",
    "started_at",
    "run_status",
    "repository_name",
    "repository_status",
    "plan_id",
    "plan_name",
    "duration_seconds",
}

_STEP_COLUMNS = {
    "run_id",
    "repository_id",
    "goon_source_sha256",
    "step_id",
    "started_at",
    "repository_name",
    "repository_status",
    "plan_id",
    "plan_name",
    "goon_name",
    "step_status",
    "attempt_count",
    "eligible_attempts",
    "cpu_observed_attempts",
    "memory_observed_attempts",
    "observed_cpu_seconds",
    "complete_cpu_seconds",
    "observed_peak_memory_bytes",
    "complete_peak_memory_bytes",
    "duration_seconds",
    "environment_key_json",
}


def test_scout_derived_tables_have_explicit_metadata_resource_allowlist(tmp_path: Path) -> None:
    database = LocalDatabase(tmp_path / "state.sqlite3")
    ScoutObservationStore(database)

    with sqlite3.connect(scout_analytical_database_path(database.path)) as connection:
        connection.row_factory = sqlite3.Row
        run_columns = {
            str(row["name"])
            for row in connection.execute("PRAGMA table_info(scout_run_observations)")
        }
        step_columns = {
            str(row["name"])
            for row in connection.execute("PRAGMA table_info(scout_step_observations)")
        }

    assert run_columns == _RUN_COLUMNS
    assert step_columns == _STEP_COLUMNS


def test_scout_derived_schema_has_no_log_secret_or_source_payload_columns(tmp_path: Path) -> None:
    database = LocalDatabase(tmp_path / "state.sqlite3")
    ScoutObservationStore(database)

    with sqlite3.connect(scout_analytical_database_path(database.path)) as connection:
        connection.row_factory = sqlite3.Row
        columns = {
            str(row["name"]).lower()
            for table in ("scout_run_observations", "scout_step_observations")
            for row in connection.execute(f"PRAGMA table_info({table})")
        }

    forbidden_fragments = (
        "stdout",
        "stderr",
        "log",
        "secret",
        "token",
        "password",
        "command",
        "source_code",
        "environment_value",
    )
    assert not {
        column for column in columns if any(fragment in column for fragment in forbidden_fragments)
    }


def test_scout_read_transactions_are_sqlite_query_only(tmp_path: Path) -> None:
    database = LocalDatabase(tmp_path / "state.sqlite3")
    ScoutObservationStore(database)
    analytical = ScoutAnalyticalDatabase(database.path)

    with analytical.transaction() as connection:
        assert int(connection.execute("PRAGMA query_only").fetchone()[0]) == 1
        try:
            connection.execute("DELETE FROM scout_metadata")
        except sqlite3.OperationalError as error:
            assert "readonly" in str(error).lower()
        else:
            raise AssertionError("Scout read transaction unexpectedly accepted a write")


def test_scout_sidecar_refuses_symlink_target_without_touching_victim(tmp_path: Path) -> None:
    database = LocalDatabase(tmp_path / "state.sqlite3")
    victim = tmp_path / "victim.txt"
    victim.write_text("keep-me", encoding="utf-8")
    sidecar = scout_analytical_database_path(database.path)
    sidecar.symlink_to(victim)

    try:
        ScoutObservationStore(database)
    except RuntimeError as error:
        assert "non-symlink" in str(error)
    else:
        raise AssertionError("Scout accepted a symlink analytical database")

    assert victim.read_text(encoding="utf-8") == "keep-me"
    assert sidecar.is_symlink()


def test_scout_corrupt_disposable_sidecar_rebuilds_from_empty_revision(tmp_path: Path) -> None:
    database = LocalDatabase(tmp_path / "state.sqlite3")
    sidecar = scout_analytical_database_path(database.path)
    sidecar.write_bytes(b"not-a-sqlite-database")

    store = ScoutObservationStore(database)

    assert store.journal_revision() == 0
    with sqlite3.connect(sidecar) as connection:
        tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name LIKE 'scout_%'"
            )
        }
    assert tables == {
        "scout_metadata",
        "scout_run_observations",
        "scout_step_observations",
    }


def test_scout_database_family_is_owner_only_and_storage_is_measurable(tmp_path: Path) -> None:
    if os.name == "nt":
        return
    database = LocalDatabase(tmp_path / "state.sqlite3")
    store = ScoutObservationStore(database)
    analytical = ScoutAnalyticalDatabase(database.path)

    with analytical.transaction(write=True) as connection:
        connection.execute(
            "INSERT OR REPLACE INTO scout_metadata(singleton, measurement_schema_version, journal_revision) "
            "VALUES (1, 3, 0)"
        )
        family = (
            analytical.path,
            Path(f"{analytical.path}-wal"),
            Path(f"{analytical.path}-shm"),
        )
        existing = [path for path in family if path.exists()]
        assert existing
        assert all((path.stat().st_mode & 0o777) == 0o600 for path in existing)

    stats = store.stats()
    assert stats.analytical_storage_bytes > 0


def test_scout_write_transactions_use_secure_delete(tmp_path: Path) -> None:
    database = LocalDatabase(tmp_path / "state.sqlite3")
    ScoutObservationStore(database)
    analytical = ScoutAnalyticalDatabase(database.path)

    with analytical.transaction(write=True) as connection:
        assert int(connection.execute("PRAGMA secure_delete").fetchone()[0]) == 1
        assert int(connection.execute("PRAGMA trusted_schema").fetchone()[0]) == 0


def test_scout_refuses_symlink_wal_family_member(tmp_path: Path) -> None:
    database = LocalDatabase(tmp_path / "state.sqlite3")
    sidecar = scout_analytical_database_path(database.path)
    victim = tmp_path / "victim-wal.txt"
    victim.write_text("keep-wal", encoding="utf-8")
    Path(f"{sidecar}-wal").symlink_to(victim)

    try:
        ScoutObservationStore(database)
    except RuntimeError as error:
        assert "non-symlink" in str(error)
    else:
        raise AssertionError("Scout accepted a symlink WAL path")

    assert victim.read_text(encoding="utf-8") == "keep-wal"


def test_scout_restart_preserves_revision_and_rehardens_sidecar_permissions(tmp_path: Path) -> None:
    import os

    database = LocalDatabase(tmp_path / "state.sqlite3")
    first = ScoutObservationStore(database)
    now = utc_now()
    first.replace_changed_runs(
        run_ids=(),
        snapshot=ScoutEvidenceSnapshot(journal_revision=17, runs=(), steps=()),
        journal_revision=17,
        run_retain_after=now - timedelta(days=365),
        step_retain_after=now - timedelta(days=90),
    )
    sidecar = ScoutAnalyticalDatabase(database.path).path
    if os.name != "nt":
        sidecar.chmod(0o644)

    reopened = ScoutObservationStore(database)

    assert reopened.journal_revision() == 17
    if os.name != "nt":
        assert (sidecar.stat().st_mode & 0o777) == 0o600


def test_scout_storage_bytes_tolerates_transient_shm_disappearance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = LocalDatabase(tmp_path / "state.sqlite3")
    ScoutObservationStore(database)
    analytical = ScoutAnalyticalDatabase(database.path)
    real_open = os.open

    def disappearing_open(
        path: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        if str(path).endswith("-shm"):
            raise FileNotFoundError(path)
        return real_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(os, "open", disappearing_open)
    assert analytical.storage_bytes() > 0


@pytest.fixture
def native_scout_wal(
    tmp_path: Path,
) -> Iterator[tuple[ScoutAnalyticalDatabase, sqlite3.Connection]]:
    database = LocalDatabase(tmp_path / "state.sqlite3")
    ScoutObservationStore(database)
    analytical = ScoutAnalyticalDatabase(database.path)
    keeper = sqlite3.connect(analytical.path, isolation_level=None)
    try:
        keeper.execute("PRAGMA journal_mode = WAL")
        keeper.execute("CREATE TABLE native_close_probe(value INTEGER)")
        keeper.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        keeper.execute("INSERT INTO native_close_probe VALUES (7)")
        yield analytical, keeper
    finally:
        keeper.close()


@pytest.mark.skipif(os.name == "nt", reason="POSIX unlink-while-open semantics required")
@pytest.mark.parametrize("suffix", ["-wal", "-shm"])
@pytest.mark.parametrize("operation", ["harden", "storage"])
def test_native_sqlite_close_can_unlink_an_opened_transient_scout_file(
    native_scout_wal: tuple[ScoutAnalyticalDatabase, sqlite3.Connection],
    monkeypatch: pytest.MonkeyPatch,
    suffix: str,
    operation: str,
) -> None:
    analytical, keeper = native_scout_wal
    candidate = Path(f"{analytical.path}{suffix}")
    native_open = os.open
    opened: int | None = None
    replacement = b"new sidecar inode"
    main_bytes = analytical.path.stat().st_size
    wal_bytes = Path(f"{analytical.path}-wal").stat().st_size

    def close_after_open(
        path: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        nonlocal opened
        descriptor = native_open(path, flags, mode, dir_fd=dir_fd)
        if Path(os.fsdecode(path)) == candidate and opened is None:
            opened = descriptor
            assert os.fstat(descriptor).st_nlink == 1
            keeper.close()  # Native SQLite removes its last connection's WAL and SHM.
            metadata = os.fstat(descriptor)
            assert stat.S_ISREG(metadata.st_mode)
            assert metadata.st_nlink == 0
            assert not candidate.exists()
            candidate.write_bytes(replacement)
            candidate.chmod(0o644)
        return descriptor

    if operation == "harden":
        with analytical.transaction() as connection:
            assert connection.execute("SELECT value FROM native_close_probe").fetchone()[0] == 7
            # The Scout transaction closes first, leaving keeper as SQLite's
            # final connection during post-transaction permission hardening.
            monkeypatch.setattr(os, "open", close_after_open)
    else:
        monkeypatch.setattr(os, "open", close_after_open)
        measured = analytical.storage_bytes()
        assert measured == main_bytes + (wal_bytes if suffix == "-shm" else 0)

    assert opened is not None
    with pytest.raises(OSError) as closed:
        os.fstat(opened)
    assert closed.value.errno == errno.EBADF
    assert candidate.read_bytes() == replacement
    assert stat.S_IMODE(candidate.stat().st_mode) == 0o644
    assert stat.S_IMODE(analytical.path.stat().st_mode) == 0o600


@pytest.mark.skipif(os.name == "nt", reason="POSIX descriptor/link semantics required")
@pytest.mark.parametrize("suffix", ["", "-wal", "-shm"])
@pytest.mark.parametrize("operation", ["harden", "storage"])
def test_scout_refuses_a_hardlink_added_after_its_file_is_opened(
    native_scout_wal: tuple[ScoutAnalyticalDatabase, sqlite3.Connection],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    suffix: str,
    operation: str,
) -> None:
    analytical, _keeper = native_scout_wal
    candidate = Path(f"{analytical.path}{suffix}")
    alias = tmp_path / "foreign-link"
    native_open = os.open

    def link_after_open(
        path: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        descriptor = native_open(path, flags, mode, dir_fd=dir_fd)
        if Path(os.fsdecode(path)) == candidate and not alias.exists():
            os.link(candidate, alias)
            assert os.fstat(descriptor).st_nlink == 2
        return descriptor

    with pytest.raises(RuntimeError, match="single-link regular file"):
        if operation == "harden":
            with analytical.transaction():
                monkeypatch.setattr(os, "open", link_after_open)
        else:
            monkeypatch.setattr(os, "open", link_after_open)
            analytical.storage_bytes()
    assert alias.exists()


@pytest.mark.skipif(os.name == "nt", reason="POSIX unlink-while-open semantics required")
@pytest.mark.parametrize("operation", ["harden", "storage"])
def test_scout_still_refuses_an_unlinked_persistent_database_descriptor(
    native_scout_wal: tuple[ScoutAnalyticalDatabase, sqlite3.Connection],
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
) -> None:
    analytical, _keeper = native_scout_wal
    native_open = os.open

    def unlink_after_open(
        path: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        descriptor = native_open(path, flags, mode, dir_fd=dir_fd)
        if Path(os.fsdecode(path)) == analytical.path:
            analytical.path.unlink()
            assert os.fstat(descriptor).st_nlink == 0
        return descriptor

    with pytest.raises(RuntimeError, match="single-link regular file"):
        if operation == "harden":
            with analytical.transaction():
                monkeypatch.setattr(os, "open", unlink_after_open)
        else:
            monkeypatch.setattr(os, "open", unlink_after_open)
            analytical.storage_bytes()
