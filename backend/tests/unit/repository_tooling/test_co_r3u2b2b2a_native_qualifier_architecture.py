"""CO-R3U2B2B2A negative controls for the single existing native qualifier."""
from __future__ import annotations

from pathlib import Path

import pytest

from oh_goon.repository_tooling.architecture.x0_single_execution_contract import (
    _check_co_r3u2b2b2a_native_qualification_cut,
)

ROOT = Path(__file__).resolve().parents[4]
SCRIPT = 'scripts/qualify_b12_development_wsl.sh'
NATIVE = 'backend/tests/native/test_co_r3u2b2b2a_native_refusal.py'


def _fixture(tmp_path: Path, *, path: str | None = None, before: str = '', after: str = '') -> Path:
    for rel in (SCRIPT, NATIVE):
        dest = tmp_path / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        contents = (ROOT / rel).read_text(encoding='utf-8')
        if path == rel:
            assert contents.count(before) >= 1
            contents = contents.replace(before, after, 1)
        dest.write_text(contents, encoding='utf-8')
    return tmp_path


def test_original_native_qualifier_is_non_issuing(tmp_path: Path) -> None:
    assert _check_co_r3u2b2b2a_native_qualification_cut(_fixture(tmp_path)) == []


@pytest.mark.parametrize(('source', 'before', 'after'), [
    (SCRIPT, 'BLOCKED_NON_ISSUING_LACE', 'READY_TO_EXECUTE'),
    (SCRIPT, 'phase=BLOCKED_UNPROVISIONED_KERNEL_CUSTODY', 'phase=PASS'),
    (SCRIPT, '    exit 3\n  fi\nfi\n# Host-owned', '    :\n  fi\nfi\n# Host-owned'),
    (SCRIPT, '  "${r3u2b2b2a_refusal_tests[@]}" \\\n', ''),
    (NATIVE, 'cg.require_independent_native_parent_custody(', 'cg.require_alternate_authority('),
    (NATIVE, 'test_live_kernel_original_lease_refuses_before_lock_and_syscall', 'test_legacy_positive_lease'),
])
def test_migration_to_stale_native_execution_fails_x0(
    tmp_path: Path, source: str, before: str, after: str,
) -> None:
    assert _check_co_r3u2b2b2a_native_qualification_cut(
        _fixture(tmp_path, path=source, before=before, after=after),
    )
