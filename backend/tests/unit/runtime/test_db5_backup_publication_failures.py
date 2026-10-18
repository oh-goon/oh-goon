"""DB5: the original LocalDatabase must not acknowledge a nondurable backup.

These are deterministic failure-path unit tests, not independent host EIO/fsync
or native WSL qualification receipts.
"""
from __future__ import annotations

import errno
import os
import sqlite3
import stat
from pathlib import Path

import pytest

import oh_goon.runtime.database as database_module
from oh_goon.runtime.database import BackupPublicationUncertain, LocalDatabase


def _original_source(tmp_path: Path) -> tuple[LocalDatabase, Path]:
    owner = LocalDatabase(tmp_path / "state.sqlite3")
    with owner.transaction(write=True) as conn:
        conn.execute("INSERT INTO locations(path, position, added_at) VALUES ('/db5/record', 1, 'now')")
    return owner, tmp_path / "snapshots" / "offline.sqlite3"


def test_fsync_failure_after_link_rolls_back_publication_and_allows_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner, target = _original_source(tmp_path)
    original_sync = database_module._sync_backup_directory
    calls = 0

    def fail_first_directory_sync(directory: Path) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError(errno.EIO, "injected directory fsync fault")
        original_sync(directory)

    with monkeypatch.context() as change:
        change.setattr(database_module, "_sync_backup_directory", fail_first_directory_sync)
        with pytest.raises(OSError, match="injected directory fsync fault"):
            owner.export_verified_backup(target)
    assert calls == 2  # failure, then successful removal/parent sync
    assert not target.exists()
    assert not list(target.parent.glob(".*.snapshot-tmp"))
    with owner.transaction() as conn:
        assert conn.execute("SELECT COUNT(*) FROM locations WHERE path='/db5/record'").fetchone()[0] == 1
    receipt = owner.export_verified_backup(target)
    LocalDatabase.verify_offline_backup(receipt)


def test_repeated_directory_fsync_failure_never_returns_success_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner, target = _original_source(tmp_path)

    def broken_directory_sync(_directory: Path) -> None:
        raise OSError(errno.EIO, "persistent EIO (injected)")

    monkeypatch.setattr(database_module, "_sync_backup_directory", broken_directory_sync)
    with pytest.raises(BackupPublicationUncertain, match="durability uncertain"):
        owner.export_verified_backup(target)
    assert not list(target.parent.glob(".*.snapshot-tmp"))
    with owner.transaction() as conn:
        assert conn.execute("SELECT COUNT(*) FROM locations WHERE path='/db5/record'").fetchone()[0] == 1


def test_postpublish_tamper_prevents_success_and_removes_our_link(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner, target = _original_source(tmp_path)
    original_verify = LocalDatabase.verify_offline_backup

    def corrupt_published_snapshot(receipt) -> None:
        if receipt.path == target:
            with target.open("r+b") as stream:
                stream.seek(500)
                stream.write(b"DB5-TAMPER")
                stream.flush()
        original_verify(receipt)

    monkeypatch.setattr(LocalDatabase, "verify_offline_backup", staticmethod(corrupt_published_snapshot))
    with pytest.raises(sqlite3.DatabaseError, match="SHA-256 mismatch"):
        owner.export_verified_backup(target)
    assert not target.exists()
    assert not list(target.parent.glob(".*.snapshot-tmp"))


def test_postpublish_replacement_never_deletes_unrelated_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner, target = _original_source(tmp_path)
    original_verify = LocalDatabase.verify_offline_backup

    def replace_published_snapshot(receipt) -> None:
        if receipt.path == target:
            target.unlink()
            target.write_bytes(b"other-owner")
            raise sqlite3.DatabaseError("injected postpublish replacement")
        original_verify(receipt)

    monkeypatch.setattr(LocalDatabase, "verify_offline_backup", staticmethod(replace_published_snapshot))
    with pytest.raises(BackupPublicationUncertain, match="never use as runtime authority"):
        owner.export_verified_backup(target)
    assert target.read_bytes() == b"other-owner"
    assert not list(target.parent.glob(".*.snapshot-tmp"))


def test_success_acknowledges_published_single_link_not_stage(tmp_path: Path) -> None:
    owner, target = _original_source(tmp_path)
    receipt = owner.export_verified_backup(target)
    assert receipt.path == target
    assert stat.S_ISREG(target.stat().st_mode)
    assert target.stat().st_nlink == 1
    assert not list(target.parent.glob(".*.snapshot-tmp"))
    LocalDatabase.verify_offline_backup(receipt)
