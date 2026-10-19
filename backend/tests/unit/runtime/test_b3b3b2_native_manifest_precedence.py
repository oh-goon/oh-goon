"""Regression: never silently select a historical source manifest on native WSL."""

from oh_goon.repository_tooling.architecture.qualification_environment_contract import (
    check_qualification_environment_contract,
)
from oh_goon.repository_tooling.architecture.repository_layout import ROOT

SELECTOR = "if [[ -f .checkpoint/B3B3B4_SOURCE.sha256 ]]; then embedded='.checkpoint/B3B3B4_SOURCE.sha256'; fi"


def test_native_qualifier_selects_current_manifest():
    assert not check_qualification_environment_contract(ROOT)


def test_architecture_rejects_missing_or_earlier_selector(tmp_path):
    shell = (ROOT / "scripts/qualify_b12_development_wsl.sh").read_text()
    makefile = (ROOT / "Makefile").read_text()
    path = tmp_path / "scripts/qualify_b12_development_wsl.sh"
    path.parent.mkdir(parents=True)
    (tmp_path / "Makefile").write_text(makefile)
    latest = tmp_path / ".checkpoint/B3B3B4_SOURCE.sha256"
    latest.parent.mkdir(parents=True)
    latest.write_text("test")
    path.write_text(shell.replace(SELECTOR, "", 1))
    assert any(
        "current manifest" in x.message for x in check_qualification_environment_contract(tmp_path)
    )
    older = "if [[ -f .checkpoint/AG02B2B3B3B3_SOURCE.sha256 ]]; then embedded='.checkpoint/AG02B2B3B3B3_SOURCE.sha256'; fi"
    path.write_text(shell.replace(SELECTOR, "", 1).replace(older, SELECTOR + "\n" + older, 1))
    assert any(
        "current manifest" in x.message for x in check_qualification_environment_contract(tmp_path)
    )


def test_native_prework_ring_requires_manifest_and_signed_mount_tests():
    qualifier = (ROOT / "scripts/qualify_b12_development_wsl.sh").read_text()
    ring = qualifier.split("run_ring AGENT_PREWORK", 1)[1].split("\nfi", 1)[0]
    assert "test_b3b3b2_native_manifest_precedence.py" in ring
    assert "test_b3b3b4_workspace_signed_mount.py" in ring
    assert "test_ag02b2b3b3b2_signed_mount_consumption.py" in ring
