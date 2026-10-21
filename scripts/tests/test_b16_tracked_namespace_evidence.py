"""B16: pinned-process namespace inventory is diagnostic, never physical proof."""

from __future__ import annotations

from oh_goon.core.ports.physical_observation import PhysicalObservationScope

from pathlib import Path

import pytest

from oh_goon.repository_tooling import local_lace_physical_preflight as probe


def fixture(
    tmp_path: Path, *, network: bool = True
) -> tuple[PhysicalObservationScope, Path, Path]:
    root = tmp_path / "cgroup"
    root.mkdir()
    (root / "cgroup.events").write_text("populated 0\n")
    (root / "cgroup.procs").write_text("")
    raw = {
        "run_id": "original-run",
        "subject_identity": "sha256:" + "b" * 64,
        "cgroup_path": str(root),
        "cgroup_device": root.stat().st_dev,
        "cgroup_inode": root.stat().st_ino,
        "tracked_pid": 817,
        "tracked_birth": "boot-test:909",
        "mount_targets": ["/private/original mount"],
        "network_namespace": "original-net" if network else None,
        "host_veth": "original-veth" if network else None,
        "nft_table": "original-nft" if network else None,
    }
    proc = tmp_path / "proc"
    (proc / "817/ns").mkdir(parents=True)
    (proc / "sys/kernel/random").mkdir(parents=True)
    (proc / "sys/kernel/random/boot_id").write_text("boot-test\n")
    (proc / "817/stat").write_text(
        "817 (original worker) " + " ".join(["S"] + ["0"] * 18 + ["909"])
    )
    (proc / "817/ns/mnt").symlink_to("mnt:[4026533001]")
    (proc / "817/ns/net").symlink_to("net:[4026533002]")
    mountinfo = proc / "817/mountinfo"
    mountinfo.write_text(
        "42 22 0:42 / /private/original\\040mount rw - tmpfs tmpfs rw\n"
    )
    return probe.parse_scope(raw), proc, mountinfo


def test_tracked_mount_and_network_handles_are_observed_but_not_proof(
    tmp_path: Path,
) -> None:
    scope, proc, _ = fixture(tmp_path)
    records = getattr(probe, "_tracked_namespace_inventory")(scope, proc)
    assert records["tracked_mount_namespace"]["state"] == "PRESENT"
    assert "target_mount_ids=[42]" in records["tracked_mount_namespace"]["detail"]
    assert "inventory_sha256=" in records["tracked_mount_namespace"]["detail"]
    assert records["tracked_network_namespace"]["state"] == "PRESENT"
    report = probe.observe(scope, proc_root=proc)
    assert report["schema"] == "oh-goon/b19/local-lace-physical-preflight/v6"
    assert (
        report["observations"]["tracked_mount_namespace"]
        == records["tracked_mount_namespace"]
    )
    assert report["gate_b"] == "BLOCKED"
    assert report["effect_permission"] == "NONE"
    assert (
        report["teardown_proven"] is False
        and report["physical_absence_proven"] is False
    )
    assert set(report["proof_obligations"].values()) == {"UNVERIFIED"}
    assert "TRACKED_AND_FOREIGN_NAMESPACE_COVERAGE" in report["proof_obligations"]


def test_no_target_mount_in_tracked_namespace_is_not_absence(tmp_path: Path) -> None:
    scope, proc, mountinfo = fixture(tmp_path)
    mountinfo.write_text("22 2 0:42 / /other rw - tmpfs tmpfs rw\n")
    report = getattr(probe, "_tracked_namespace_inventory")(scope, proc)
    assert report["tracked_mount_namespace"]["state"] == "NOT_OBSERVED"
    assert (
        "foreign/detached mounts unverified"
        in report["tracked_mount_namespace"]["detail"]
    )


def test_tracked_process_disappears_without_proving_handle_absence(
    tmp_path: Path,
) -> None:
    scope, proc, _ = fixture(tmp_path)
    (proc / "817/mountinfo").unlink()
    (proc / "817/stat").unlink()
    (proc / "817/ns/mnt").unlink()
    (proc / "817/ns/net").unlink()
    (proc / "817/ns").rmdir()
    (proc / "817").rmdir()
    records = getattr(probe, "_tracked_namespace_inventory")(scope, proc)
    assert {v["state"] for v in records.values()} == {"NOT_OBSERVED"}


