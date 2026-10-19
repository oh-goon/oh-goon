"""Prevent documented backend-selection authority from drifting from the v8 parser."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_v8_parser_is_exact_single_owner_and_certification_uses_it() -> None:
    source = (
        ROOT / "backend/src/oh_goon/runtime/_executor_configuration.py"
    ).read_text(encoding="utf-8")
    certificate = (
        ROOT / "backend/src/oh_goon/services/certification_bootstrap.py"
    ).read_text(encoding="utf-8")
    assert "EXECUTOR_CONFIG_VERSION = 8" in source
    assert 'kind = configured_environment_backend(raw.get("execution"))' in source
    assert '"kind",' not in source
    assert 'execution = { environment = { backend = "bubblewrap" } }' in certificate
