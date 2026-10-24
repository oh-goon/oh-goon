"""Diagnostic ownership mutations and one current-source composition proof."""

from pathlib import Path

import pytest

from oh_goon.repository_tooling.architecture.x0_single_execution_contract import (
    DIAGNOSTIC_ENTRYPOINT,
    DIAGNOSTIC_MODULE,
    _check_diagnostic_observer_ownership,
    check_x0_single_execution_path,
)
from tests.factories.x0_architecture import write_x0_rule_sources


@pytest.fixture
def diagnostic_source(tmp_path: Path) -> Path:
    package = write_x0_rule_sources(
        tmp_path,
        {
            "backend/pyproject.toml": (
                f'[project.scripts]\n"{DIAGNOSTIC_ENTRYPOINT}" = "{DIAGNOSTIC_MODULE}:main"\n'
            ),
            "backend/src/oh_goon/repository_tooling/local_lace_physical_preflight.py": (
                "def main(): pass\n"
            ),
        },
    )
    assert _check_diagnostic_observer_ownership(tmp_path) == []
    return package


def test_current_execution_architecture_composes_all_rules() -> None:
    assert check_x0_single_execution_path() == []


@pytest.mark.parametrize(
    "text",
    [
        "from oh_goon.repository_tooling import local_lace_physical_preflight as observer",
        f"import {DIAGNOSTIC_MODULE} as observer",
        f'import importlib; importlib.import_module("{DIAGNOSTIC_MODULE}")',
        f'subprocess.run("{DIAGNOSTIC_ENTRYPOINT}")',
    ],
)
def test_production_owners_cannot_invoke_diagnostic(
    tmp_path: Path, diagnostic_source: Path, text: str
) -> None:
    target = diagnostic_source / "services/example.py"
    target.parent.mkdir()
    target.write_text(text + "\n")
    assert _check_diagnostic_observer_ownership(tmp_path)


def test_diagnostic_cannot_import_execution_authority(
    tmp_path: Path, diagnostic_source: Path
) -> None:
    (
        diagnostic_source / "repository_tooling/local_lace_physical_preflight.py"
    ).write_text("from oh_goon.runtime import enforcer_lace_fleet_resource_owner\n")
    assert _check_diagnostic_observer_ownership(tmp_path)


def test_alternate_diagnostic_entrypoint_owner_is_rejected(
    tmp_path: Path, diagnostic_source: Path
) -> None:
    (tmp_path / "backend/pyproject.toml").write_text(
        f'[project.scripts]\n"{DIAGNOSTIC_ENTRYPOINT}" = "oh_goon.services.example:main"\n'
    )
    assert _check_diagnostic_observer_ownership(tmp_path)
