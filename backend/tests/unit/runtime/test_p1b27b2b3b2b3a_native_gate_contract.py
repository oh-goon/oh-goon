"""P1B27B2B3B2B3A anti-drift: original effectful test cannot fake success."""

from __future__ import annotations

from pathlib import Path

import pytest

from oh_goon.repository_tooling.architecture.repository_layout import ROOT
from tests.unit.integrations.test_b12c_live_prework_resource_bridge import _backend, _request


def test_opt_in_is_only_in_original_physical_ring_and_never_skip():
    shell = (ROOT / "scripts/qualify_b12_development_wsl.sh").read_text()
    assert "QUALIFY_B27_EFFECTFUL:-0" in shell
    assert "BLOCKED: effectful B27 witness requires real bwrap" in shell
    assert (
        shell.count("required_modules+=(--require-module test_p1b27b2b3b2b3a_effectful_mount)") == 1
    )
    assert shell.count("observer_tests+=(tests/native/test_p1b27b2b3b2b3a_effectful_mount.py)") == 1
    test = (ROOT / "backend/tests/native/test_p1b27b2b3b2b3a_effectful_mount.py").read_text()
    assert "pytest.fail(" in test and "pytest.skip(" not in test
    assert "OriginalBindSourceLease.pin_prepared(request)" in test
    assert "backend._command(" in test
    assert "_require_original_native_fd_handoff(" in test
    assert "run_command(*argv," in test
    assert "/proc/self/mountinfo" in test


def test_opt_in_does_not_mutate_production_execution():
    from oh_goon.integrations import fleet_plan_bubblewrap_isolation as owner

    module = Path(owner.__file__).read_text()
    assert "QUALIFY_B27_EFFECTFUL" not in module
    assert "tests/native" not in module
    assert module.index("proof: LocalLaceIntentProof | None = None") < module.index(
        "proof = request.local_resource_intent.reserve(request, self)"
    )


def test_optional_intent_is_none_before_physical_allocation(tmp_path, monkeypatch):
    backend, touched = _backend(monkeypatch)
    backend.require_local_resource_intent = False
    request = _request(tmp_path)
    with pytest.raises(RuntimeError, match="PHYSICAL_RESOURCE_CREATION_REACHED"):
        backend.execute(request, timeout=1, cancel_requested=None)
    touched.assert_called_once()


def test_strict_missing_intent_still_rejects_before_any_allocation(tmp_path, monkeypatch):
    from oh_goon.core.errors import FleetPlanInvalid

    backend, touched = _backend(monkeypatch)
    request = _request(tmp_path)
    with pytest.raises(FleetPlanInvalid, match="RESOURCE_INTENT missing"):
        backend.execute(request, timeout=1, cancel_requested=None)
    touched.assert_not_called()
