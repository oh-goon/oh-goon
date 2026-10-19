"""M162 ownership/qualification boundary protection."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ISSUER = (
    ROOT
    / "backend/src/oh_goon/native_issuer/enforcer_lace_backend_capability_issuer.py"
)


def test_issuer_delegates_to_existing_native_owners_and_has_no_runnable_effects() -> (
    None
):
    data = ISSUER.read_text()
    for fragment in (
        "environment_owner._observe(",
        "configured_executor_pool(",
        "isolation_backend_evidence(",
        "IsolationBackendCapabilities",
        "backend.capabilities",
        "SIGNATURE_DOMAIN",
        "_inventory_snapshot(",
    ):
        assert fragment in data
    assert "def execute(" not in data
    assert "def require_available(" not in data
