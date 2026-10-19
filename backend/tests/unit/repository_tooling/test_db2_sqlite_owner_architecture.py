"""DB2: X0 rejects removal of the original owner's durability admission."""

from pathlib import Path

import pytest
from tests.unit.repository_tooling.test_db1b2_database_owner_architecture import _source

from oh_goon.repository_tooling.architecture.canonical_database_owner_contract import (
    check_canonical_database_owner,
)


@pytest.mark.parametrize(
    "old, replacement",
    [
        (
            "self._require_durable_connection_policy(connection)",
            "pass  # weakened connection policy",
        ),
        ('connection.execute("PRAGMA synchronous")', 'connection.execute("SELECT 2")'),
        ('connection.execute("PRAGMA foreign_keys")', 'connection.execute("SELECT 1")'),
    ],
)
def test_durability_fence_removed_from_owner_is_rejected(
    tmp_path: Path, old: str, replacement: str
) -> None:
    package = _source(tmp_path)
    marker = tmp_path / ".checkpoint/DB2_SOURCE.sha256"
    marker.write_text("synthetic marker for mutation test\n")
    assert check_canonical_database_owner(tmp_path) == []
    target = package / "runtime/database.py"
    source = target.read_text()
    assert old in source
    if old == "self._require_durable_connection_policy(connection)":
        # DB3 adds an earlier offline maintenance policy check. Mutate the
        # original transaction guard, not the unrelated backup call.
        prefix, sep, suffix = source.rpartition(old)
        assert sep
        target.write_text(prefix + replacement + suffix)
    else:
        target.write_text(source.replace(old, replacement, 1))
    assert any("DB2" in item.message for item in check_canonical_database_owner(tmp_path))
