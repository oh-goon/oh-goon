"""Static anti-duplication mutations for SQLite-only DB0/DB1A."""
from __future__ import annotations

from pathlib import Path

import pytest

from oh_goon.repository_tooling.architecture.canonical_database_owner_contract import (
    check_canonical_database_owner,
    database_authority_census,
)


def fixture(tmp_path: Path) -> Path:
    from tests.factories.database_architecture import copy_database_architecture_sources

    package = copy_database_architecture_sources(tmp_path)
    assert check_canonical_database_owner(tmp_path) == []
    return package


@pytest.mark.parametrize("payload,kind", [
    ("import sqlite3\ndef commit(path):\n    return sqlite3.connect(path)\n", "connection"),
    ("from sqlite3 import connect\ndef writer(path):\n    return connect(path)\n", "connection"),
    ("def commit(connection):\n    connection.execute('BEGIN IMMEDIATE')\n", "transaction"),
    ("def commit(connection):\n    connection.commit()\n", "transaction"),
    ("def commit(connection):\n    connection.executescript('CREATE TABLE x(y)')\n", "schema_script"),
])
def test_new_independent_runtime_database_writer_rejected(tmp_path: Path, payload: str, kind: str):
    package = fixture(tmp_path)
    (package / "services/shadow_db.py").write_text(payload)
    assert any(f"unowned {kind}" in violation.message
               for violation in check_canonical_database_owner(tmp_path))


def test_current_exceptions_are_semantically_scoped_not_generic_bypass(tmp_path: Path):
    package = fixture(tmp_path)
    (package / "runtime/storage_health.py").write_text(
        "import sqlite3\ndef probe(path):\n    return sqlite3.connect(path)\n"
    )
    assert not check_canonical_database_owner(tmp_path)
    (package / "runtime/enforcer_lace_journal.py").write_text(
        "import sqlite3\ndef wrong(path):\n    return sqlite3.connect(path)\n"
    )
    assert any("unowned connection" in violation.message
               for violation in check_canonical_database_owner(tmp_path))


def test_live_admission_write_cut_proof_cannot_be_removed(tmp_path: Path):
    package = fixture(tmp_path)
    (package / "services/fleet_admission.py").write_text(
        "def publish_atomic_fleet_prework_ingress(connection, operational_journal):\n"
        "    assert connection.in_transaction\n"
    )
    assert any("original Fleet admission" in violation.message
               for violation in check_canonical_database_owner(tmp_path))


def test_original_source_has_one_classified_sqlite_census():
    from oh_goon.repository_tooling.architecture.repository_layout import ROOT
    census = database_authority_census(ROOT)
    assert {name for name, _, kind in census if kind == "connection"} == {
        "runtime/database.py", "runtime/storage_health.py", "runtime/_database_policy.py", "scout/database.py",
    }
    assert check_canonical_database_owner(ROOT) == []


@pytest.mark.parametrize("mutation,diagnostic", [
    ("second-writer", "unowned connection"),
    ("lost-durability", "DB2"),
    ("lost-backup-verification", "DB5"),
    ("missing-owner", "missing canonical operational SQLite owner"),
])
def test_clean_source_keeps_current_database_architecture_active(
    tmp_path: Path, mutation: str, diagnostic: str,
) -> None:
    from tests.factories.x0_architecture import copy_x0_architecture_sources

    from oh_goon.repository_tooling.architecture.x0_single_execution_contract import (
        check_x0_single_execution_path,
    )

    package = copy_x0_architecture_sources(tmp_path)
    assert not (tmp_path / ".checkpoint").exists()
    assert check_x0_single_execution_path(tmp_path) == []
    database = package / "runtime/database.py"
    if mutation == "second-writer":
        (package / "services/second_writer.py").write_text(
            "import sqlite3\ndef write(path):\n    return sqlite3.connect(path)\n"
        )
    elif mutation == "missing-owner":
        database.unlink()
    else:
        before = (
            "self._require_durable_connection_policy(connection)"
            if mutation == "lost-durability" else "self.verify_offline_backup(receipt)"
        )
        original = database.read_text()
        assert before in original
        database.write_text(original.replace(before, "None"))
    assert any(diagnostic in issue.message
               for issue in check_x0_single_execution_path(tmp_path))
