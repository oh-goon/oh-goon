"""M170 documentation and original-owner guardrails."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_original_control_plane_and_lifecycle_use_single_authentication_gate() -> None:
    plane = (
        ROOT / "backend/src/oh_goon/services/fleet_run_control_plane.py"
    ).read_text()
    lifecycle = (
        ROOT / "backend/src/oh_goon/services/fleet_run_lifecycle.py"
    ).read_text()
    composition = (ROOT / "backend/src/oh_goon/_application_services.py").read_text()
    assert "self._lifecycle.require_fleet_prework_available(" in plane
    assert "FleetAdmissionPlanner.reserve_atomic_fleet_prework_ingress(" in lifecycle
    assert (
        "require_authenticated_fleet=foundation.require_authenticated_fleet"
        in composition
    )
    assert "fleet_prework=foundation.fleet_prework" in composition
