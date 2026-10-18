"""Docs/QA guard for native lock issuer scope and non-live admission boundary."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ISSUER = ROOT / "backend/src/oh_goon/native_issuer/enforcer_lace_native_lock_issuer.py"
NATIVE = ROOT / "backend/src/oh_goon/integrations/native_uv_lock.py"


def test_selected_authority_and_immutable_native_check_only() -> None:
    source = ISSUER.read_text()
    for phrase in (
        "_read_original_admission",
        "UvProjectRequirement",
        "native_uv_locked_project_issue",
        "pyproject.toml",
        "uv.lock",
        "SIGNATURE_DOMAIN",
        "changed during issuance",
    ):
        assert phrase in source
    native = NATIVE.read_text()
    assert '("uv", "lock", "--project", str(project), "--check", "--offline")' in native
    assert '"uv", "sync"' not in source


def test_strict_admission_canonical_replay_excludes_computed_capacity_fields() -> None:
    text = (
        ROOT / "backend/src/oh_goon/native_issuer/enforcer_lace_captured_run_issuer.py"
    ).read_text()
    assert 'event.model_dump(mode="python", exclude_computed_fields=True)' in text
    assert 'admitted.model_dump(mode="python", exclude_computed_fields=True)' in text
