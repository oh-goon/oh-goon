"""DB4 negative architecture mutations in the original X0/QENV1 guards."""

from __future__ import annotations

from pathlib import Path

import pytest
from tests.factories.x0_architecture import replace_python_syntax
from tests.unit.repository_tooling.test_db3_backup_owner_architecture import _db3_fixture

from oh_goon.repository_tooling.architecture.canonical_database_owner_contract import (
    check_canonical_database_owner,
)
from oh_goon.repository_tooling.architecture.qualification_environment_contract import (
    check_qualification_environment_contract,
)
from oh_goon.repository_tooling.architecture.repository_layout import ROOT


@pytest.mark.parametrize(
    "needle,replacement",
    [
        (
            "self.verify_offline_backup(LocalStateBackupReceipt(",
            "self._skip_backup_check(LocalStateBackupReceipt(",
        ),
        ("if observed_hash.hexdigest() != receipt.sha256:", "if False:"),
        ("revision = read_durable_revision(reader)", "revision = receipt.durable_revision"),
        (
            'cookie = int(reader.execute("PRAGMA application_id").fetchone()[0])',
            "cookie = receipt.database_identity_cookie",
        ),
    ],
)
def test_db4_rejects_snapshot_verifier_shortcut(
    tmp_path: Path, needle: str, replacement: str
) -> None:
    package = _db3_fixture(tmp_path)
    (tmp_path / ".checkpoint/DB4_SOURCE.sha256").write_text("test-only\n")
    source = package / "runtime/database.py"
    text = source.read_text()
    source.write_text(replace_python_syntax(text, needle, replacement))
    assert any("DB4" in issue.message for issue in check_canonical_database_owner(tmp_path))


def test_db4_requires_exact_original_qenv1_core_ring(tmp_path: Path) -> None:
    from shutil import copy2

    (tmp_path / "scripts").mkdir()
    copy2(
        ROOT / "scripts/qualify_b12_development_wsl.sh",
        tmp_path / "scripts/qualify_b12_development_wsl.sh",
    )
    copy2(ROOT / "Makefile", tmp_path / "Makefile")
    (tmp_path / ".checkpoint").mkdir()
    (tmp_path / ".checkpoint/DB4_SOURCE.sha256").write_text("test-only\n")
    shell = tmp_path / "scripts/qualify_b12_development_wsl.sh"
    shell.write_text(
        shell.read_text().replace(
            "  tests/unit/runtime/test_db4_offline_recovery_certification.py \\\n", "", 1
        )
    )
    assert any(
        "DB4" in issue.message for issue in check_qualification_environment_contract(tmp_path)
    )
