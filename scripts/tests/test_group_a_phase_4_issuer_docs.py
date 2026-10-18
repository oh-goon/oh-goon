"""M151 source-backed documentary invariants for unsealed issuer gate."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_new_owner_and_test_are_present_without_second_admission_service():
    enforcer = (ROOT / "backend/src/oh_goon/services/fleet_admission.py").read_text()
    owner = (
        ROOT / "backend/src/oh_goon/core/enforcer_lace_issuer_evidence.py"
    ).read_text()
    assert "def publish_verified_ingress_admission(" in enforcer
    assert "issuer_verifier.verify(" in enforcer
    assert enforcer.index("issuer_verifier.verify(") < enforcer.index(
        "return cls.publish_atomic_run_admission("
    )
    assert "lease_is_current" in owner
    assert "AUTHORITY_FIELDS" in owner
    assert not (ROOT / "backend/src/oh_goon/services/enforcer_admission.py").exists()
