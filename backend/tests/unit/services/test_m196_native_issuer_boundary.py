"""M196: installed-product native issuer and repository-tooling legacy import have one owner."""
from __future__ import annotations

import importlib
from pathlib import Path
import subprocess

import pytest

from oh_goon.integrations.native_uv_lock import native_uv_locked_project_issue
from oh_goon.repository_tooling.dependency_locks import (
    native_uv_locked_project_issue as tooling_native_uv_locked_project_issue,
)
from oh_goon.repository_tooling.architecture.backend_boundary_contract import (
    check_backend_boundaries,
)

NATIVE_NAMES = (
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
)


@pytest.mark.parametrize("name", NATIVE_NAMES)
def test_native_issuer_has_exactly_one_installed_module_object(name: str) -> None:
    native = importlib.import_module(f"oh_goon.native_issuer.{name}")
    compatibility = importlib.import_module(f"oh_goon.repository_tooling.{name}")
    assert native is compatibility, "compatibility path cannot duplicate native issuer state"
    assert native.__name__ == f"oh_goon.native_issuer.{name}"
    assert "native_issuer" in Path(native.__file__).parts


def test_product_layer_does_not_import_repository_tooling() -> None:
    assert check_backend_boundaries() == []


def test_tooling_and_native_issuer_delegate_to_exact_same_uv_observer(tmp_path: Path) -> None:
    calls: list[tuple[object, object]] = []

    def capture(command, **options):
        calls.append((command, options))
        return subprocess.CompletedProcess(command, returncode=0, stdout="", stderr="")

    assert native_uv_locked_project_issue(tmp_path, runner=capture) is None
    assert tooling_native_uv_locked_project_issue(tmp_path, runner=capture) is None
    assert len(calls) == 2
    assert calls[0] == calls[1]
    command, options = calls[0]
    assert command == ("uv", "lock", "--project", str(tmp_path), "--check", "--offline")
    assert options["cwd"] == tmp_path
    assert options["check"] is False


def test_native_uv_fail_closed_before_work_on_missing_native_authority(tmp_path: Path) -> None:
    def unavailable(*_a, **_kw):
        raise FileNotFoundError("uv")

    assert "could not run" in native_uv_locked_project_issue(tmp_path, runner=unavailable)
    assert "could not run" in tooling_native_uv_locked_project_issue(tmp_path, runner=unavailable)
    assert "canonical" in native_uv_locked_project_issue(Path("relative"))
