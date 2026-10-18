"""Protect M167 fail-before-work architecture statements and both QA owners."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_precommit_native_issuer_owner_and_regression_remain_present():
    assert (
        ROOT
        / "backend/tests/unit/services/test_group_a_precommit_native_issuer_migration.py"
    ).is_file()
    assert (
        ROOT / "backend/src/oh_goon/native_issuer/enforcer_lace_prework_capture.py"
    ).is_file()
