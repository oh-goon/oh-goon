"""M174: policy-only contract construction must never imply live activation."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_compiler_precedes_existing_signed_publication_in_original_reservation():
    source = (ROOT / "backend/src/oh_goon/services/fleet_admission.py").read_text()
    segment = source.split("def reserve_atomic_fleet_policy_ingress(")[1].split(
        "def reserve_atomic_fleet_prework_ingress("
    )[0]
    assert (
        segment.index("PreworkRunCapture.capture(")
        < segment.index("compile_native_prework_run_contract(")
        < segment.index("publish_atomic_fleet_prework_ingress(")
    )
    assert "execution.reserve_fleet_with(" in segment
    lifecycle = (
        ROOT / "backend/src/oh_goon/services/fleet_run_lifecycle.py"
    ).read_text()
    assert "policy-only Fleet enrollment requires native compiler" in lifecycle
    assert "binding.require_matching(" in lifecycle


def test_policy_only_enrollment_has_no_derived_contract_input():
    source = (
        ROOT / "backend/src/oh_goon/services/fleet_policy_enrollment.py"
    ).read_text()
    assert "contract: AdmittedRunContract" not in source
    assert "MappingProxyType" in source
    assert "deployment_policy: DeploymentRunContractPolicy" in source