@pytest.mark.parametrize(
    "mutation",
    [
        "reused-pid",
        "changed-boot",
        "missing-mount",
        "bad-handle",
        "bad-net-handle",
        "bad-mountinfo",
        "duplicate-mount-id",
        "invalid-escape",
        "empty-mountinfo",
        "wrong-stat-pid",
        "symlink-pid-dir",
    ],
)
def test_incomplete_or_substituted_proc_information_is_unknown(
    tmp_path: Path, mutation: str
) -> None:
    scope, proc, mountinfo = fixture(tmp_path)
    if mutation == "reused-pid":
        (proc / "817/stat").write_text(
            "817 (reused) " + " ".join(["S"] + ["0"] * 18 + ["910"])
        )
    elif mutation == "changed-boot":
        (proc / "sys/kernel/random/boot_id").write_text("another-boot\n")
    elif mutation == "missing-mount":
        mountinfo.unlink()
    elif mutation == "bad-handle":
        (proc / "817/ns/mnt").unlink()
        (proc / "817/ns/mnt").symlink_to("not-a-mount-namespace")
    elif mutation == "bad-net-handle":
        (proc / "817/ns/net").unlink()
        (proc / "817/ns/net").symlink_to("net:[wrong]")
    elif mutation == "bad-mountinfo":
        mountinfo.write_text("not mountinfo\n")
    elif mutation == "duplicate-mount-id":
        mountinfo.write_text(
            "42 2 0:42 / /one rw - tmpfs tmpfs rw\n42 2 0:42 / /two rw - tmpfs tmpfs rw\n"
        )
    elif mutation == "invalid-escape":
        mountinfo.write_text(
            "42 2 0:42 / /private/original\\invalid rw - tmpfs tmpfs rw\n"
        )
    elif mutation == "empty-mountinfo":
        mountinfo.write_text("")
    elif mutation == "wrong-stat-pid":
        (proc / "817/stat").write_text(
            "900 (wrong) " + " ".join(["S"] + ["0"] * 18 + ["909"])
        )
    elif mutation == "symlink-pid-dir":
        (proc / "817").rename(proc / "actual")
        (proc / "817").symlink_to(proc / "actual", target_is_directory=True)
    assert {
        r["state"]
        for r in getattr(probe, "_tracked_namespace_inventory")(scope, proc).values()
    } == {"UNKNOWN"}


def test_no_network_lease_cannot_bind_process_namespace_to_run(tmp_path: Path) -> None:
    scope, proc, _ = fixture(tmp_path, network=False)
    result = getattr(probe, "_tracked_namespace_inventory")(scope, proc)
    assert result["tracked_mount_namespace"]["state"] == "PRESENT"
    assert result["tracked_network_namespace"]["state"] == "UNKNOWN"


def test_double_scan_namespace_identity_change_is_unknown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scope, proc, _ = fixture(tmp_path)
    real = getattr(probe, "_tracked_namespace_inventory")
    count = 0

    def raced(
        scope: PhysicalObservationScope, proc_root: Path
    ) -> dict[str, dict[str, str]]:
        nonlocal count
        count += 1
        result: dict[str, dict[str, str]] = real(scope, proc_root)
        if count == 2:
            result["tracked_mount_namespace"] = getattr(probe, "_component")(
                "PRESENT", "replaced mount namespace"
            )
        return result

    monkeypatch.setattr(probe, "_tracked_namespace_inventory", raced)
    report = probe.observe(scope, proc_root=proc)
    assert report["observations"]["tracked_mount_namespace"]["state"] == "UNKNOWN"
    assert report["gate_b"] == "BLOCKED"


def test_manifest_selection_and_transport_cleanup_preserved() -> None:
    root = Path(__file__).resolve().parents[2]
    script = (root / "scripts/qualify_b12_development_wsl.sh").read_text()
    assert (
        "if [[ -f .checkpoint/B16_SOURCE.sha256 ]]; then embedded='.checkpoint/B16_SOURCE.sha256'; fi"
        in script
    )
    assert script.index("B15_SOURCE.sha256") < script.index("B16_SOURCE.sha256")
    make = (root / "Makefile").read_text()
    qualification = make.split("qualify-development-wsl:", 1)[1].split("\n\n", 1)[0]
    assert qualification.index("source-transport-clean") < qualification.index(
        "qualify_b12_development_wsl.sh"
    )


def test_never_imports_original_authority_or_writes_journal() -> None:
    text = Path(probe.__file__).read_text()
    assert "from oh_goon.services" not in text
    assert "from oh_goon.runtime" not in text
    assert 'QUIESCENCE_PROVEN"' not in text
    assert 'TEARDOWN_PROVEN"' not in text
