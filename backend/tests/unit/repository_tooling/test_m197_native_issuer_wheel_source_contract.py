"""M197: package discovery cannot drop M196's promoted native issuer owners.

This is a source metadata check, NOT an installed-wheel attestation. A native
Hatch wheel build, independent installation and import remain release gates.
"""

from __future__ import annotations

import tomllib
from pathlib import Path


def test_hatch_wheel_discovers_all_promoted_installed_native_issuer_owners() -> None:
    root = Path(__file__).resolve().parents[4]
    backend = root / "backend"
    metadata = tomllib.loads((backend / "pyproject.toml").read_text(encoding="utf-8"))
    wheel = metadata["tool"]["hatch"]["build"]["targets"]["wheel"]
    assert wheel["packages"] == ["src/oh_goon"]

    owner = backend / "src/oh_goon/native_issuer"
    assert (owner / "__init__.py").is_file()
    expected = {
        "enforcer_lace_backend_capability_issuer",
        "enforcer_lace_captured_run_issuer",
        "enforcer_lace_execution_environment_issuer",
        "enforcer_lace_native_issuer_suite",
        "enforcer_lace_native_lock_issuer",
        "enforcer_lace_native_tool_issuer",
        "enforcer_lace_prework_capture",
        "enforcer_lace_repository_snapshot_issuer",
        "enforcer_lace_run_source_issuer",
        "enforcer_lace_signer_custody",
    }
    for module in sorted(expected):
        assert (owner / f"{module}.py").is_file(), module
        assert (backend / f"src/oh_goon/repository_tooling/{module}.py").is_file()
    assert (backend / "src/oh_goon/integrations/native_uv_lock.py").is_file()
