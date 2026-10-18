"""Fail before native work when CO-R2c expiry write-cut authority is bypassed."""

import pytest
from tests.unit.repository_tooling.test_co_r01_lifecycle_architecture import _fixture

from oh_goon.repository_tooling.architecture.co_r01_lifecycle_contract import (
    _EVIDENCE,
    check_co_r01_lifecycle_edges,
)


def test_co_r2c_original_owner_write_cut_passes(tmp_path):
    assert check_co_r01_lifecycle_edges(_fixture(tmp_path)) == []


@pytest.mark.parametrize('old,new', [
    ('accepted_at = self._validate_durable_rows(', 'accepted_at = self._skip_cut_expiry('),
    ('accepted_at=accepted_at,', 'accepted_at=utc_now(),'),
    ('accepted_at = utc_now()', 'accepted_at = lease.expires_at'),
    ('accepted_at >= persisted_expires_at', 'accepted_at > persisted_expires_at'),
    ('persisted_expires_at = datetime.fromisoformat(lease_row["expires_at"])',
     'persisted_expires_at = lease.expires_at'),
    ('issued_at, expires_at', 'issued_at'),
    ('self._persist_acceptance(connection, value)', 'self._other_acceptance_path(connection, value)'),
])
def test_co_r2c_bypass_mutant_rejected(tmp_path, old, new):
    root = _fixture(tmp_path)
    p = root / _EVIDENCE
    text = p.read_text()
    assert old in text
    p.write_text(text.replace(old, new, 1))
    assert any(v.path == p for v in check_co_r01_lifecycle_edges(root))
