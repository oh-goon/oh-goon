from __future__ import annotations

from pathlib import Path

import pytest

from oh_goon.repository_tooling.architecture._launch_contract_foundation import (
    _documentation_violations,
    check_launch_foundation_model,
)

FORMER_PROSE_MARKERS = (
    "scripts/dev.sh",
    "cli_start.py",
    "ApplicationRuntime",
    "Launch Manifest is read-only projection",
    "configured `127.0.0.1:8787` or `127.0.0.1:5173` values are intent only",
    "Access tokens are capabilities",
)


def _write_document(root: Path, text: str) -> Path:
    path = root / "docs/development/LAUNCH_OWNERSHIP.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _write_foundation(root: Path) -> None:
    core = root / "backend/src/oh_goon/core"
    core.mkdir(parents=True, exist_ok=True)
    (core / "launch_manifest.py").write_text(
        'LAUNCH_MANIFEST_SCHEMA = "manifest/v1"\n'
        'LAUNCH_MANIFEST_KIND = "manifest"\n'
        "IDENTITY = (LAUNCH_MANIFEST_SCHEMA, LAUNCH_MANIFEST_KIND)\n",
        encoding="utf-8",
    )
    (core / "launch_events.py").write_text(
        'LAUNCH_EVENT_SCHEMA = "event/v1"\n'
        'LAUNCH_EVENT_KIND = "event"\n'
        "IDENTITY = (LAUNCH_EVENT_SCHEMA, LAUNCH_EVENT_KIND)\n",
        encoding="utf-8",
    )
    (core / "launch_projection.py").write_text("VALUE = 1\n", encoding="utf-8")
    (core / "process_birth.py").write_text("VALUE = 1\n", encoding="utf-8")


@pytest.mark.parametrize(
    "replacement",
    (
        "Contributor startup enters one owned local-launch frontdoor.",
        "Built local startup delegates native server mechanics without duplicating launch truth.",
        "Application background lifecycle remains below process ownership.",
        "The launch read model describes observed facts and never owns process execution.",
        "Configured endpoints express desired intent; only observation can prove a bound service.",
        "Browser credentials remain private capabilities and are never durable lifecycle truth.",
    ),
)
def test_launch_documentation_wording_is_not_architecture_authority(
    tmp_path: Path, replacement: str
) -> None:
    _write_document(tmp_path, f"# Launch ownership\n\n{replacement}\n")

    assert _documentation_violations(tmp_path) == []


def test_launch_documentation_still_has_to_exist(tmp_path: Path) -> None:
    violations = _documentation_violations(tmp_path)

    assert any("documentation is missing" in item.message for item in violations)


def test_launch_semantic_boundary_still_rejects_process_authority(
    tmp_path: Path,
) -> None:
    _write_document(tmp_path, "# Launch ownership\n\nWording is descriptive.\n")
    _write_foundation(tmp_path)
    path = tmp_path / "backend/src/oh_goon/core/launch_manifest.py"
    path.write_text(
        path.read_text(encoding="utf-8") + "import subprocess\n", encoding="utf-8"
    )

    violations = check_launch_foundation_model(tmp_path)

    assert any("must remain read-only" in item.message for item in violations)


def test_launch_schema_identity_owner_still_fails_closed(tmp_path: Path) -> None:
    _write_document(tmp_path, "# Launch ownership\n\nWording is descriptive.\n")
    _write_foundation(tmp_path)
    path = tmp_path / "backend/src/oh_goon/core/launch_manifest.py"
    path.write_text(
        'LAUNCH_MANIFEST_KIND = "manifest"\nVALUE = LAUNCH_MANIFEST_KIND\n',
        encoding="utf-8",
    )

    violations = check_launch_foundation_model(tmp_path)

    assert any("LAUNCH_MANIFEST_SCHEMA" in item.message for item in violations)


def test_launch_architecture_contains_no_exact_prose_authority() -> None:
    root = Path(__file__).resolve().parents[2]
    architecture = (
        root
        / "backend/src/oh_goon/repository_tooling/architecture/_launch_contract_foundation.py"
    ).read_text(encoding="utf-8")

    for marker in FORMER_PROSE_MARKERS:
        assert marker not in architecture
