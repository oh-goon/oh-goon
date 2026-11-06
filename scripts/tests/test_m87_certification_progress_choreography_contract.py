from __future__ import annotations

from pathlib import Path

from oh_goon.repository_tooling.architecture._structural_project_supply_contract import (
    check_project_supply_contract,
)


def _write_progress_contract_fixture(root: Path, *, progress_protocol: str) -> None:
    plan_cli = root / "backend/src/oh_goon/cli_runtime_plans.py"
    plan_cli.parent.mkdir(parents=True, exist_ok=True)
    plan_cli.write_text(
        "\n".join(
            (
                'MILESTONE = "Native activity observed"',
                'HEARTBEAT = "Run active"',
                f'PROGRESS_PROTOCOL = "{progress_protocol}"',
                "",
            )
        ),
        encoding="utf-8",
    )


def test_certification_progress_architecture_accepts_semantics_without_control_flow_choreography(
    tmp_path: Path,
) -> None:
    _write_progress_contract_fixture(tmp_path, progress_protocol="OH_GOON_PROGRESS_FD")

    assert check_project_supply_contract(tmp_path) == []


def test_certification_progress_architecture_still_requires_progress_protocol_identity(
    tmp_path: Path,
) -> None:
    _write_progress_contract_fixture(tmp_path, progress_protocol="PRESENTATION_FD")

    violations = check_project_supply_contract(tmp_path)

    assert len(violations) == 1
    assert "OH_GOON_PROGRESS_FD" in violations[0].message
