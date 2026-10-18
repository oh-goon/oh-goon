"""One original QENV1 selector and CORE enrollment for workspace recovery."""
from pathlib import Path
from shutil import copy2

from oh_goon.repository_tooling.architecture.qualification_environment_contract import (
    check_qualification_environment_contract,
)
from oh_goon.repository_tooling.architecture.repository_layout import ROOT


def _fixture(tmp_path: Path) -> Path:
    (tmp_path / "scripts").mkdir()
    (tmp_path / ".checkpoint").mkdir()
    (tmp_path / ".checkpoint/CO_R2W1_SOURCE.sha256").write_text("fixture\n")
    copy2(ROOT / "Makefile", tmp_path / "Makefile")
    shell = tmp_path / "scripts/qualify_b12_development_wsl.sh"
    copy2(ROOT / "scripts/qualify_b12_development_wsl.sh", shell)
    return shell


def test_new_manifest_must_override_co_r01(tmp_path: Path) -> None:
    shell = _fixture(tmp_path)
    selector = "if [[ -f .checkpoint/CO_R2W1_SOURCE.sha256 ]]; then embedded='.checkpoint/CO_R2W1_SOURCE.sha256'; fi"
    text = shell.read_text()
    assert text.count(selector) == 1
    shell.write_text(text.replace(selector, "", 1))
    assert any("current manifest" in v.message for v in check_qualification_environment_contract(tmp_path))


def test_original_core_rejects_removal_of_real_crash_regressions(tmp_path: Path) -> None:
    shell = _fixture(tmp_path)
    text = shell.read_text()
    member = "  tests/unit/runtime/test_co_r2b_workspace_recovery.py \\\n"
    assert text.count(member) == 1
    shell.write_text(text.replace(member, "", 1))
    assert any("CO-R2W1" in v.message for v in check_qualification_environment_contract(tmp_path))
