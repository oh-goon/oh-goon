"""CO-R3P X0: the original physical effect callback cannot silently drop revocation."""

from __future__ import annotations

from pathlib import Path

import pytest
from tests.unit.repository_tooling.test_co_r01_lifecycle_architecture import _fixture

from oh_goon.repository_tooling.architecture.co_r01_lifecycle_contract import (
    _LIFECYCLE,
    _R3_QUALIFIER,
    check_co_r01_lifecycle_edges,
)


def test_original_live_effect_signer_epoch_guard_passes(tmp_path: Path) -> None:
    assert check_co_r01_lifecycle_edges(_fixture(tmp_path)) == []


@pytest.mark.parametrize(
    ("relative", "before", "after"),
    [
        (
            _LIFECYCLE,
            "SignerTrustEpochAuthority(connection).require_in(",
            "SignerTrustEpochAuthority(connection).active() # dropped required transaction fence\n            #(",
        ),
        (
            _LIFECYCLE,
            "require_original_admitted_signer_epoch_in(\n                CausalRunJournal(connection)",
            "skip_original_signed_epoch_in(\n                CausalRunJournal(connection)",
        ),
        (
            _LIFECYCLE,
            "binding.configured.require_unchanged(binding.current)",
            "pass # removed configuration fence",
        ),
        (
            _R3_QUALIFIER,
            "tests/unit/services/test_co_r3p_original_fleet_effect_revocation.py",
            "tests/unit/services/test_co_r3o_native_witness_contract.py",
        ),
        (
            _R3_QUALIFIER,
            "if [[ -f .checkpoint/CO_R3P_SOURCE.sha256 ]]; then embedded='.checkpoint/CO_R3P_SOURCE.sha256'; fi",
            "# CO-R3P selector removed",
        ),
    ],
)
def test_original_fleet_effect_epoch_mutation_rejected(
    tmp_path: Path,
    relative: str,
    before: str,
    after: str,
) -> None:
    root = _fixture(tmp_path)
    target = root / relative
    contents = target.read_text(encoding="utf-8")
    assert before in contents
    if relative == _LIFECYCLE:
        prefix, marker, suffix = contents.partition("    def require_execution_authority_in(")
        assert marker and before in suffix
        contents = prefix + marker + suffix.replace(before, after, 1)
    else:
        contents = contents.replace(before, after, 1)
    target.write_text(contents, encoding="utf-8")
    assert any(problem.path == target for problem in check_co_r01_lifecycle_edges(root))
