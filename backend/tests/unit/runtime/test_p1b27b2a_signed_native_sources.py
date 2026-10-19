"""P1/B27B2A: pre-effect original signed native mount sources, not kernel facts."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.ports.original_mount_intent import (
    OriginalMountIntentDescriptor,
    OriginalNativeMount,
)
from oh_goon.integrations.fleet_plan_bubblewrap_isolation import (
    _require_original_signed_mount_identity,
)
from oh_goon.integrations.fleet_plan_local_lace_intent import (
    local_resource_identities,
    prepared_original_native_mounts,
)
from oh_goon.repository_tooling.architecture.repository_layout import ROOT
from oh_goon.repository_tooling.architecture.x0_single_execution_contract import (
    _check_p1b27b2a_signed_native_sources,
)
from tests.factories.x0_architecture import replace_python_syntax
from tests.unit.integrations.test_b12c_live_prework_resource_bridge import _request

PACKAGE = ROOT / "backend/src/oh_goon"


def test_static_native_mounts_signed_before_effects(tmp_path):
    request = _request(tmp_path)
    roster = prepared_original_native_mounts(request)
    assert len(roster) == 10
    assert (roster[0].source, roster[0].target, roster[0].mode) == (
        str(request.attempt.runtime_root.root),
        "/",
        "--ro-bind",
    )
    assert {mount.target for mount in roster} == {
        "/",
        "/etc/passwd",
        "/etc/group",
        "/etc/nsswitch.conf",
        "/etc/hosts",
        "/workspace",
        "/home/fleet",
        "/tmp",
        "/config",
        "/cache",
    }
    signed = local_resource_identities(request)["mounts"]
    _require_original_signed_mount_identity(request, signed)
    assert signed != OriginalMountIntentDescriptor((), ()).resource_identity


@pytest.mark.parametrize(
    "change",
    [
        "runtime-root",
        "etc-source",
        "home",
        "tmp",
        "cache",
        "config",
        "workspace",
        "readonly-source",
        "readonly-mode",
        "managed-cache",
    ],
)
def test_native_mount_source_or_mode_swap_fails_signed_consumption(tmp_path, change):
    request = _request(tmp_path)
    if change.startswith("readonly"):
        request.read_only_workspace = SimpleNamespace(
            snapshot_path=tmp_path / "snapshot", identity=SimpleNamespace(sha256="a" * 64)
        )
    signed = local_resource_identities(request)["mounts"]
    if change == "runtime-root":
        request.attempt.runtime_root.root = tmp_path / "other-root"
    elif change == "etc-source":
        request.attempt.paths.etc = tmp_path / "other-etc"
    elif change in ("home", "tmp", "cache", "config", "workspace"):
        setattr(request.attempt.paths, change, tmp_path / ("foreign-" + change))
    elif change == "readonly-source":
        request.read_only_workspace.snapshot_path = tmp_path / "other-snapshot"
    elif change == "readonly-mode":
        request.read_only_workspace = None
    else:
        request.attempt.sandbox_bindings = (
            SimpleNamespace(
                snapshot_path=tmp_path / "source",
                sandbox_path=Path("/cache"),
                identity=SimpleNamespace(sha256="b" * 64),
            ),
        )
    with pytest.raises(FleetPlanInvalid, match="original signed RESOURCE_INTENT"):
        _require_original_signed_mount_identity(request, signed)


@pytest.mark.parametrize(
    "bad",
    [
        ("/source", "/target", "--rw-bind"),
        ("/source/../other", "/target", "--bind"),
        ("relative", "/target", "--bind"),
        ("/source", "/target/..", "--bind"),
    ],
)
def test_noncanonical_native_mount_rejected(bad):
    with pytest.raises(ValueError, match="noncanonical"):
        OriginalNativeMount(*bad)


def test_conflicting_native_mount_targets_rejected():
    with pytest.raises(ValueError, match="ambiguous"):
        OriginalMountIntentDescriptor(
            (),
            (),
            native_mounts=(
                OriginalNativeMount("/one", "/home", "--bind"),
                OriginalNativeMount("/two", "/home", "--bind"),
            ),
        )


def test_existing_bwrap_argv_guard_consumes_single_original_roster(tmp_path, monkeypatch):
    from oh_goon.integrations.fleet_plan_bubblewrap_isolation import (
        _require_original_native_mount_argv,
    )
    from tests.unit.runtime.test_p1b27b1_native_mount_roster import prepared

    request, atomic, args = prepared(tmp_path, monkeypatch)
    _require_original_native_mount_argv(args, request, atomic, None)
    request.attempt.paths.home = tmp_path / "replacement"
    with pytest.raises(FleetPlanInvalid, match="native mount"):
        _require_original_native_mount_argv(args, request, atomic, None)


def test_canonical_architecture_guard():
    assert _check_p1b27b2a_signed_native_sources(PACKAGE) == []


@pytest.mark.parametrize(
    "change", ["drop-field", "drop-sign", "drop-projection", "drop-consumer", "drop-root"]
)
def test_x0_denies_signing_or_consumption_drift(tmp_path, change):
    paths = {
        "core/ports/original_mount_intent.py": PACKAGE / "core/ports/original_mount_intent.py",
        "integrations/fleet_plan_local_lace_intent.py": PACKAGE
        / "integrations/fleet_plan_local_lace_intent.py",
        "integrations/fleet_plan_bubblewrap_isolation.py": PACKAGE
        / "integrations/fleet_plan_bubblewrap_isolation.py",
    }
    for rel, source in paths.items():
        target = tmp_path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        code = source.read_text()
        if change == "drop-field" and rel.startswith("core/"):
            code = code.replace("    native_mounts: tuple[OriginalNativeMount, ...] = ()", "")
        if change == "drop-sign" and rel.startswith("core/"):
            code = replace_python_syntax(
                code, "members['native_mounts']", "members['native_unused']"
            )
        if change == "drop-projection" and rel.endswith("fleet_plan_local_lace_intent.py"):
            code = code.replace(
                "native_mounts=prepared_original_native_mounts(request),", "native_mounts=(),"
            )
        if change == "drop-consumer" and rel.endswith("fleet_plan_bubblewrap_isolation.py"):
            code = code.replace(
                "for mount in prepared_original_native_mounts(request)", "for mount in ()"
            )
        if change == "drop-root" and rel.endswith("fleet_plan_local_lace_intent.py"):
            code = code.replace("str(attempt.runtime_root.root)", "'/foreign-root'", 1)
        target.write_text(code)
    assert _check_p1b27b2a_signed_native_sources(tmp_path)


def test_standard_wsl_enrollment_and_exact_source_selector():
    qualifier = (ROOT / "scripts/qualify_b12_development_wsl.sh").read_text()
    needle = "if [[ -f .checkpoint/P1B27B2A_SOURCE.sha256 ]]; then embedded='.checkpoint/P1B27B2A_SOURCE.sha256'; fi"
    assert qualifier.count(needle) == 1
    next_needle = "if [[ -f .checkpoint/P1B27B2B1_SOURCE.sha256 ]]; then embedded='.checkpoint/P1B27B2B1_SOURCE.sha256'; fi"
    assert (
        qualifier.index(needle)
        < qualifier.index(next_needle)
        < qualifier.index('if [[ -f "$embedded" ]]')
    )
    assert "--require-module test_p1b27b2a_signed_native_sources" in qualifier
    assert (
        "observer_tests+=(tests/unit/runtime/test_p1b27b2a_signed_native_sources.py)" in qualifier
    )
