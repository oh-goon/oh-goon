"""DB3 remains part of the one original X0 database authority gate."""
from __future__ import annotations

from pathlib import Path

import pytest
from tests.unit.repository_tooling.test_db1b2_database_owner_architecture import _source

from oh_goon.repository_tooling.architecture.canonical_database_owner_contract import (
    check_canonical_database_owner,
)


def _db3_fixture(tmp_path: Path) -> Path:
    package = _source(tmp_path)
    (tmp_path / ".checkpoint/DB2_SOURCE.sha256").write_text("test-only marker\n")
    (tmp_path / ".checkpoint/DB3_SOURCE.sha256").write_text("test-only marker\n")
    assert not check_canonical_database_owner(tmp_path)
    return package


@pytest.mark.parametrize("target,old,replacement", [
    ("runtime/database.py", 'connection.execute("VACUUM INTO ?", (str(staged),))', 'connection.execute("SELECT 1")'),
    ("runtime/database.py", 'os.link(staged, target, follow_symlinks=False)', 'os.replace(staged, target)'),
    ("scout/database.py", 'self._require_disposable_identity()', 'pass # bypass Scout identity'),
    ("runtime/database.py", '_require_private_backup_directory(requested.parent)', 'requested.parent'),
])
def test_owner_shortcut_fails_original_x0(tmp_path: Path, target: str, old: str, replacement: str) -> None:
    package = _db3_fixture(tmp_path)
    path = package / target
    text = path.read_text()
    assert old in text
    path.write_text(text.replace(old, replacement, 1))
    assert any("DB3" in issue.message for issue in check_canonical_database_owner(tmp_path))
