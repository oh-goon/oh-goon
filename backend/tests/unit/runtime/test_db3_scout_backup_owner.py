"""DB3: disposable Scout and explicit original-owner SQLite backup fences."""

from __future__ import annotations

import hashlib
import os
import sqlite3
from contextlib import closing
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from oh_goon.runtime.database import LocalDatabase
from oh_goon.runtime.durable_revision import read_durable_revision
from oh_goon.scout.database import ScoutAnalyticalDatabase, scout_analytical_database_path


def test_scout_cannot_alias_operational_file_as_hardlink(tmp_path: Path) -> None:
    operational = LocalDatabase(tmp_path / "state.sqlite3")
    sidecar = scout_analytical_database_path(operational.path)
    os.link(operational.path, sidecar)
    scout = ScoutAnalyticalDatabase(operational.path)
    with pytest.raises(RuntimeError, match="single-link|aliases operational"):
        with scout.transaction(write=True):
            pytest.fail("Scout must never receive an operational SQLite handle")
    with pytest.raises(RuntimeError, match="single-link|aliases operational"):
        scout.remove()
    assert sidecar.exists()
    with operational.transaction() as conn:
        assert conn.execute("PRAGMA quick_check").fetchone()[0] == "ok"


def test_scout_rejects_hardlink_to_external_victim(tmp_path: Path) -> None:
    operational = LocalDatabase(tmp_path / "state.sqlite3")
    victim = tmp_path / "victim.txt"
    victim.write_text("do-not-touch")
    os.link(victim, scout_analytical_database_path(operational.path))
    scout = ScoutAnalyticalDatabase(operational.path)
    with pytest.raises(RuntimeError, match="single-link"):
        with scout.transaction(write=True):
            pytest.fail("Scout aliased victim")
    assert victim.read_text() == "do-not-touch"


def test_original_owner_backup_is_verified_and_no_clobber(tmp_path: Path) -> None:
    operational = LocalDatabase(tmp_path / "state.sqlite3")
    with operational.transaction(write=True) as conn:
        conn.execute("UPDATE state_metadata SET durable_revision = durable_revision + 1")
    with operational.transaction() as conn:
        expected = read_durable_revision(conn)
        cookie = conn.execute("PRAGMA application_id").fetchone()[0]
    dest = tmp_path / "snapshots" / "state-backup.sqlite3"
    receipt = operational.export_verified_backup(dest)
    assert receipt.path == dest
    assert receipt.sha256 == hashlib.sha256(dest.read_bytes()).hexdigest()
    assert receipt.durable_revision == expected
    assert receipt.database_identity_cookie == cookie
    with closing(sqlite3.connect(f"file:{dest}?mode=ro", uri=True)) as conn:
        assert read_durable_revision(conn) == expected
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert conn.execute("PRAGMA application_id").fetchone()[0] == cookie
    # A backup is a standalone offline SQLite snapshot, not a replacement of
    # the original live file or a shortcut past restart/admission checks.
    recovered = LocalDatabase(dest)
    with recovered.transaction() as conn:
        assert read_durable_revision(conn) == expected
    original_bytes = dest.read_bytes()
    with pytest.raises(FileExistsError):
        operational.export_verified_backup(dest)
    assert dest.read_bytes() == original_bytes
    with operational.transaction() as conn:
        assert read_durable_revision(conn) == expected


@pytest.mark.parametrize("matching_receipt", [True, False])
def test_backup_verification_closes_native_readers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, matching_receipt: bool
) -> None:
    owner = LocalDatabase(tmp_path / "state.sqlite3")
    receipt = owner.export_verified_backup(tmp_path / "snapshots" / "offline.sqlite3")
    if not matching_receipt:
        receipt = replace(receipt, database_identity_cookie=receipt.database_identity_cookie + 1)
    connections: list[sqlite3.Connection] = []
    native_connect = sqlite3.connect

    def track_connection(*args: Any, **kwargs: Any) -> sqlite3.Connection:
        connection: sqlite3.Connection = native_connect(*args, **kwargs)
        connections.append(connection)
        return connection

    monkeypatch.setattr(sqlite3, "connect", track_connection)
    try:
        if matching_receipt:
            LocalDatabase.verify_offline_backup(receipt)
        else:
            with pytest.raises(sqlite3.DatabaseError, match="cookie/revision mismatch"):
                LocalDatabase.verify_offline_backup(receipt)
        assert connections
        for connection in connections:
            with pytest.raises(sqlite3.ProgrammingError, match="closed"):
                connection.execute("SELECT 1")
    finally:
        for connection in connections:
            connection.close()


