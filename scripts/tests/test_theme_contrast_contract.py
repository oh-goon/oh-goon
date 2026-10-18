from oh_goon.repository_tooling import check_theme_contrast as contract


def test_theme_contrast_contract_protects_readability() -> None:
    assert contract.main() == 0


def test_contrast_ratio_matches_wcag_reference_cases() -> None:
    assert contract.contrast_ratio("#000000", "#ffffff") == 21.0
    assert contract.contrast_ratio("#ffffff", "#ffffff") == 1.0
