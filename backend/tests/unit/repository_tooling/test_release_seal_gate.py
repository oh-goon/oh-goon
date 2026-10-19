from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from oh_goon.repository_tooling.release import seal_gate


def test_module_environment_rejects_ambient_pythonpath_authority(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("PYTHONPATH", "/ambient/host/source")
    monkeypatch.setenv("PYTHONDONTWRITEBYTECODE", "0")

    environment = seal_gate._module_environment(tmp_path)

    assert environment["PYTHONPATH"] == str(tmp_path / "backend" / "src")
    assert "/ambient/host/source" not in environment["PYTHONPATH"]
    assert environment["PYTHONDONTWRITEBYTECODE"] == "1"


def test_source_seal_gate_runs_complete_authorities_in_order(tmp_path: Path, monkeypatch) -> None:
    observed: list[tuple[Path, str, tuple[str, ...]]] = []

    def fake_run(root: Path, module: str, *args: str) -> None:
        observed.append((root, module, args))

    monkeypatch.setattr(seal_gate, "_run_module", fake_run)
    (tmp_path / "VERSION").write_text("7.8.9\n", encoding="utf-8")
    (tmp_path / "CHECKPOINT_NOT_RELEASE.md").write_text(
        "## Current development checkpoint: 7.8.9\n\n"
        "Current status: **7.8.9 DEVELOPMENT — native qualification deferred**\n",
        encoding="utf-8",
    )

    seal_gate.require_source_seal_gate(tmp_path)

    root = tmp_path.resolve()
    assert observed == [
        (root, "oh_goon.repository_tooling.phase_evidence_convergence", ()),
        (root, "oh_goon.repository_tooling.check_architecture", ()),
        (root, "oh_goon.repository_tooling.generation.check_generated", ()),
        (
            root,
            "oh_goon.repository_tooling.goon_static_admission",
            ("--root", str(root)),
        ),
    ]


def test_packaged_architecture_gate_runs_from_exact_extracted_root(
    tmp_path: Path, monkeypatch
) -> None:
    archive_path = tmp_path / "source.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("oh-goon-7.8.9/VERSION", "7.8.9\n")
        archive.writestr("oh-goon-7.8.9/backend/src/marker.txt", "source\n")
    observed: list[tuple[Path, str]] = []

    def fake_run(root: Path, module: str, *args: str) -> None:
        assert not args
        assert (root / "VERSION").read_text(encoding="utf-8") == "7.8.9\n"
        assert (root / "backend/src/marker.txt").is_file()
        observed.append((root, module))

    monkeypatch.setattr(seal_gate, "_run_module", fake_run)

    seal_gate.require_packaged_architecture_gate(archive_path)

    assert len(observed) == 1
    assert observed[0][1] == "oh_goon.repository_tooling.check_architecture"


def test_packaged_architecture_gate_refuses_archive_traversal(tmp_path: Path) -> None:
    archive_path = tmp_path / "source.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("oh-goon-7.8.9/../escape.txt", "no\n")

    with pytest.raises(ValueError, match="unsafe source archive member"):
        seal_gate.require_packaged_architecture_gate(archive_path)


def test_source_seal_gate_rejects_stale_checkpoint_version(tmp_path: Path) -> None:
    (tmp_path / "VERSION").write_text("7.8.9\n", encoding="utf-8")
    (tmp_path / "CHECKPOINT_NOT_RELEASE.md").write_text(
        "## Current development checkpoint: 7.8.8\n\n"
        "Current status: **7.8.8 DEVELOPMENT — native qualification deferred**\n",
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="stale current-checkpoint heading"):
        seal_gate.require_source_seal_gate(tmp_path)


def test_source_seal_refuses_missing_archive_metadata_before_any_gate_runs(
    tmp_path: Path, monkeypatch
) -> None:
    (tmp_path / "VERSION").write_text("7.8.9\n", encoding="utf-8")
    observed: list[str] = []
    monkeypatch.setattr(
        seal_gate, "_run_module", lambda _root, module, *args: observed.append(module)
    )

    with pytest.raises(RuntimeError, match="requires local archive metadata"):
        seal_gate.require_source_seal_gate(tmp_path)

    assert observed == []


def test_source_seal_gate_rejects_multiple_current_status_owners(tmp_path: Path) -> None:
    (tmp_path / "VERSION").write_text("7.8.9\n", encoding="utf-8")
    (tmp_path / "CHECKPOINT_NOT_RELEASE.md").write_text(
        "## Current development checkpoint: 7.8.9\n\n"
        "Current status: **7.8.9 DEVELOPMENT — current**\n"
        "Current status: **7.8.8 DEVELOPMENT — stale historical owner**\n",
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="stale current-status version"):
        seal_gate.require_source_seal_gate(tmp_path)


def test_source_seal_gate_rejects_checkpoint_phase_history_bloat(tmp_path: Path) -> None:
    (tmp_path / "VERSION").write_text("7.8.9\n", encoding="utf-8")
    lines = [
        "## Current development checkpoint: 7.8.9",
        "",
        "Current status: **7.8.9 DEVELOPMENT — native qualification deferred**",
    ] + [f"historical detail {index}" for index in range(57)]
    (tmp_path / "CHECKPOINT_NOT_RELEASE.md").write_text(
        "\n".join(lines) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="refuses embedded phase history"):
        seal_gate.require_source_seal_gate(tmp_path)
