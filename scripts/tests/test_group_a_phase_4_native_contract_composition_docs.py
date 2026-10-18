"""M173: preserve the single-transaction and non-live limits in shipped docs."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_original_reservation_invokes_compiler_before_six_signatures():
    source = (ROOT / "backend/src/oh_goon/services/fleet_admission.py").read_text()
    segment = source.split("def operational_admission(")[1].split("def final_fence(")[0]
    assert segment.index("PreworkRunCapture.capture(") < segment.index(
        "compile_native_prework_run_contract("
    )
    assert segment.index("compile_native_prework_run_contract(") < segment.index(
        "issue_fixed_prework_native_evidence_with_signer_grant("
    )
    assert "compiled.identity != contract.identity" in segment
    assert "native compiled Run contract differs" in segment


def test_lifecycle_rejects_missing_policy_before_planning():
    source = (ROOT / "backend/src/oh_goon/services/fleet_run_lifecycle.py").read_text()
    assert "require_compiled_fleet_contract: bool = False" in source
    assert "compiled Fleet contract deployment policy not provisioned" in source
    assert "deployment_policy=(binding.deployment_policy" in source
