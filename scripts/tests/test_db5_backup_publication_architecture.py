"""Keep DB5 in the original X0 and QENV1 authority paths."""

from __future__ import annotations

from pathlib import Path
from shutil import copy2

from test_db3_backup_owner_architecture import _db3_fixture

from oh_goon.repository_tooling.architecture.canonical_database_owner_contract import (
    check_canonical_database_owner,
)
from oh_goon.repository_tooling.architecture.qualification_environment_contract import (
    check_qualification_environment_contract,
)
from oh_goon.repository_tooling.architecture.repository_layout import ROOT


def test_db5_cannot_remove_publication_cleanup_from_original_owner(
    tmp_path: Path,
) -> None:
    package = _db3_fixture(tmp_path)
    (tmp_path / ".checkpoint/DB5_SOURCE.sha256").write_text("test only\n")
    source = package / "runtime/database.py"
    before = source.read_text()
    assert "target.unlink()" in before
    source.write_text(before.replace("target.unlink()", "pass", 1))
    assert any(
        "DB5" in issue.message for issue in check_canonical_database_owner(tmp_path)
    )


def test_db5_requires_one_original_core_ring(tmp_path: Path) -> None:
    (tmp_path / "scripts").mkdir()
    (tmp_path / ".checkpoint").mkdir()
    (tmp_path / ".checkpoint/DB5_SOURCE.sha256").write_text("test only\n")
    copy2(
        ROOT / "scripts/qualify_b12_development_wsl.sh",
        tmp_path / "scripts/qualify_b12_development_wsl.sh",
    )
    copy2(ROOT / "Makefile", tmp_path / "Makefile")
    shell = tmp_path / "scripts/qualify_b12_development_wsl.sh"
    before = shell.read_text()
    required = "  tests/unit/runtime/test_db5_backup_publication_failures.py \\\n"
    assert required in before
    shell.write_text(before.replace(required, "", 1))
    assert any(
        "DB5" in issue.message
        for issue in check_qualification_environment_contract(tmp_path)
    )
