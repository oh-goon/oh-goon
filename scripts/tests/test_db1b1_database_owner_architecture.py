"""DB1B1 original X0: named atomic operations cannot silently lose their owner."""

from __future__ import annotations

from pathlib import Path
from shutil import copyfile

import pytest
from test_db0db1a_database_owner_architecture import fixture

from oh_goon.repository_tooling.architecture.canonical_database_owner_contract import (
    check_canonical_database_owner,
)
from oh_goon.repository_tooling.architecture.repository_layout import ROOT


def _source(tmp_path: Path) -> Path:
    package = fixture(tmp_path)
    for name in ("enforcer_lace_signer_trust_epoch.py", "enforcer_lace_journal.py"):
        copyfile(
            ROOT / "backend/src/oh_goon/runtime" / name, package / "runtime" / name
        )
    checkpoint = tmp_path / ".checkpoint"
    checkpoint.mkdir()
    (checkpoint / "DB1B1_SOURCE.sha256").write_text("selected source marker\n")
    assert check_canonical_database_owner(tmp_path) == []
    return package


@pytest.mark.parametrize(
    "mutation",
    [
        (
            "database.py",
            "connection.set_authorizer(deny_transaction)",
            "connection.set_authorizer(None)",
        ),
        ("database.py", "self.require_atomic_write_cut(connection)", "pass"),
        (
            "database.py",
            "SignerTrustEpochAuthority(connection, owner=self).revoke_in",
            "SignerTrustEpochAuthority(connection).revoke_in",
        ),
        (
            "database.py",
            "with self._guard_atomic_domain_participant(connection):",
            "if True:",
        ),
        (
            "enforcer_lace_signer_trust_epoch.py",
            "self._database_owner.require_atomic_write_cut(connection)",
            "pass",
        ),
        (
            "enforcer_lace_signer_trust_epoch.py",
            "self._require_participant_cut(connection)",
            "pass",
        ),
    ],
)
def test_owner_mutations_are_rejected(
    tmp_path: Path, mutation: tuple[str, str, str]
) -> None:
    package = _source(tmp_path)
    file, before, after = mutation
    target = package / "runtime" / file
    source = target.read_text()
    assert before in source
    target.write_text(source.replace(before, after))
    assert any(
        "DB1B1" in violation.message
        for violation in check_canonical_database_owner(tmp_path)
    )


def test_no_second_sqlite_engine_or_writer_is_added(tmp_path: Path) -> None:
    package = _source(tmp_path)
    (package / "runtime/second_owner.py").write_text(
        "import sqlite3\ndef new_writer(path):\n    return sqlite3.connect(path)\n"
    )
    assert any(
        "unowned connection" in violation.message
        for violation in check_canonical_database_owner(tmp_path)
    )
