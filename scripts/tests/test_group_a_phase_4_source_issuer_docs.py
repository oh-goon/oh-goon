"""M155 campaign map and separation of issuer/Enforcer signing authority."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_source_signer_is_outside_enforcer_and_not_runtime_wired():
    owner = (
        ROOT / "backend/src/oh_goon/repository_tooling/enforcer_lace_source_issuer.py"
    )
    text = owner.read_text()
    assert "canonical_source_identity(root)" in text
    assert "signing_key.sign(message)" in text
    assert "TrustedIssuerVerifier" not in text
    for rel in (
        "backend/src/oh_goon/core/enforcer_lace_issuer_evidence.py",
        "backend/src/oh_goon/services/fleet_admission.py",
        "backend/src/oh_goon/runtime/enforcer_lace_issuer_keyring.py",
    ):
        body = (ROOT / rel).read_text()
        assert "enforcer_lace_source_issuer" not in body
        assert "Ed25519PrivateKey" not in body
