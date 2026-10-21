"""CO-R3R: original shared observer rejects mid-census escaped tasks.

No mock or local read-only observation may authorize physical cleanup.
"""

from __future__ import annotations

from oh_goon.core.ports.physical_observation import PhysicalObservationScope

from pathlib import Path

import pytest

from oh_goon.integrations import linux_physical_observer as linux
from oh_goon.repository_tooling import local_lace_physical_preflight as probe
from oh_goon.repository_tooling.local_lace_physical_preflight import parse_scope


def _pid(proc: Path, pid: int, start: int, ns: str, mount: str = "/unrelated") -> None:
    directory = proc / str(pid)
    (directory / "ns").mkdir(parents=True)
    (directory / "stat").write_text(
        f"{pid} (worker) " + " ".join(["S"] + ["0"] * 18 + [str(start)])
    )
    (directory / "ns/mnt").symlink_to(f"mnt:[{ns}]")
    (directory / "mountinfo").write_text(f"42 1 0:5 / {mount} rw - tmpfs tmpfs rw\n")


def _scope(
    tmp_path: Path, *, foreign: bool = False
) -> tuple[PhysicalObservationScope, Path]:
    proc = tmp_path / "proc"
    (proc / "sys/kernel/random").mkdir(parents=True)
    (proc / "sys/kernel/random/boot_id").write_text("boot-r3r\n")
    cg = tmp_path / "group"
    cg.mkdir()
    _pid(proc, 710, 87, "4026531100")
    if foreign:
        _pid(proc, 711, 88, "4026531199")
    return parse_scope(
        {
            "run_id": "original-r3r",
            "subject_identity": "sha256:" + "e" * 64,
            "cgroup_path": str(cg),
            "cgroup_device": cg.stat().st_dev,
            "cgroup_inode": cg.stat().st_ino,
            "tracked_pid": 710,
            "tracked_birth": "boot-r3r:87",
            "mount_targets": ["/original"],
            "network_namespace": "net",
            "host_veth": "veth",
            "nft_table": "table",
        }
    ), proc


@pytest.mark.parametrize("change", ["appears", "disappears"])
def test_foreign_pid_roster_drift_is_unknown_at_original_kernel_observer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    scope, proc = _scope(tmp_path, foreign=change == "disappears")
    read = linux._read_regular
    observations = 0

    def raced(path: Path, limit: int) -> str:
        nonlocal observations
        result = read(path, limit)
        # The original tracked stat is first read before the scan and again
        # immediately before reporting.  Simulate a task arriving/leaving in
        # the destructive uncertainty cut, after the first PID census.
        if path == proc / "710/stat":
            observations += 1
            if observations == 2:
                if change == "appears":
                    _pid(proc, 711, 88, "4026531199", "/original")
                else:
                    child = proc / "711"
                    (child / "stat").unlink()
                    (child / "mountinfo").unlink()
                    (child / "ns/mnt").unlink()
                    (child / "ns").rmdir()
                    child.rmdir()
        return result

    monkeypatch.setattr(linux, "_read_regular", raced)
    fact = linux._visible_foreign_mount_targets(scope, proc)
    assert observations >= 2
    assert fact["state"] == "UNKNOWN"
    assert "incomplete" in fact["detail"]


def test_stable_foreign_mount_is_positive_only_and_no_matches_are_not_absence(
    tmp_path: Path,
) -> None:
    scope, proc = _scope(tmp_path, foreign=True)
    assert linux._visible_foreign_mount_targets(scope, proc)["state"] == "NOT_OBSERVED"
    (proc / "711/mountinfo").write_text("42 1 0:5 / /original rw - tmpfs tmpfs rw\n")
    assert linux._visible_foreign_mount_targets(scope, proc)["state"] == "PRESENT"
    result = probe.observe(scope, proc_root=proc)
    assert result["gate_b"] == "BLOCKED"
    assert result["effect_permission"] == "NONE"
    assert not result["physical_absence_proven"] and not result["teardown_proven"]
