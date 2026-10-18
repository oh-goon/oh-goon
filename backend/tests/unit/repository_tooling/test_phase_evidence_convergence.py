from __future__ import annotations

from pathlib import Path

from oh_goon.repository_tooling import phase_evidence_convergence


def _write_phase(root: Path, *, version: str = "7.8.9") -> None:
    (root / "docs/development").mkdir(parents=True)
    (root / "VERSION").write_text(version + "\n", encoding="utf-8")
    (root / "CHECKPOINT_NOT_RELEASE.md").write_text(
        f"## Current development checkpoint: {version}\n\n"
        f"Current status: **{version} DEVELOPMENT / TEST / NATIVE QUALIFICATION REQUIRED**\n",
        encoding="utf-8",
    )
    token = version.replace(".", "_")
    (root / f"docs/development/PHASE_{token}_TEST.md").write_text(
        f"# {version} — Test Phase\n", encoding="utf-8"
    )
    (root / "CHANGELOG.md").write_text(f"## {version} DEVELOPMENT — Test Phase\n", encoding="utf-8")


def test_phase_evidence_accepts_the_same_checkpoint_format_as_release_seal(tmp_path: Path) -> None:
    _write_phase(tmp_path)
    assert phase_evidence_convergence.phase_evidence_issues(tmp_path) == []


def test_phase_evidence_rejects_stale_phase_document_heading(tmp_path: Path) -> None:
    _write_phase(tmp_path)
    phase = next((tmp_path / "docs/development").glob("PHASE_7_8_9_*.md"))
    phase.write_text("# Phase 7.8.9 — stale format\n", encoding="utf-8")
    assert "current phase document heading does not match VERSION" in (
        phase_evidence_convergence.phase_evidence_issues(tmp_path)
    )


def test_phase_evidence_rejects_missing_current_changelog_heading(tmp_path: Path) -> None:
    _write_phase(tmp_path)
    (tmp_path / "CHANGELOG.md").write_text("## 7.8.8 DEVELOPMENT — stale\n", encoding="utf-8")
    assert "CHANGELOG lacks current DEVELOPMENT heading" in (
        phase_evidence_convergence.phase_evidence_issues(tmp_path)
    )


def test_explicit_phase_validation_refuses_checkout_without_archive_metadata(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    (tmp_path / "VERSION").write_text("7.8.9\n", encoding="utf-8")
    monkeypatch.setattr(phase_evidence_convergence, "ROOT", tmp_path)

    assert phase_evidence_convergence.main() == 1

    output = capsys.readouterr().out
    assert "PHASE EVIDENCE CONVERGENCE: FAIL" in output
    assert "local archive metadata CHECKPOINT_NOT_RELEASE.md" in output
    assert "local archive metadata CHANGELOG.md" in output
    assert "PHASE EVIDENCE CONVERGENCE: PASS" not in output
