"""DB6 negative architecture checks: no second owner or qualification front door."""

from __future__ import annotations

from pathlib import Path
from shutil import copy2

from oh_goon.repository_tooling.architecture.qualification_environment_contract import (
    check_qualification_environment_contract,
)
from oh_goon.repository_tooling.architecture.repository_layout import ROOT


def _fixture(tmp_path: Path) -> Path:
    (tmp_path / "scripts").mkdir()
    (tmp_path / ".checkpoint").mkdir()
    (tmp_path / ".checkpoint/DB6_SOURCE.sha256").write_text("fixture only\n")
    copy2(
        ROOT / "scripts/qualify_b12_development_wsl.sh",
        tmp_path / "scripts/qualify_b12_development_wsl.sh",
    )
    copy2(ROOT / "Makefile", tmp_path / "Makefile")
    return tmp_path / "scripts/qualify_b12_development_wsl.sh"


def test_db6_native_witness_cannot_be_removed_from_original_core(tmp_path: Path) -> None:
    shell = _fixture(tmp_path)
    contents = shell.read_text()
    assert "db6_native_tests+=(tests/native/test_db6_kernel_enospc_witness.py)" in contents
    shell.write_text(
        contents.replace(
            "db6_native_tests+=(tests/native/test_db6_kernel_enospc_witness.py)", ":", 1
        )
    )
    assert any(
        "DB6" in violation.message
        for violation in check_qualification_environment_contract(tmp_path)
    )


def test_db6_latest_manifest_must_override_db5(tmp_path: Path) -> None:
    shell = _fixture(tmp_path)
    contents = shell.read_text()
    assert (
        "if [[ -f .checkpoint/DB6_SOURCE.sha256 ]]; then embedded='.checkpoint/DB6_SOURCE.sha256'; fi"
        in contents
    )
    shell.write_text(
        contents.replace(
            "if [[ -f .checkpoint/DB6_SOURCE.sha256 ]]; then embedded='.checkpoint/DB6_SOURCE.sha256'; fi",
            "",
            1,
        )
    )
    assert any(
        "manifest" in violation.message
        for violation in check_qualification_environment_contract(tmp_path)
    )


def test_db6_preflight_cannot_leave_core(tmp_path: Path) -> None:
    shell = _fixture(tmp_path)
    contents = shell.read_text()
    shell.write_text(
        contents.replace("  tests/unit/runtime/test_db6_native_fault_preflight.py \\\n", "", 1)
    )
    assert any(
        "DB6" in violation.message
        for violation in check_qualification_environment_contract(tmp_path)
    )
