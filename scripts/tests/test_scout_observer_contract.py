from pathlib import Path

from oh_goon.repository_tooling.architecture.scout_observer_contract import (
    check_scout_observer_contract,
)
from oh_goon.repository_tooling.architecture.repository_layout import (
    BACKEND_PACKAGE_ROOT,
)


def test_scout_observer_contract_is_clean() -> None:
    assert check_scout_observer_contract() == []


def test_scout_store_schema_is_metadata_and_resource_only() -> None:
    text = (
        (BACKEND_PACKAGE_ROOT / "scout" / "store.py")
        .read_text(encoding="utf-8")
        .lower()
    )
    for forbidden in (
        "stdout",
        "stderr",
        "command_output",
        "source_code",
        "environment_value",
        "gitlab",
    ):
        assert forbidden not in text


def test_scout_has_no_gitlab_dependency() -> None:
    roots = (
        *sorted((BACKEND_PACKAGE_ROOT / "scout").glob("*.py")),
        BACKEND_PACKAGE_ROOT / "api" / "routes" / "scout.py",
    )
    assert roots
    assert all(
        "gitlab" not in path.read_text(encoding="utf-8").lower() for path in roots
    )


def test_scout_sidecar_enforces_private_read_and_recovery_pragmas() -> None:
    text = (BACKEND_PACKAGE_ROOT / "scout" / "database.py").read_text(encoding="utf-8")
    for required in (
        "PRAGMA query_only = ON",
        "PRAGMA trusted_schema = OFF",
        "PRAGMA secure_delete = ON",
        "_reject_unsafe_existing_file",
        "recover_if_corrupt",
        "truncate_wal",
    ):
        assert required in text


def test_scout_runtime_network_certification_is_wired() -> None:
    root = Path(__file__).resolve().parents[2]
    makefile = (root / "Makefile").read_text(encoding="utf-8")
    tooling = root / "backend/src/oh_goon/repository_tooling"
    probe = (tooling / "scout_http_load_probe.py").read_text(encoding="utf-8")
    command = (tooling / "scout_network_certification.py").read_text(encoding="utf-8")
    assert "scout-network-certification:" in makefile
    assert "enforce_loopback_only=True" in command
    assert 'argv.append("oh-goon-guarded-uvicorn")' in probe
