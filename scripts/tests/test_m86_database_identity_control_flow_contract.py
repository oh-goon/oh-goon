from __future__ import annotations

from pathlib import Path

from oh_goon.repository_tooling.architecture._structural_project_supply_contract import (
    check_project_supply_contract,
)


def _write_service(root: Path, relative: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "from __future__ import annotations\n\n"
        "def execute() -> str:\n"
        "    return 'database identity-loss semantics are executable behavior contracts'\n",
        encoding="utf-8",
    )


def test_database_identity_loss_architecture_does_not_freeze_python_control_flow(
    tmp_path: Path,
) -> None:
    _write_service(
        tmp_path,
        "backend/src/oh_goon/services/fleet_run_execution.py",
    )
    _write_service(
        tmp_path,
        "backend/src/oh_goon/services/repository_effect_execution.py",
    )

    assert check_project_supply_contract(tmp_path) == []
