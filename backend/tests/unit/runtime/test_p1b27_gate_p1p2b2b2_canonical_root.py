"""P1P2B2B2: one runtime-root capture entrypoint, no witness verifier bypass."""
from __future__ import annotations

from pathlib import Path

import pytest

from oh_goon.repository_tooling.architecture.repository_layout import ROOT
from oh_goon.repository_tooling.architecture.x0_single_execution_contract import (
    _check_p1p2b2b2_canonical_runtime_root,
)

WITNESS = ROOT / 'backend/tests/native/test_p1b27b2b3b2b3a_effectful_mount.py'


def test_canonical_runtime_witness_contract():
    assert _check_p1p2b2b2_canonical_runtime_root(ROOT) == []


@pytest.mark.parametrize('old,new', (
    ('capture_runtime_root(', 'capture_not_canonical_root('),
    ('verify_runtime_root(runtime)', 'pass # snapshot identity removed'),
    ('request.attempt.runtime_root = runtime', "request.attempt.runtime_root.root = Path('/')"),
    ('request.attempt.host_tools = tools', 'request.attempt.host_tools = ()'),
    ('snapshot_host_path(', 'snapshot_not_verified('),
    ('backend._command(', 'fake_command('),
))
def test_canonical_runtime_witness_drift_is_rejected(tmp_path: Path, old: str, new: str):
    out = tmp_path / 'backend/tests/native/test_p1b27b2b3b2b3a_effectful_mount.py'
    out.parent.mkdir(parents=True)
    text = WITNESS.read_text(encoding='utf-8')
    assert old in text
    out.write_text(text.replace(old, new, 1), encoding='utf-8')
    assert _check_p1p2b2b2_canonical_runtime_root(tmp_path)
