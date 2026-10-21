"""Negative mutations preserve CO-R3R within the original X0 architecture checker."""

from __future__ import annotations

from pathlib import Path

from oh_goon.repository_tooling.architecture.co_r01_lifecycle_contract import (
    check_co_r3r_visibility,
)


def _minimal(tmp_path: Path) -> Path:
    from oh_goon.repository_tooling.architecture.repository_layout import ROOT

    members = (
        "backend/src/oh_goon/integrations/linux_physical_observer.py",
        "scripts/tests/test_co_r3r_visible_holder_race.py",
        "scripts/tests/test_co_r3r_observer_architecture.py",
        "backend/tests/native/test_co_r3r_real_visible_holder.py",
        "scripts/qualify_b12_development_wsl.sh",
    )
    for name in members:
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((ROOT / name).read_bytes())
    return tmp_path


def test_original_x0_detects_removed_final_foreign_pid_roster_check(
    tmp_path: Path,
) -> None:
    root = _minimal(tmp_path)
    assert not check_co_r3r_visibility(root)
    target = root / "backend/src/oh_goon/integrations/linux_physical_observer.py"
    text = target.read_text()
    anchor = 'if final_pids != pids:\n            raise ValueError("visible foreign mount process roster changed during census")'
    assert text.count(anchor) == 1
    target.write_text(
        text.replace(anchor, 'if False:\n            raise ValueError("unreachable")')
    )
    assert any(
        "changed final PID roster" in item.message
        for item in check_co_r3r_visibility(root)
    )


def test_original_x0_rejects_missing_core_and_manifest_selector(tmp_path: Path) -> None:
    root = _minimal(tmp_path)
    assert not check_co_r3r_visibility(root)
    shell = root / "scripts/qualify_b12_development_wsl.sh"
    shell.write_text(
        shell.read_text().replace(
            "  tests/native/test_co_r3r_real_visible_holder.py \\\n", "", 1
        )
    )
    assert any(
        "CORE witness missing" in item.message for item in check_co_r3r_visibility(root)
    )
