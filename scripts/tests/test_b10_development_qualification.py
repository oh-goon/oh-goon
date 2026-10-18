"""B10: easy development witness must not confer certification authority."""

from __future__ import annotations

from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/qualify_b10_development_wsl.sh"


def _mini_source(tmp_path: Path) -> Path:
    root = tmp_path / "candidate"
    (root / "scripts").mkdir(parents=True)
    shutil.copy2(SCRIPT, root / "scripts/qualify_b10_development_wsl.sh")
    (root / "README.md").write_text("working development tree\n", encoding="utf-8")
    return root


def _run(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(root / "scripts/qualify_b10_development_wsl.sh"), *args],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=15,
    )


def test_witness_help_requires_no_manually_placed_manifest(tmp_path):
    root = _mini_source(tmp_path)
    result = _run(root, "--help")
    assert result.returncode == 0
    assert "No external manifest is needed" in result.stdout
    assert list(tmp_path.iterdir()) == [root]


def test_working_source_can_be_inventoried_without_a_manifest(tmp_path):
    root = _mini_source(tmp_path)
    result = _run(root, "--snapshot")
    assert result.returncode == 0, result.stderr
    assert "./README.md" in result.stdout
    assert "./scripts/qualify_b10_development_wsl.sh" in result.stdout
    assert not (root / ".checkpoint").exists()


def test_unsafe_symlink_is_rejected_even_in_development(tmp_path):
    root = _mini_source(tmp_path)
    (root / "foreign").symlink_to(root / "README.md")
    result = _run(root, "--snapshot")
    assert result.returncode != 0
    assert "BLOCKED: unsafe source entry" in result.stderr


def test_generated_environment_symlinks_are_not_mistaken_for_source(tmp_path):
    root = _mini_source(tmp_path)
    (root / "backend/.venv/bin").mkdir(parents=True)
    (root / "backend/.venv/bin/python").symlink_to("/usr/bin/python3")
    result = _run(root, "--snapshot")
    assert result.returncode == 0, result.stderr
    assert ".venv" not in result.stdout


def test_non_wsl_does_not_create_evidence_or_execute_project(tmp_path):
    # Native WSL runs are exercised by the user/WSL host, not simulated here.
    kernel = Path("/proc/sys/kernel/osrelease").read_text().lower()
    if "microsoft" in kernel or "wsl" in kernel:
        import pytest

        pytest.skip("native WSL available: non-WSL denial not applicable")
    root = _mini_source(tmp_path)
    expected = tmp_path / "new-evidence"
    result = _run(root, str(expected))
    assert result.returncode == 3
    assert not expected.exists()
    assert "native WSL is required" in result.stderr


def test_make_target_remains_development_only():
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    assert "qualify-development-wsl:" in makefile
    # B11 becomes the default while the B10 witness remains reproducible.
    assert "scripts/qualify_b12_development_wsl.sh" in makefile
    assert SCRIPT.exists()
    witness = SCRIPT.read_text(encoding="utf-8")
    assert "external_issuer=NOT_PROVEN" in witness
    assert "physical_lace=NOT_PROVEN" in witness
    assert "app_pass=NOT_CLAIMED" in witness
    assert "release_authority=NONE" in witness
