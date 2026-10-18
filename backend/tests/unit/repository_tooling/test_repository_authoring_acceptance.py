from __future__ import annotations

from oh_goon.repository_tooling.acceptance.repository_authoring import certify_repository_authoring


def test_repository_authoring_real_fallback_acceptance() -> None:
    # Real temporary Git repository + TOML -> VERSION fallback. This guards the exact
    # P1 path rather than only unit-testing the input resolver in isolation.
    certify_repository_authoring()
