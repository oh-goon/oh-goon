from __future__ import annotations

import os
import sqlite3
from datetime import timedelta
from pathlib import Path

from oh_goon.scout.evidence import ScoutEvidenceSnapshot
from oh_goon.core.time import utc_now
from oh_goon.runtime.database import LocalDatabase
from oh_goon.scout.database import ScoutAnalyticalDatabase, scout_analytical_database_path
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
    tmp_path: Path, monkeypatch
) -> None:
    database = LocalDatabase(tmp_path / "state.sqlite3")
    ScoutObservationStore(database)
    analytical = ScoutAnalyticalDatabase(database.path)
    real_open = os.open

    def disappearing_open(path, flags, *args):
        if str(path).endswith("-shm"):
            raise FileNotFoundError(path)
        return real_open(path, flags, *args)

    monkeypatch.setattr(os, "open", disappearing_open)
    assert analytical.storage_bytes() > 0
