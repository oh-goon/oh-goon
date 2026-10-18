from oh_goon.repository_tooling.architecture.diagnostic_ownership_contract import (
    check_diagnostic_ownership_contract,
)


def test_diagnostic_ownership_contract() -> None:
    assert check_diagnostic_ownership_contract() == []
