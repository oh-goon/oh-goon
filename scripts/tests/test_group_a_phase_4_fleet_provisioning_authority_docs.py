"""M171: retain deployment provenance and fail-before-planning boundaries."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_one_original_lifecycle_and_deployment_owner():
    service = (ROOT / "backend/src/oh_goon/services/fleet_run_lifecycle.py").read_text()
    plane = (
        ROOT / "backend/src/oh_goon/services/fleet_run_control_plane.py"
    ).read_text()
    foundation = (ROOT / "backend/src/oh_goon/_application_foundation.py").read_text()
    wiring = (ROOT / "backend/src/oh_goon/_application_services.py").read_text()
    assert "self._lifecycle.require_fleet_prework_available(" in plane
    assert "FleetAdmissionPlanner.reserve_atomic_fleet_prework_ingress(" in service
    assert "fleet_provisioner: FleetPreworkProvisioner | None = None" in foundation
    assert "fleet_provisioner=foundation.fleet_provisioner" in wiring
    assert "Fleet Enforcer must have one provisioning authority" in service
