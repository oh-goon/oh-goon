from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_captured_run_issuer_has_no_new_live_executor_authority() -> None:
    code = (
        ROOT / "backend/src/oh_goon/native_issuer/enforcer_lace_captured_run_issuer.py"
    ).read_text()
    assert "journal.read_view().run_events(run_id)" in code
    assert "issue_observed_run_repository_source_identity(" in code
    assert "REPOSITORY_CAPTURE_DOMAIN" in code
    assert "issue_observed_captured_run_repository_identity" in code
    assert "len(batch.events) != 1" in code
    assert "if later_revision != revision or after != admitted" in code
    assert "execute_direct" not in code and "subprocess" not in code
