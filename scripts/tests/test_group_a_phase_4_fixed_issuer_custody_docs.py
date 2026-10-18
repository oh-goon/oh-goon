"""M164 contract/docs/QA guardrails."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SUITE = ROOT / "backend/src/oh_goon/native_issuer/enforcer_lace_native_issuer_suite.py"
CUSTODY = ROOT / "backend/src/oh_goon/native_issuer/enforcer_lace_signer_custody.py"


def test_signing_dispatch_is_fixed_to_six_native_owners_not_an_api() -> None:
    text = SUITE.read_text()
    for fn in (
        "issue_observed_captured_run_repository_identity",
        "issue_observed_captured_run_source",
        "issue_observed_captured_run_lock_identity",
        "issue_observed_captured_run_native_tool_identity",
        "issue_observed_captured_run_execution_environment_identity",
        "issue_observed_captured_run_backend_capability_manifest_identity",
    ):
        assert fn in text
    assert "deployment_signer_policy_snapshot" in text
    assert "_read_original_admission" in text
    assert "native_issuer:" not in text
    assert "def execute(" not in text
    assert "_policy(root_fd, trusted_public_roots)" in CUSTODY.read_text()
