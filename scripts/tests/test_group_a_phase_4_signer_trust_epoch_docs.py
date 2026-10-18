"""M165 architecture guardrails for durable signer trust epoch fencing."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "backend/src/oh_goon/runtime/enforcer_lace_signer_trust_epoch.py"
FRONT = ROOT / "backend/src/oh_goon/services/fleet_admission.py"


def test_trust_revision_is_single_commit_owner_and_no_fake_issuer() -> None:
    source = SOURCE.read_text()
    front = FRONT.read_text()
    assert "lace_signer_trust_epoch" in source
    database = (ROOT / "backend/src/oh_goon/runtime/database.py").read_text()
    assert "BEGIN IMMEDIATE" in database
    assert "BEGIN IMMEDIATE" not in source
    assert "require_atomic_write_cut" in source
    assert "WHERE singleton=1 AND trust_epoch=?" in source
    assert "connection is not self.connection" in source
    assert "def publish_with_fenced_signer_trust" in front
    assert "signer_authority.require_in" in front
    assert "commit_lease_is_current" in front
    assert "deployment_signer_policy_snapshot" in front
    assert "def execute(" not in source
