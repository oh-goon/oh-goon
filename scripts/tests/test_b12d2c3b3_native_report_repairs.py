"""Regression contracts for native WSL blockers discovered on B12D2C3B1."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def _root(tmp_path: Path) -> Path:
    (tmp_path / "scripts").mkdir()
    shutil.copyfile(
        ROOT / "scripts/source_transport_clean.sh",
        tmp_path / "scripts/source_transport_clean.sh",
    )
    (tmp_path / "backend").mkdir()
    (tmp_path / "backend/pyproject.toml").write_text(
        '[project]\nname="oh-goon"\n', encoding="utf-8"
    )
    (tmp_path / "VERSION").write_text("development\n", encoding="utf-8")
    return tmp_path


def _clean(root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(root / "scripts/source_transport_clean.sh")],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )


def test_transport_cleanup_needs_no_build_index_and_preserves_source(
    tmp_path: Path,
) -> None:
    root = _root(tmp_path)
    source = root / "backend/pyproject.toml"
    original = source.read_bytes()
    (root / "backend/pyproject.toml:Zone.Identifier").write_text(
        "MOTW", encoding="utf-8"
    )
    (root / "backend/rename.patch").write_text("transport", encoding="utf-8")
    (root / "backend/rename.patch.sha256").write_text("transport", encoding="utf-8")
    (root / "backend/.venv").mkdir()
    generated = root / "backend/.venv/transient.patch"
    generated.write_text("generated", encoding="utf-8")
    result = _clean(root)
    assert result.returncode == 0, result.stderr
    assert "Removed 3 disposable source transport artifacts" in result.stdout
    assert source.read_bytes() == original and generated.read_text() == "generated"
    assert not (root / "backend/rename.patch").exists()
    assert (
        _clean(root).stdout.strip() == "Removed 0 disposable source transport artifacts"
    )


def test_transport_cleanup_rejects_symlink_without_partial_mutation(
    tmp_path: Path,
) -> None:
    root = _root(tmp_path)
    victim = root / "backend" / "victim"
    victim.write_text("important", encoding="utf-8")
    sidecar = root / "backend" / "a.patch"
    sidecar.write_text("disposable", encoding="utf-8")
    (root / "backend" / "z.patch").symlink_to(victim)
    result = _clean(root)
    assert result.returncode == 2
    assert "non-regular transport artifact" in result.stderr
    assert victim.read_text() == "important" and sidecar.exists()


def test_native_report_ui_boundary_uses_shared_presentation_owner() -> None:
    shared = ROOT / "frontend/src/components/common/PlanInputDiagnostic.tsx"
    league = (
        ROOT / "frontend/src/features/league/components/SharedPlanPanel.tsx"
    ).read_text()
    assert shared.is_file()
    assert "@/components/common/PlanInputDiagnostic" in league
    assert "@/features/plans/components/PlanInputDiagnostic" not in league
    assert (
        "not_started_by_inspection" not in shared.read_text()
    )  # rendered schema, no fabricated outcome
