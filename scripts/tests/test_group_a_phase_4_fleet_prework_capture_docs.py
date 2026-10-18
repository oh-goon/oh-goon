"""M166 structural documentation guard for an explicitly non-live pre-work step."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
JOURNAL = ROOT / "backend/src/oh_goon/runtime/journal.py"
FRONT = ROOT / "backend/src/oh_goon/services/fleet_admission.py"


def test_prework_capture_reuses_original_journal_and_fleet_owners():
    assert "prepare_run_admission_in" in JOURNAL.read_text()
    assert "prepare_fleet_prework_capture_in" in FRONT.read_text()
