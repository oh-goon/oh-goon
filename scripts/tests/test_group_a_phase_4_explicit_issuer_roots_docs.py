"""M154 docs/authority drift guard: key loading cannot become a hidden admission owner."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_keyring_has_no_signing_or_admission_bypass():
    source = (
        ROOT / "backend/src/oh_goon/runtime/enforcer_lace_issuer_keyring.py"
    ).read_text()
    assert "def load_explicit_issuer_verifier(" in source
    assert "TrustedIssuerVerifier(roots)" in source
    assert "os.O_NOFOLLOW" in source
    assert "dir_fd=directory_fd" in source
    assert "class FleetAdmissionPlanner" not in source
    assert "def issue(" not in source
    assert "publish_admission(" not in source


def test_protocol_is_verification_only_without_symmetric_issuer_fallback():
    source = (
        ROOT / "backend/src/oh_goon/core/enforcer_lace_issuer_evidence.py"
    ).read_text()
    assert 'ISSUER_PROTOCOL = "oh-goon/admission-issuer-evidence/v2"' in source
    assert 'SIGNATURE_DOMAIN = b"oh-goon/admission-issuer-ed25519/v2' in source
    assert "Ed25519PublicKey.from_public_bytes" in source
    assert "signature_hex" in source
    assert "admission-issuer-hmac" not in source
    assert "hmac.new(" not in source
