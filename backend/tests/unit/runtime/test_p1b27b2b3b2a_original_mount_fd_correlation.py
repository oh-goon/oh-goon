"""B27B2B3B2A: original shared kernel reader reconciles native mount and FD facts.

These fixtures are NOT effectful Bubblewrap execution or independent Gate B proof.
"""

from __future__ import annotations

import shutil

import pytest

from oh_goon.integrations import linux_physical_observer as linux
from oh_goon.repository_tooling.architecture.repository_layout import ROOT
from oh_goon.repository_tooling.architecture.x0_single_execution_contract import (
    _check_p1b27b2b3b2a_mount_fd_correlation,
)
from tests.unit.runtime.test_p1b27b2b3b1_mount_fd_lifecycle import _fd, _fixture

PACKAGE = ROOT / "backend/src/oh_goon"


def _original_methods():
    methods = {name: getattr(linux, name) for name in linux.PROBES}
    for key in methods:
        if key not in ("_tracked_namespace_inventory", "_visible_mount_fd_holders"):
            methods[key] = lambda *_args: {"state": "UNKNOWN", "detail": "synthetic"}
    return methods


def test_real_original_probe_pair_correlates_same_mount_id_and_namespace(tmp_path):
    proc, scope = _fixture(tmp_path)
    _fd(proc)
    evidence = linux.observe_kernel(scope, proc_root=proc, diagnostic_probes=_original_methods())
    facts = evidence.to_mapping()
    assert facts["tracked_mount_namespace"]["state"] == "PRESENT"
    assert facts["visible_mount_fd_holders"]["state"] == "PRESENT"
    assert (
        facts["tracked_mount_namespace"]["detail"]
        .split("target_kernel_sha256=", 1)[1]
        .split(";", 1)[0]
        in facts["visible_mount_fd_holders"]["detail"]
    )


def test_no_visible_fd_is_not_qualified_absence(tmp_path):
    proc, scope = _fixture(tmp_path)
    facts = linux.observe_kernel(
        scope, proc_root=proc, diagnostic_probes=_original_methods()
    ).to_mapping()
    assert facts["tracked_mount_namespace"]["state"] == "PRESENT"
    assert facts["visible_mount_fd_holders"]["state"] == "NOT_OBSERVED"


@pytest.mark.parametrize(
    "attack", ["mount-id", "mount-root", "namespace", "missing-field", "foreign-state"]
)
def test_individually_stable_inconsistent_probes_become_unknown(tmp_path, attack):
    proc, scope = _fixture(tmp_path)
    _fd(proc)
    methods = _original_methods()
    original = methods["_visible_mount_fd_holders"]

    def mismatched(*args):
        result = original(*args)
        assert result["state"] == "PRESENT"
        detail = result["detail"]
        if attack == "mount-id":
            detail = detail.replace(detail.split("target_kernel_sha256=", 1)[1][:64], "f" * 64)
        elif attack == "mount-root":
            detail = detail.replace(detail.split("target_kernel_sha256=", 1)[1][:64], "0" * 64)
        elif attack == "namespace":
            detail = detail.replace(
                "original_namespace=mnt:[4026538123]", "original_namespace=mnt:[4026539000]"
            )
        elif attack == "missing-field":
            detail = detail.replace("target_kernel_sha256=", "dropped_kernel_sha256=")
        elif attack == "foreign-state":
            return {"state": "NOT_OBSERVED", "detail": "not a complete target roster"}
        return {"state": result["state"], "detail": detail}

    methods["_visible_mount_fd_holders"] = mismatched
    facts = linux.observe_kernel(scope, proc_root=proc, diagnostic_probes=methods).to_mapping()
    assert facts["tracked_mount_namespace"]["state"] == "UNKNOWN"
    assert facts["visible_mount_fd_holders"]["state"] == "UNKNOWN"
    assert not hasattr(
        linux.observe_kernel(scope, proc_root=proc, diagnostic_probes=methods), "teardown_proven"
    )


def test_unknown_tracked_roster_cannot_correlate_to_visible_fd(tmp_path):
    proc, scope = _fixture(tmp_path)
    _fd(proc)
    methods = _original_methods()
    original = methods["_tracked_namespace_inventory"]

    def unknown(*args):
        item = original(*args)
        item["tracked_mount_namespace"]["state"] = "UNKNOWN"
        return item

    methods["_tracked_namespace_inventory"] = unknown
    result = linux.observe_kernel(scope, proc_root=proc, diagnostic_probes=methods).to_mapping()
    assert result["tracked_mount_namespace"]["state"] == "UNKNOWN"
    # It is independently scoped holder visibility, NOT qualified correspondence.
    assert result["visible_mount_fd_holders"]["state"] == "PRESENT"


def test_partial_mount_target_roster_never_correlates_as_present(tmp_path):
    proc, scope = _fixture(tmp_path)
    _fd(proc)
    (proc / "812/mountinfo").write_text("43 2 8:1 / /workspace rw - ext4 /dev/sda1 rw\n")
    facts = linux.observe_kernel(
        scope, proc_root=proc, diagnostic_probes=_original_methods()
    ).to_mapping()
    assert facts["tracked_mount_namespace"]["state"] == "UNKNOWN"
    assert facts["visible_mount_fd_holders"]["state"] == "UNKNOWN"


def test_x0_correspondence_and_single_owner_guard():
    assert _check_p1b27b2b3b2a_mount_fd_correlation(PACKAGE) == []


@pytest.mark.parametrize(
    "attack", ["omit-call", "skip-identity", "omit-result", "accept-nondetection"]
)
def test_x0_fails_closed_on_original_correlation_bypass(tmp_path, attack):
    original = PACKAGE / "integrations/linux_physical_observer.py"
    destination = tmp_path / "integrations/linux_physical_observer.py"
    destination.parent.mkdir(parents=True)
    shutil.copy2(original, destination)
    source = destination.read_text()
    old, new = {
        "omit-call": (
            "combined = _correlate_original_mount_fd_snapshot(combined)",
            "combined = combined",
        ),
        "skip-identity": ("tracked_id != fd_id", "False"),
        "omit-result": ("value = combined[name]", "value = second[name]"),
        "accept-nondetection": ('tracked["state"] != "PRESENT"', "False"),
    }[attack]
    assert old in source
    destination.write_text(source.replace(old, new, 1))
    assert _check_p1b27b2b3b2a_mount_fd_correlation(tmp_path)
