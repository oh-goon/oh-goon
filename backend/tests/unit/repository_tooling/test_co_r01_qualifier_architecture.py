"""CO-R01 source selector and one original QENV1 CORE path."""
from pathlib import Path
from shutil import copy2

from oh_goon.repository_tooling.architecture.qualification_environment_contract import (
    check_qualification_environment_contract,
)
from oh_goon.repository_tooling.architecture.repository_layout import ROOT


def _fixture(tmp_path: Path) -> Path:
    (tmp_path / 'scripts').mkdir()
    (tmp_path / '.checkpoint').mkdir()
    (tmp_path / '.checkpoint/CO_R01_SOURCE.sha256').write_text('fixture\n')
    copy2(ROOT / 'Makefile', tmp_path / 'Makefile')
    shell = tmp_path / 'scripts/qualify_b12_development_wsl.sh'
    copy2(ROOT / 'scripts/qualify_b12_development_wsl.sh', shell)
    return shell


def test_latest_checkpoint_is_selected(tmp_path):
    path = _fixture(tmp_path)
    data = path.read_text()
    token = "if [[ -f .checkpoint/CO_R01_SOURCE.sha256 ]]; then embedded='.checkpoint/CO_R01_SOURCE.sha256'; fi"
    assert data.count(token) == 1
    path.write_text(data.replace(token, '', 1))
    assert any('manifest' in v.message for v in check_qualification_environment_contract(tmp_path))


def test_existing_core_must_enroll_negative_call_edge_tests(tmp_path):
    path = _fixture(tmp_path)
    data = path.read_text()
    token = '  tests/unit/repository_tooling/test_co_r01_lifecycle_architecture.py \\\n'
    assert data.count(token) == 1
    path.write_text(data.replace(token, '', 1))
    assert any('CO-R01' in v.message for v in check_qualification_environment_contract(tmp_path))
