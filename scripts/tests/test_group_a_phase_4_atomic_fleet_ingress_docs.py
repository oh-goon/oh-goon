from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_atomic_fleet_prework_ingress_has_one_publication_owner():
    code = (ROOT / "backend/src/oh_goon/services/fleet_admission.py").read_text()
    assert "publish_atomic_fleet_prework_ingress" in code
