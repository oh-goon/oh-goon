from pathlib import Path


def test_review_keeps_canonical_owner_and_qualification_blockers_visible():
    root = Path(__file__).resolve().parents[4]
    text = (root / "docs/development/TEMPLATE_INSPECTION_ADVERSARIAL_REVIEW.md").read_text()
    assert "materialize_projected_inputs()" in text
    assert "no native wsl" in text.lower()
    assert "Do not seal" in text
    assert "test_every_shipped_plan_inspection_matches_canonical_materialization" in text
