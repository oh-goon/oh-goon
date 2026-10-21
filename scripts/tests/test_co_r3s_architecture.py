"""CO-R3S mutation guards for the original single LACE physical observer."""

from __future__ import annotations

from pathlib import Path

from oh_goon.repository_tooling.architecture.co_r01_lifecycle_contract import (
    check_co_r3s_observation,
)
from oh_goon.repository_tooling.architecture.repository_layout import ROOT


def _fixture(tmp_path: Path) -> Path:
    for name in (
        "backend/src/oh_goon/integrations/linux_physical_observer.py",
        "scripts/tests/test_co_r3s_pinned_cgroup_observation.py",
        "scripts/tests/test_co_r3s_architecture.py",
        "scripts/qualify_b12_development_wsl.sh",
    ):
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((ROOT / name).read_bytes())
    return tmp_path


def test_original_x0_rejects_unbound_cgroup_controls(tmp_path: Path) -> None:
    root = _fixture(tmp_path)
    assert not check_co_r3s_observation(root)
    path = root / "backend/src/oh_goon/integrations/linux_physical_observer.py"
    content = path.read_text()
    assert content.count("dir_fd=directory_fd") == 1
    path.write_text(content.replace("dir_fd=directory_fd", "dir_fd=None", 1))
    assert any("handle-bound" in x.message for x in check_co_r3s_observation(root))


def test_original_x0_rejects_missing_identity_recheck_and_core(tmp_path: Path) -> None:
    root = _fixture(tmp_path)
    assert not check_co_r3s_observation(root)
    path = root / "backend/src/oh_goon/integrations/linux_physical_observer.py"
    path.write_text(
        path.read_text().replace(
            '"original cgroup path replaced during observation"', '"no recheck"', 1
        )
    )
    assert any("reverify identity" in x.message for x in check_co_r3s_observation(root))
    shell = root / "scripts/qualify_b12_development_wsl.sh"
    shell.write_text(
        shell.read_text().replace(
            "  ../scripts/tests/test_co_r3s_pinned_cgroup_observation.py \\\n", "", 1
        )
    )
    assert any(
        "CORE witness missing" in x.message for x in check_co_r3s_observation(root)
    )
