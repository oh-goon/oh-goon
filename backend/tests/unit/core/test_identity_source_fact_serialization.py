from pathlib import Path


def test_identity_model_dumps_exclude_computed_fields() -> None:
    root = Path(__file__).resolve().parents[3] / "src" / "oh_goon"
    targets = {
        "core/models/recovery_lifecycle.py": ("evidence_id", "admission_id"),
        "core/models/proof_boundary.py": ("proof_id", "grant_id"),
        "core/models/shard_execution.py": ("evidence_identity",),
        "integrations/fleet_plan_executor_trust.py": ("executor_trust_policy_id",),
    }
    for relative, owners in targets.items():
        text = (root / relative).read_text(encoding="utf-8")
        assert "exclude_computed_fields=True" in text, (relative, owners)


def test_snapshot_provenance_excludes_computed_fields() -> None:
    root = Path(__file__).resolve().parents[3] / "src" / "oh_goon"
    text = (root / "core/projections/fleet_snapshot_provenance.py").read_text(encoding="utf-8")
    assert text.count("exclude_computed_fields=True") >= 3
