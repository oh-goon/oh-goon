"""DB1B2 X0 gate: fixture SQL never becomes a runtime transaction owner."""

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
    (checkpoint / "DB1B1_SOURCE.sha256").write_text("source ancestry proof\n")
    (checkpoint / "DB1B2_SOURCE.sha256").write_text("source ancestry proof\n")
    assert check_canonical_database_owner(tmp_path) == []
    return package


@pytest.mark.parametrize(
    "module, old, mutated",
    [
        (
            "enforcer_lace_journal.py",
            'raise CausalJournalError("standalone causal transaction forbidden; use LocalDatabase owner")',
            'self.connection.execute("BEGIN IMMEDIATE")',
        ),
        (
            "enforcer_lace_signer_trust_epoch.py",
            'raise TrustProtocolError("standalone signer transaction forbidden; use LocalDatabase owner")',
            'self.connection.execute("BEGIN IMMEDIATE")',
        ),
        (
            "database.py",
            "self._verify_exact_causal_admission(",
            "pass  # defanged durable reread\n                # self._verify_exact_causal_admission(",
        ),
    ],
)
def test_raw_transaction_or_missing_durable_reread_fails_architecture(
    tmp_path: Path,
    module: str,
    old: str,
    mutated: str,
) -> None:
    package = _source(tmp_path)
    target = package / "runtime" / module
    source = target.read_text()
    assert old in source
    target.write_text(source.replace(old, mutated))
    assert check_canonical_database_owner(tmp_path)


def test_test_only_fixture_import_is_forbidden_in_runtime_source(
    tmp_path: Path,
) -> None:
    package = _source(tmp_path)
    target = package / "runtime/database.py"
    target.write_text(
        target.read_text()
        + "\nfrom tests.fixtures.db1b2_legacy_sqlite import CausalRunJournal\n"
    )
    assert any(
        "test-only SQLite" in violation.message
        for violation in check_canonical_database_owner(tmp_path)
    )