def test_backup_never_overwrites_symlink_destination(tmp_path: Path) -> None:
    operational = LocalDatabase(tmp_path / "state.sqlite3")
    victim = tmp_path / "victim.txt"
    victim.write_text("safe")
    (tmp_path / "snapshot.sqlite3").symlink_to(victim)
    with pytest.raises(FileExistsError):
        operational.export_verified_backup(tmp_path / "snapshot.sqlite3")
    assert victim.read_text() == "safe"
    with operational.transaction() as conn:
        assert conn.execute("PRAGMA quick_check").fetchone()[0] == "ok"


def test_disposable_scout_corruption_does_not_change_operational_state(tmp_path: Path) -> None:
    operational = LocalDatabase(tmp_path / "state.sqlite3")
    scout = ScoutAnalyticalDatabase(operational.path)
    with scout.transaction(write=True) as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS scout_derived (value TEXT)")
        conn.execute("INSERT INTO scout_derived VALUES ('disposable')")
    before = operational.export_verified_backup(tmp_path / "before.sqlite3")
    scout.remove()
    with scout.transaction(write=True) as conn:
        conn.execute("CREATE TABLE scout_recreated (n INTEGER)")
    after = operational.export_verified_backup(tmp_path / "after.sqlite3")
    assert before.sha256 == after.sha256
    with operational.transaction() as conn:
        assert conn.execute("PRAGMA quick_check").fetchone()[0] == "ok"


def test_backup_rejects_active_write_cut_before_creating_directory(tmp_path: Path) -> None:
    operational = LocalDatabase(tmp_path / "state.sqlite3")
    directory = tmp_path / "not-created"
    with operational.transaction(write=True) as conn:
        conn.execute(
            "INSERT INTO locations(path, position, added_at) VALUES ('/db3/held', 1, 'now')"
        )
        with pytest.raises(RuntimeError, match="independent explicit maintenance cut"):
            operational.export_verified_backup(directory / "snapshot.sqlite3")
        assert not directory.exists()
    with operational.transaction() as conn:
        assert conn.execute("SELECT COUNT(*) FROM locations").fetchone()[0] == 1


def test_backup_atomic_publication_refuses_competing_destination(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    operational = LocalDatabase(tmp_path / "state.sqlite3")
    destination = tmp_path / "snapshots" / "state.sqlite3"
    original_link = os.link

    def competing_create(source: Path, target: Path, **options: object) -> None:
        # Deterministically simulate another process reserving the target after
        # initial no-clobber inspection but before atomic publication.
        Path(target).write_text("other-owner", encoding="utf-8")
        original_link(source, target, **options)

    monkeypatch.setattr(os, "link", competing_create)
    with pytest.raises(FileExistsError):
        operational.export_verified_backup(destination)
    assert destination.read_text() == "other-owner"
    assert not list(destination.parent.glob(".*.snapshot-tmp"))
    with operational.transaction() as conn:
        assert conn.execute("PRAGMA quick_check").fetchone()[0] == "ok"


def test_backup_never_chmods_existing_shared_directory(tmp_path: Path) -> None:
    operational = LocalDatabase(tmp_path / "state.sqlite3")
    shared = tmp_path / "shared"
    shared.mkdir()
    shared.chmod(0o770)
    with pytest.raises(PermissionError, match="private"):
        operational.export_verified_backup(shared / "do-not-write.sqlite3")
    assert shared.stat().st_mode & 0o777 == 0o770
    assert not (shared / "do-not-write.sqlite3").exists()


def test_invalid_runtime_identity_prevents_backup_directory_creation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    operational = LocalDatabase(tmp_path / "state.sqlite3")
    import oh_goon.runtime.database as original_owner

    observed = operational._path_identity
    assert observed is not None
    new_fs = "invalid-fs-change" if observed.filesystem != "invalid-fs-change" else "other"
    monkeypatch.setattr(original_owner, "database_filesystem_type", lambda _path: new_fs)
    target_parent = tmp_path / "not-admitted"
    with pytest.raises(sqlite3.DatabaseError, match="filesystem admission changed"):
        operational.export_verified_backup(target_parent / "snapshot.sqlite3")
    assert not target_parent.exists()
