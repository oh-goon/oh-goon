"""P3B2B: new REAL kernel tests share the existing opt-in original launcher.

These are structural/negative tests only; WSL opt-in actually runs them.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from oh_goon.repository_tooling.architecture.repository_layout import ROOT
from oh_goon.repository_tooling.architecture.x0_single_execution_contract import (
    _check_p3b2b_original_native_lifecycle,
)

WITNESS = ROOT / 'backend/tests/native/test_p1b27b2b3b2b3a_effectful_mount.py'


def test_one_native_lifecycle_owner_and_original_selection():
    assert _check_p3b2b_original_native_lifecycle(ROOT) == []
    script = (ROOT / 'scripts/qualify_b12_development_wsl.sh').read_text()
    assert script.count('observer_tests+=(tests/native/test_p1b27b2b3b2b3a_effectful_mount.py)') == 1
    assert 'QUALIFY_B27_EFFECTFUL:-0' in script
    assert 'BLOCKED: effectful B27 witness requires real bwrap' in script
    assert 'test_p3b2b_original_native_lifecycle' in script


@pytest.mark.parametrize('removed', (
    '_original_effectful_fd_mount_command(',
    'require_process_group_quiescence=True',
    'CommandCancelledError',
    'CommandAuthorityTerminatedError',
    'CommandTimeoutExpired',
    'cause=\'trust_revoke\'',
    'final_process_group_quiescent',
    'P3_NATIVE_CHILD_READY',
    '/proc/self/mountinfo',
))
def test_architecture_detects_bypassed_native_lifecycle(tmp_path: Path, removed: str):
    source = WITNESS.read_text(encoding='utf-8')
    assert removed in source
    altered = tmp_path / 'backend/tests/native/test_p1b27b2b3b2b3a_effectful_mount.py'
    altered.parent.mkdir(parents=True)
    head, native = source.split('def test_original_effectful_fd_mount_lifecycle_is_supervised', 1)
    assert removed in native
    altered.write_text(head + 'def test_original_effectful_fd_mount_lifecycle_is_supervised' + native.replace(removed, 'REMOVED_NATIVE_REQUIREMENT', 1))
    assert _check_p3b2b_original_native_lifecycle(tmp_path)


def test_no_separate_p3_launcher_or_false_physical_absence():
    source = WITNESS.read_text(encoding='utf-8')
    assert source.count('def _original_effectful_fd_mount_command(') == 1
    assert source.count('backend._command(') == 1
    assert source.count('OriginalBindSourceLease.pin_prepared(request)') == 1
    assert 'subprocess.Popen(' not in source
    assert 'pytest.skip(' not in source
    assert 'teardown_proven = True' not in source
    assert 'physical_absence_proven = True' not in source
