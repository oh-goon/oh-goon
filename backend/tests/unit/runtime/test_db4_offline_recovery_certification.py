"""DB4 development receipts, not host-fault or independent WSL certification.

The real original SQLite owner provides all transactions and backup verification.
No fixture can promote offline evidence into live execution admission.
"""
from __future__ import annotations

import multiprocessing
import os
import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest

from oh_goon.runtime.database import LocalDatabase
from oh_goon.runtime.durable_revision import read_durable_revision


def _terminated_writer(database_path: str, committed: bool) -> None:
    """Real OS process exit, not an exception pretending to be a crash."""
    database = LocalDatabase(Path(database_path), journal_mode="DELETE")
    if committed:
        with database.transaction(write=True) as connection:
            connection.execute(
                "INSERT INTO locations(path, position, added_at) VALUES ('/db4/committed', 3, 'now')"
            )
        os._exit(27)  # AFTER commit but before caller acknowledgement
    with database.transaction(write=True) as connection:
        connection.execute(
            "INSERT INTO locations(path, position, added_at) VALUES ('/db4/uncommitted', 4, 'now')"
        )
        os._exit(28)  # while SQLite owns an uncommitted writer cut


@pytest.mark.parametrize("committed,expected_exit", [(False, 28), (True, 27)])
def test_real_process_exit_reopen_original_owner_and_verified_snapshot(
    tmp_path: Path, committed: bool, expected_exit: int
) -> None:
    path = tmp_path / "operational.sqlite3"
    LocalDatabase(path, journal_mode="DELETE")
    ctx = multiprocessing.get_context("spawn")
    child = ctx.Process(target=_terminated_writer, args=(str(path), committed))
    child.start()
    try:
        child.join(timeout=20)
        assert child.exitcode == expected_exit
    finally:
        if child.is_alive():
            child.terminate()
            child.join(timeout=3)
    resumed = LocalDatabase(path, journal_mode="DELETE")
    with resumed.transaction() as connection:
        rows = connection.execute("SELECT path FROM locations WHERE path LIKE '/db4/%'").fetchall()
        assert [row[0] for row in rows] == (["/db4/committed"] if committed else [])
        assert read_durable_revision(connection) == (1 if committed else 0)
    receipt = resumed.export_verified_backup(tmp_path / "offline" / "snapshot.sqlite3")
    LocalDatabase.verify_offline_backup(receipt)
    # Snapshot is not swapped into the live path. Original owner still owns it.
    assert resumed.path == path
    assert receipt.path != path


def test_offline_snapshot_rejects_modified_receipt_even_when_sqlite_valid(tmp_path: Path) -> None:
    db = LocalDatabase(tmp_path / "operational.sqlite3")
    with db.transaction(write=True) as connection:
        connection.execute(
            "INSERT INTO locations(path, position, added_at) VALUES ('/db4/genuine', 1, 'now')"
        )
    receipt = db.export_verified_backup(tmp_path / "snapshots" / "snapshot.sqlite3")
    LocalDatabase.verify_offline_backup(receipt)
    for wrong in (
        replace(receipt, durable_revision=receipt.durable_revision + 1),
        replace(receipt, database_identity_cookie=receipt.database_identity_cookie + 1),
        replace(receipt, sha256="0" * 64),
    ):
        with pytest.raises(sqlite3.DatabaseError, match="mismatch"):
            LocalDatabase.verify_offline_backup(wrong)
    assert db.durable_revision() == receipt.durable_revision


def test_offline_snapshot_rejects_path_alias_and_mutated_bytes(tmp_path: Path) -> None:
    db = LocalDatabase(tmp_path / "operational.sqlite3")
    receipt = db.export_verified_backup(tmp_path / "snapshots" / "snapshot.sqlite3")
    alias = receipt.path.with_name("alias.sqlite3")
    os.link(receipt.path, alias)
    with pytest.raises(sqlite3.DatabaseError, match="single-link"):
        LocalDatabase.verify_offline_backup(receipt)
    alias.unlink()
    symlink = receipt.path.with_name("symlink.sqlite3")
    symlink.symlink_to(receipt.path)
    with pytest.raises(sqlite3.DatabaseError, match="canonical"):
        LocalDatabase.verify_offline_backup(replace(receipt, path=symlink))
    with receipt.path.open("r+b") as stream:
        stream.seek(600)
        stream.write(b"TAMPER")
    with pytest.raises(sqlite3.DatabaseError, match="SHA-256 mismatch"):
        LocalDatabase.verify_offline_backup(receipt)
    assert db.durable_revision() == 0


def test_source_cookie_mismatch_never_publishes_snapshot(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db = LocalDatabase(tmp_path / "operational.sqlite3")
    real_verify = LocalDatabase.verify_offline_backup

    def observe_wrong_cookie(receipt):
        return real_verify(replace(receipt, database_identity_cookie=receipt.database_identity_cookie + 1))

    monkeypatch.setattr(LocalDatabase, "verify_offline_backup", staticmethod(observe_wrong_cookie))
    target = tmp_path / "snapshots" / "snapshot.sqlite3"
    with pytest.raises(sqlite3.DatabaseError, match="cookie/revision mismatch"):
        db.export_verified_backup(target)
    assert not target.exists()
    assert not list(target.parent.glob(".*.snapshot-tmp"))
    assert db.durable_revision() == 0


def test_original_fleet_atomic_ingress_survives_offline_snapshot_reopen(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Import the ORIGINAL Fleet product setup rather than constructing a test
    # scheduler/admission writer or a parallel Sqlite transaction controller.
    from tests.unit.services.test_group_a_atomic_fleet_prework_ingress import prepared, publish

    args, external, _native_calls = prepared(tmp_path, monkeypatch)
    try:
        assert publish(args) is not None
        db = args["journal"]._database
        receipt = db.export_verified_backup(tmp_path / "snapshots" / "fleet.sqlite3")
        LocalDatabase.verify_offline_backup(receipt)
        offline = LocalDatabase(receipt.path)
        with offline.transaction() as connection:
            assert connection.execute(
                "SELECT COUNT(*) FROM lace_run_authority WHERE run_id='durable-run'"
            ).fetchone()[0] == 1
            kinds = [row[0] for row in connection.execute(
                "SELECT event_kind FROM lace_run_events WHERE run_id='durable-run' ORDER BY run_seq"
            )]
            assert kinds == ["RUN_ADMITTED", "RUN_SIGNER_EPOCH_BOUND"]
            assert read_durable_revision(connection) == receipt.durable_revision
    finally:
        external.close()
