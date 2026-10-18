from __future__ import annotations

from pathlib import Path

import oh_goon.repository_tooling.architecture.diagnostic_ownership_contract as contract


_REQUIRED_SOURCES = (
    "services/fleet_run_execution.py",
    "services/game_tape_live_output.py",
    "integrations/fleet_plan_step_output_observation.py",
)


def _write_contract_root(
    tmp_path: Path,
    monkeypatch,
    *,
    source_body: str,
) -> Path:
    package = tmp_path / "oh_goon"
    model = package / "core/models/diagnostic.py"
    port = package / "core/ports/diagnostics.py"
    router = package / "runtime/diagnostics.py"
    for path in (model, port, router):
        path.parent.mkdir(parents=True, exist_ok=True)
    model.write_text(
        "class DiagnosticEvidence:\n"
        "    @classmethod\n"
        "    def renamed_exception_capture(cls, exc, **fields):\n"
        "        return cls()\n",
        encoding="utf-8",
    )
    port.write_text("class DiagnosticEmitter:\n    pass\n", encoding="utf-8")
    router.write_text("class DiagnosticRouter:\n    pass\n", encoding="utf-8")
    for relative in _REQUIRED_SOURCES:
        path = package / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source_body, encoding="utf-8")
    monkeypatch.setattr(contract, "BACKEND_PACKAGE_ROOT", package)
    return package


def _messages() -> list[str]:
    return [violation.message for violation in contract.check_diagnostic_ownership_contract()]


def test_private_diagnostic_constructor_rename_is_architecture_invariant(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _write_contract_root(
        tmp_path,
        monkeypatch,
        source_body=(
            "from oh_goon.core.models.diagnostic import DiagnosticEvidence as Evidence\n"
            "def degrade(diagnostics, exc):\n"
            "    diagnostics.emit(Evidence.renamed_exception_capture(exc, owner='semantic-owner'))\n"
        ),
    )

    assert _messages() == []


def test_module_alias_and_direct_constructor_are_equivalent_owner_projections(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _write_contract_root(
        tmp_path,
        monkeypatch,
        source_body=(
            "import oh_goon.core.models.diagnostic as diagnostic_contract\n"
            "def degrade(diagnostics, exc):\n"
            "    diagnostics.emit(diagnostic_contract.DiagnosticEvidence())\n"
        ),
    )

    assert _messages() == []


def test_missing_diagnostic_semantic_owner_fails_closed(tmp_path: Path, monkeypatch) -> None:
    _write_contract_root(
        tmp_path,
        monkeypatch,
        source_body=(
            "class LocalDiagnostic:\n"
            "    pass\n"
            "def degrade(diagnostics, exc):\n"
            "    diagnostics.emit(LocalDiagnostic())\n"
        ),
    )

    assert any("diagnostic semantic owner" in message for message in _messages())


def test_importing_owner_but_emitting_duplicate_local_authority_fails_closed(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _write_contract_root(
        tmp_path,
        monkeypatch,
        source_body=(
            "from oh_goon.core.models.diagnostic import DiagnosticEvidence\n"
            "class LocalDiagnostic:\n"
            "    pass\n"
            "def degrade(diagnostics, exc):\n"
            "    diagnostics.emit(LocalDiagnostic())\n"
        ),
    )

    assert any("diagnostic semantic owner" in message for message in _messages())


def test_architecture_contains_no_private_diagnostic_constructor_authority() -> None:
    root = Path(__file__).resolve().parents[4]
    architecture = (
        root
        / "backend/src/oh_goon/repository_tooling/architecture/diagnostic_ownership_contract.py"
    ).read_text(encoding="utf-8")

    assert "DiagnosticEvent.from_exception" not in architecture
