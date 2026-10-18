"""M152 documentation / fail-closed transaction ownership guard."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_only_enforcer_introduces_the_commit_fence() -> None:
    planner = (ROOT / "backend/src/oh_goon/services/fleet_admission.py").read_text()
    assert "commit_lease_is_current" in planner
    assert "before_commit=fence_before_commit" in planner
    assert "control lease stale at admission commit" in planner
    assert "def publish_verified_ingress_admission(" in planner
    assert "return cls.publish_atomic_run_admission(" in planner
