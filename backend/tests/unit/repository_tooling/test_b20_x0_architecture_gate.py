"""X0 anti-duplication architecture regressions: one semantic authority."""
from pathlib import Path

import pytest

from oh_goon.repository_tooling.architecture.x0_single_execution_contract import (
    check_x0_single_execution_path,
)


def _fixture(tmp_path: Path) -> Path:
    from tests.factories.x0_architecture import copy_x0_architecture_sources

    return copy_x0_architecture_sources(tmp_path)


def test_original_project_has_no_diagnostic_to_runtime_authority_edge():
    assert check_x0_single_execution_path() == []


@pytest.mark.parametrize('text', [
    'from oh_goon.repository_tooling import local_lace_physical_preflight as observer',
    'import oh_goon.repository_tooling.local_lace_physical_preflight as observer',
    'import importlib; importlib.import_module("oh_goon.repository_tooling.local_lace_physical_preflight")',
    'subprocess.run("oh-goon-local-lace-physical-preflight")',
])
def test_production_owners_cannot_invoke_diagnostic(tmp_path, text):
    p = _fixture(tmp_path)
    (p / 'services/fleet_run_lifecycle.py').write_text(text + '\n')
    assert any('authority import/dispatch' in e.message for e in check_x0_single_execution_path(tmp_path))


def test_diagnostic_cannot_import_real_execution_owner(tmp_path):
    p = _fixture(tmp_path)
    (p / 'repository_tooling/local_lace_physical_preflight.py').write_text(
        'from oh_goon.runtime.enforcer_lace_fleet_resource_owner import OriginalFleetLocalLaceReservation\n'
    )
    assert any('diagnostic owner may not import' in e.message for e in check_x0_single_execution_path(tmp_path))


def test_alternate_cli_owner_rejected_before_work(tmp_path):
    p = _fixture(tmp_path)
    (tmp_path / 'backend/pyproject.toml').write_text('''[project.scripts]
"oh-goon-local-lace-physical-preflight" = "oh_goon.services.fleet_run_lifecycle:main"
''')
    assert any('must have exactly' in e.message for e in check_x0_single_execution_path(tmp_path))


def test_no_code_change_to_canonical_owners_in_b20():
    # The first X0 slice blocks known routes; comprehensive dynamic ingress,
    # replay, and exactly-once effect tracing remain Gate A/B/C work.
    assert check_x0_single_execution_path() == []
