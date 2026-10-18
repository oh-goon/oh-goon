"""CO-R3U2B2B2B1: original native positive witness refuses before env work.

These are source-slice tests of the EXISTING qualifier, not a second qualifier
and not evidence of independent kernel custody. All mutations are confined to
fixture directories and never exercise native cgroup/network effects.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from oh_goon.repository_tooling.architecture.x0_single_execution_contract import (
    _check_co_r3u2b2b2a_native_qualification_cut,
)

ROOT = Path(__file__).resolve().parents[4]
QUALIFIER = 'scripts/qualify_b12_development_wsl.sh'
NATIVE_REFUSAL = 'backend/tests/native/test_co_r3u2b2b2a_native_refusal.py'


def _admission_script() -> str:
    source = (ROOT / QUALIFIER).read_text(encoding='utf-8')
    return ('# CO-R3U2B2B2B1: pre-environment admission.' + source.split('# CO-R3U2B2B2B1: pre-environment admission.', 1)[1]).split(
        '# Host-owned diagnostics only.', 1,
    )[0]


def _run_slice(tmp_path: Path, native_root: str | None) -> subprocess.CompletedProcess[str]:
    marker = tmp_path / '.checkpoint' / 'CO_R3U2B2B2A_SOURCE.sha256'
    marker.parent.mkdir(parents=True)
    marker.write_text('marker only; not a custody grant\n', encoding='utf-8')
    evidence = tmp_path / 'evidence'
    evidence.mkdir()
    env = dict(os.environ)
    env.pop('QUALIFY_R3N_CGROUP_ROOT', None)
    if native_root is not None:
        env['QUALIFY_R3N_CGROUP_ROOT'] = native_root
    script = 'set -Eeuo pipefail\nevidence="$1"\nphase=ADMISSION\nlast_result=3\n' + _admission_script()
    result = subprocess.run(
        ['bash', '-c', script, 'admission', str(evidence)],
        cwd=tmp_path, env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        timeout=10, check=False,
    )
    if native_root:
        assert not (evidence / 'UV_LOCKED_SYNC.log').exists()
        assert not (evidence / 'UV_ENV_VERIFY.log').exists()
        assert not (evidence / 'CORE.xml').exists()
    return result


def test_requested_legacy_positive_native_refuses_without_environment_work(tmp_path: Path) -> None:
    result = _run_slice(tmp_path, '/caller/provided/unqualified-root')
    assert result.returncode == 3
    assert 'BLOCKED' in result.stderr
    evidence = tmp_path / 'evidence'
    assert (evidence / 'R3N_NATIVE_CGROUP_STATUS.txt').read_text() == (
        'status=BLOCKED_NON_ISSUING_LACE\n'
        'reason=legacy_positive_native_witness_incompatible_with_current_physical_gate\n'
        'physical_lace=NOT_PROVEN\n'
    )
    assert (evidence / 'R3U2B2B2B1_ADMISSION_STATUS.txt').read_text() == (
        'blocked_before_environment=YES\nlegacy_native_positive=REFUSED\n'
    )


def test_unrequested_positive_witness_preserves_original_core_enrollment(tmp_path: Path) -> None:
    result = _run_slice(tmp_path, None)
    assert result.returncode == 0
    evidence = tmp_path / 'evidence'
    assert not list(evidence.iterdir())


def _fixture(tmp_path: Path, change: str, replacement: str) -> Path:
    for rel in (QUALIFIER, NATIVE_REFUSAL):
        dest = tmp_path / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        text = (ROOT / rel).read_text(encoding='utf-8')
        if rel == QUALIFIER:
            assert text.count(change) == 1
            text = text.replace(change, replacement, 1)
        dest.write_text(text, encoding='utf-8')
    return tmp_path


@pytest.mark.parametrize(('before', 'after'), [
    ("qualify_host_sample 'UV_SYNC_BEFORE'", "qualify_host_sample 'MOVED_BEFORE_ADMISSION'"),
    ('blocked_before_environment=YES', 'blocked_before_environment=NO'),
    ('    exit 3\n  fi\nfi\n# Host-owned', '    :\n  fi\nfi\n# Host-owned'),
    ('> "$evidence/PRE.txt"', '> "$evidence/MISSING_PRE.txt"'),
    ("if [[ -f .checkpoint/CO_R3U2B2B2B1_SOURCE.sha256 ]]; then",
     "if [[ -f .checkpoint/FAKE_SOURCE.sha256 ]]; then"),
])
def test_original_architecture_rejects_early_refusal_regressions(
    tmp_path: Path, before: str, after: str,
) -> None:
    assert _check_co_r3u2b2b2a_native_qualification_cut(_fixture(tmp_path, before, after))
