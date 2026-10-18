from __future__ import annotations

import pytest

from oh_goon.core.dependency_version_filter import version_matches_specifier
from oh_goon.core.errors import InvalidRequest


def test_numeric_dependency_version_specifiers_are_deterministic() -> None:
    assert version_matches_specifier("2.17.2", ">=2.0,<2.18.0") is True
    assert version_matches_specifier("2.18", ">=2.0,<2.18.0") is False
    assert version_matches_specifier("2.18.0", "==2.18") is True


def test_non_numeric_versions_remain_unclassified() -> None:
    assert version_matches_specifier("2.18.0rc1", "<2.18.0") is None


def test_unsupported_specifier_is_rejected_instead_of_guessed() -> None:
    with pytest.raises(InvalidRequest, match="stable numeric comparisons"):
        version_matches_specifier("2.17.2", "~=2.17")
