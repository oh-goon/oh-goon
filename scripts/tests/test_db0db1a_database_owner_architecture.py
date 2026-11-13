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


@pytest.mark.parametrize(
    "payload,kind",
    [
        (
            "import sqlite3\ndef commit(path):\n    return sqlite3.connect(path)\n",
            "connection",
        ),
        (
            "from sqlite3 import connect\ndef writer(path):\n    return connect(path)\n",
            "connection",
        ),
        (
            "def commit(connection):\n    connection.execute('BEGIN IMMEDIATE')\n",
            "transaction",
        ),
        ("def commit(connection):\n    connection.commit()\n", "transaction"),
        (
            "def commit(connection):\n    connection.executescript('CREATE TABLE x(y)')\n",
            "schema_script",
        ),
    ],
)
def test_new_independent_runtime_database_writer_rejected(
    tmp_path: Path, payload: str, kind: str
) -> None:
    package = fixture(tmp_path)
    (package / "services/shadow_db.py").write_text(payload)
    assert any(
        f"unowned {kind}" in violation.message
        for violation in check_canonical_database_owner(tmp_path)
    )


def test_current_exceptions_are_semantically_scoped_not_generic_bypass(
    tmp_path: Path,
) -> None:
    package = fixture(tmp_path)
    (package / "runtime/storage_health.py").write_text(
        "import sqlite3\ndef probe(path):\n    return sqlite3.connect(path)\n"
    )
    assert not check_canonical_database_owner(tmp_path)
    (package / "runtime/enforcer_lace_journal.py").write_text(
        "import sqlite3\ndef wrong(path):\n    return sqlite3.connect(path)\n"
    )
    assert any(
        "unowned connection" in violation.message
        for violation in check_canonical_database_owner(tmp_path)
    )


def test_live_admission_write_cut_proof_cannot_be_removed(tmp_path: Path) -> None:
    package = fixture(tmp_path)
    (package / "services/fleet_admission.py").write_text(
        "def publish_atomic_fleet_prework_ingress(connection, operational_journal):\n"
        "    assert connection.in_transaction\n"
    )
    assert any(
        "original Fleet admission" in violation.message
        for violation in check_canonical_database_owner(tmp_path)
    )


def test_original_source_has_one_classified_sqlite_census() -> None:
    from oh_goon.repository_tooling.architecture.repository_layout import ROOT

    census = database_authority_census(ROOT)
    assert {name for name, _, kind in census if kind == "connection"} == {
        "runtime/database.py",
        "runtime/storage_health.py",
        "runtime/_database_policy.py",
        "scout/database.py",
    }
    assert check_canonical_database_owner(ROOT) == []


def test_connection_census_follows_producer_alias_and_parameter_scope(
    tmp_path: Path,
) -> None:
    package = tmp_path / "backend/src/oh_goon"
    package.mkdir(parents=True)
    source = package / "candidate.py"
    source.write_text(
        "import sqlite3 as native\n"
        "open_database = native.connect\n"
        "def writer(path):\n"
        "    return open_database(path)\n"
        "def unrelated(open_database, path):\n"
        "    return open_database(path)\n",
        encoding="utf-8",
    )
    assert database_authority_census(tmp_path) == [("candidate.py", 4, "connection")]


def test_connection_census_does_not_recreate_rebound_import_authority(
    tmp_path: Path,
) -> None:
    package = tmp_path / "backend/src/oh_goon"
    package.mkdir(parents=True)
    source = package / "candidate.py"
    source.write_text(
        "from sqlite3 import connect as dial\n"
        "def writer(path):\n"
        "    return dial(path)\n",
        encoding="utf-8",
    )
    assert database_authority_census(tmp_path) == [("candidate.py", 3, "connection")]
    source.write_text(
        "from sqlite3 import connect as dial\n"
        "def writer(dial, path):\n"
        "    return dial(path)\n",
        encoding="utf-8",
    )
    assert database_authority_census(tmp_path) == []


@pytest.mark.parametrize(
    "conditional_source",
    [
        "if True:\n import sqlite3 as native\ndef writer(path):\n return native.connect(path)\n",
        "if True:\n from sqlite3 import connect as dial\ndef writer(path):\n return dial(path)\n",
    ],
)
def test_unknown_connection_origin_cannot_certify_sqlite_absence(
    tmp_path: Path, conditional_source: str
) -> None:
    package = tmp_path / "backend/src/oh_goon"
    package.mkdir(parents=True)
    source = package / "candidate.py"
    source.write_text(
        "import sqlite3\ndef writer(path):\n return sqlite3.connect(path)\n",
        encoding="utf-8",
    )
    assert database_authority_census(tmp_path) == [("candidate.py", 3, "connection")]
    source.write_text(conditional_source, encoding="utf-8")

    with pytest.raises(ValueError, match="SQLite connection exclusion is unproven"):
        database_authority_census(tmp_path)


@pytest.mark.parametrize(
    "mutation,diagnostic",
    [
        ("second-writer", "unowned connection"),
        ("lost-durability", "DB2"),
        ("lost-backup-verification", "DB5"),
        ("missing-owner", "missing canonical operational SQLite owner"),
    ],
)
def test_clean_source_keeps_current_database_architecture_active(
    tmp_path: Path,
    mutation: str,
    diagnostic: str,
) -> None:
    from tests.factories.database_architecture import copy_database_architecture_sources

    package = copy_database_architecture_sources(tmp_path)
    assert check_canonical_database_owner(tmp_path) == []
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
            if mutation == "lost-durability"
            else "self.verify_offline_backup(receipt)"
        )
        original = database.read_text()
        assert before in original
        database.write_text(original.replace(before, "None"))
    assert any(
        diagnostic in issue.message
        for issue in check_canonical_database_owner(tmp_path)
    )


@pytest.mark.parametrize("captured_packets", [False, True])
def test_connection_census_reuses_existing_native_import_declarations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, captured_packets: bool
) -> None:
    from typing import Any

    from oh_goon.integrations.hashmarks import HashmarksSourceEvidence
    from oh_goon.repository_tooling.architecture import common

    package = tmp_path / "backend/src/oh_goon"
    package.mkdir(parents=True)
    source = package / "candidate.py"
    source.write_text(
        "from sqlite3 import connect as dial\n"
        "def writer(path):\n    return dial(path)\n",
        encoding="utf-8",
    )
    native_packet = HashmarksSourceEvidence.packet
    reads: list[HashmarksSourceEvidence] = []

    def packet(evidence: HashmarksSourceEvidence) -> dict[str, Any]:
        reads.append(evidence)
        return native_packet(evidence)

    with common.python_source_snapshot(tmp_path, captured_packets=captured_packets):
        common.python_semantic_facts(source, imported_only=True)
        monkeypatch.setattr(HashmarksSourceEvidence, "packet", packet)
        assert database_authority_census(tmp_path) == [
            ("candidate.py", 3, "connection")
        ]
        assert reads == []
