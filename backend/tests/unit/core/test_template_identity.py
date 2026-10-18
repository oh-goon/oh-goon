from oh_goon.core.template_identity import import_step_id


def test_import_step_id_preserves_short_canonical_alias() -> None:
    assert import_step_id("python/quality", 1) == "python-quality"
    assert import_step_id("python/quality", 2) == "python-quality-2"


def test_import_step_id_bounds_long_valid_goon_key_without_widening_step_contract() -> None:
    first = import_step_id("oh-goon/browser-localhost-transport-preflight", 1)
    second = import_step_id("oh-goon/browser-localhost-transport-preflight", 2)
    assert len(first) <= 40
    assert len(second) <= 40
    assert first != second
    assert first == import_step_id("oh-goon/browser-localhost-transport-preflight", 1)
    assert second.endswith("-2")


def test_import_step_id_hash_distinguishes_long_names_with_same_prefix() -> None:
    left = import_step_id("oh-goon/browser-localhost-transport-preflight", 1)
    right = import_step_id("oh-goon/browser-localhost-transport-probe", 1)
    assert left != right
