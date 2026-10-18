"""DB0/DB1A: admitted SQLite cut, not a new database engine or scheduler."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from oh_goon.repository_tooling.architecture.canonical_database_owner_contract import (
    check_canonical_database_owner,
)
from oh_goon.runtime.database import LocalDatabase


def test_only_actual_local_database_write_cut_can_admit_atomic_domain_operation(tmp_path: Path):
    owner = LocalDatabase(tmp_path / "state.sqlite")
    other = LocalDatabase(owner.path)
    with owner.transaction(write=False) as read:
        with pytest.raises(RuntimeError, match="atomic write cut required"):
            owner.require_atomic_write_cut(read)
    with owner.transaction(write=True) as write:
        owner.require_atomic_write_cut(write)
        with pytest.raises(RuntimeError, match="atomic write cut required"):
            other.require_atomic_write_cut(write)
    # SQLite in_transaction by itself is NOT proof of the owner or admitted cut.
    raw = sqlite3.connect(owner.path, isolation_level=None)
    try:
        raw.execute("BEGIN IMMEDIATE")
        with pytest.raises(RuntimeError, match="atomic write cut required"):
            owner.require_atomic_write_cut(raw)
        raw.rollback()
    finally:
        raw.close()
    with pytest.raises(RuntimeError, match="atomic write cut required"):
        owner.require_atomic_write_cut(write)


def test_original_transaction_rolls_back_entire_atomic_domain_cut_and_never_retries(tmp_path: Path):
    owner = LocalDatabase(tmp_path / "state.sqlite")
    revision_before = owner.durable_revision()
    with pytest.raises(RuntimeError, match="issuer fence rejected"):
        with owner.transaction(write=True) as connection:
            owner.require_atomic_write_cut(connection)
            connection.execute("INSERT INTO locations VALUES ('lease', 1, 'now')")
            connection.execute("INSERT INTO locations VALUES ('run_admitted', 2, 'now')")
            connection.execute("INSERT INTO locations VALUES ('causal_admitted', 3, 'now')")
            raise RuntimeError("issuer fence rejected")
    with owner.transaction() as connection:
        assert connection.execute("SELECT count(*) FROM locations").fetchone()[0] == 0
    assert owner.durable_revision() == revision_before
    with owner.transaction(write=True) as connection:
        owner.require_atomic_write_cut(connection)
        connection.execute("INSERT INTO locations VALUES ('lease', 1, 'now')")
        connection.execute("INSERT INTO locations VALUES ('run_admitted', 2, 'now')")
    assert owner.durable_revision() == revision_before + 1


def test_nested_or_read_cut_cannot_be_promoted_to_second_write_cut(tmp_path: Path):
    owner = LocalDatabase(tmp_path / "state.sqlite")
    with owner.transaction(write=True) as connection:
        owner.require_atomic_write_cut(connection)
        with pytest.raises(RuntimeError, match="nested operational database transaction"):
            with owner.transaction(write=True):
                pass
        owner.require_atomic_write_cut(connection)
    with owner.transaction() as connection:
        with pytest.raises(RuntimeError, match="atomic write cut required"):
            owner.require_atomic_write_cut(connection)


def test_original_database_owner_inventory_passes():
    from oh_goon.repository_tooling.architecture.repository_layout import ROOT

    assert check_canonical_database_owner(ROOT) == []
