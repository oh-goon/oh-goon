"""DB5: the original LocalDatabase must not acknowledge a nondurable backup.

These are deterministic failure-path unit tests, not independent host EIO/fsync
or native WSL qualification receipts.
"""

from __future__ import annotations

import errno
import os
import sqlite3
import stat
from dataclasses import dataclass
from pathlib import Path

import pytest

import oh_goon.runtime.database as database_module
from oh_goon.runtime.database import (
    BackupPublicationUncertain,
    LocalDatabase,
    LocalStateBackupReceipt,
)


@dataclass
class BackupDescriptor:
    descriptor: int
    closed: bool = False


@pytest.fixture
def backup_descriptors(monkeypatch: pytest.MonkeyPatch) -> list[BackupDescriptor]:
    """Observe original-owner native handles without opening a test-owned pin."""
    handles: list[BackupDescriptor] = []
    native_open, native_close = os.open, os.close

    def observe_open(
        path: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        descriptor = native_open(path, flags, mode, dir_fd=dir_fd)
        if os.fsdecode(path).endswith(".snapshot-tmp"):
            handles.append(BackupDescriptor(descriptor))
        return descriptor

    def observe_close(descriptor: int) -> None:
        native_close(descriptor)
        for handle in reversed(handles):
            if handle.descriptor == descriptor and not handle.closed:
                handle.closed = True
                break

    monkeypatch.setattr(os, "open", observe_open)
    monkeypatch.setattr(os, "close", observe_close)
    return handles


def _original_source(tmp_path: Path) -> tuple[LocalDatabase, Path]:
    owner = LocalDatabase(tmp_path / "state.sqlite3")
    with owner.transaction(write=True) as conn:
        conn.execute(
            "INSERT INTO locations(path, position, added_at) VALUES ('/db5/record', 1, 'now')"
        )
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
        assert (
            conn.execute("SELECT COUNT(*) FROM locations WHERE path='/db5/record'").fetchone()[0]
            == 1
        )
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
        assert (
            conn.execute("SELECT COUNT(*) FROM locations WHERE path='/db5/record'").fetchone()[0]
            == 1
        )


def test_postpublish_tamper_prevents_success_and_removes_our_link(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner, target = _original_source(tmp_path)
    original_verify = LocalDatabase.verify_offline_backup

    def corrupt_published_snapshot(receipt: LocalStateBackupReceipt) -> None:
        if receipt.path == target:
            with target.open("r+b") as stream:
                stream.seek(500)
                stream.write(b"DB5-TAMPER")
                stream.flush()
        original_verify(receipt)

    monkeypatch.setattr(
        LocalDatabase, "verify_offline_backup", staticmethod(corrupt_published_snapshot)
    )
    with pytest.raises(sqlite3.DatabaseError, match="SHA-256 mismatch"):
        owner.export_verified_backup(target)
    assert not target.exists()
    assert not list(target.parent.glob(".*.snapshot-tmp"))


def test_postpublish_replacement_never_deletes_unrelated_target(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    backup_descriptors: list[BackupDescriptor],
) -> None:
    owner, target = _original_source(tmp_path)
    original_verify = LocalDatabase.verify_offline_backup

    def replace_published_snapshot(receipt: LocalStateBackupReceipt) -> None:
        if receipt.path == target:
            original = backup_descriptors[0]
            assert not original.closed
            pinned = os.fstat(original.descriptor)
            published = target.lstat()
            assert (pinned.st_dev, pinned.st_ino) == (published.st_dev, published.st_ino)
            assert pinned.st_nlink == published.st_nlink == 1
            target.unlink()
            assert os.fstat(original.descriptor).st_nlink == 0
            target.write_bytes(b"other-owner")
            raise sqlite3.DatabaseError("injected postpublish replacement")
        original_verify(receipt)

    monkeypatch.setattr(
        LocalDatabase, "verify_offline_backup", staticmethod(replace_published_snapshot)
    )
    with pytest.raises(BackupPublicationUncertain, match="never use as runtime authority"):
        owner.export_verified_backup(target)
    assert target.read_bytes() == b"other-owner"
    assert not list(target.parent.glob(".*.snapshot-tmp"))
    assert backup_descriptors and all(handle.closed for handle in backup_descriptors)


def test_success_acknowledges_published_single_link_not_stage(
    tmp_path: Path, backup_descriptors: list[BackupDescriptor]
) -> None:
    owner, target = _original_source(tmp_path)
    receipt = owner.export_verified_backup(target)
    assert receipt.path == target
    assert stat.S_ISREG(target.stat().st_mode)
    assert target.stat().st_nlink == 1
    assert not list(target.parent.glob(".*.snapshot-tmp"))
    assert backup_descriptors and all(handle.closed for handle in backup_descriptors)
    LocalDatabase.verify_offline_backup(receipt)


def test_stage_cleanup_failure_still_closes_original_snapshot_descriptor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    backup_descriptors: list[BackupDescriptor],
) -> None:
    owner, target = _original_source(tmp_path)
    native_unlink = Path.unlink

    def fail_final_stage_cleanup(path: Path, missing_ok: bool = False) -> None:
        if path.name.endswith(".snapshot-tmp") and missing_ok:
            raise OSError(errno.EIO, "injected final staging cleanup fault")
        native_unlink(path, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", fail_final_stage_cleanup)
    with pytest.raises(OSError, match="injected final staging cleanup fault"):
        owner.export_verified_backup(target)
    assert backup_descriptors and all(handle.closed for handle in backup_descriptors)
    assert target.is_file() and target.stat().st_nlink == 1
    assert not list(target.parent.glob(".*.snapshot-tmp"))
