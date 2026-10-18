"""M150 architecture/documentation guard against claiming pre-live proof as Gate A."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_atomic_publication_has_one_owner_and_no_new_live_wiring():
    planner = (ROOT / "backend/src/oh_goon/services/fleet_admission.py").read_text()
    journal = (ROOT / "backend/src/oh_goon/runtime/enforcer_lace_journal.py").read_text()
    lifecycle = (ROOT / "backend/src/oh_goon/services/fleet_run_lifecycle.py").read_text()
    assert "def publish_atomic_run_admission(" in planner
    assert "cls.require_exact_contract_environment(" in planner
    assert "journal.publish_admission_with_operation(" in planner
    assert "def publish_admission_with_operation(" in journal
    assert "self._transaction(commit)" in journal
    assert "publish_atomic_run_admission(" not in lifecycle  # not live before trusted issuers
