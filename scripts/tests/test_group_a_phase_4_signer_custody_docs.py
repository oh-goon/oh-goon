"""M163 docs and separation-of-authority QA."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SIGNER = ROOT / "backend/src/oh_goon/native_issuer/enforcer_lace_signer_custody.py"
ENFORCER = ROOT / "backend/src/oh_goon/core/enforcer_lace_issuer_evidence.py"


def test_key_custody_has_no_enforcer_private_key_or_run_effect_path() -> None:
    data = SIGNER.read_text()
    assert "def execute(" not in data and "RUN_ADMITTED" not in data
    assert "private_key" not in ENFORCER.read_text()
    assert "O_NOFOLLOW" in data and "_policy(root_fd" in data
    assert "TrustedIssuerVerifier" in ENFORCER.read_text()
