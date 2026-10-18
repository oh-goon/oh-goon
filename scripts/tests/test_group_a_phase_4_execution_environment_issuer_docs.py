"""M161 architecture regression: one owner and explicit non-live boundary."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ISSUER = (
    ROOT
    / "backend/src/oh_goon/native_issuer/enforcer_lace_execution_environment_issuer.py"
)


def test_m161_issuer_uses_runtime_owner_not_inline_backend_fallback() -> None:
    source = ISSUER.read_text()
    assert "configured_executor_pool(" in source
    assert "_read_original_admission(" in source
    assert "isolation_backend_evidence(" in source
    assert "resolve_environment(" in source
    assert "SIGNATURE_DOMAIN" in source
    assert "backend = 'bubblewrap'" not in source
