"""B18 read-only foreign namespace mount inventory cannot become Gate B proof."""
from pathlib import Path

import pytest

from oh_goon.repository_tooling import local_lace_physical_preflight as probe


def _pid(proc: Path, pid: int, birth: int, namespace: str, targets: tuple[str, ...] = ("/other",)) -> None:
    node = proc / str(pid)
    (node / "ns").mkdir(parents=True)
    (node / "fd").mkdir()
    (node / "stat").write_text(f"{pid} (worker) " + " ".join(["S"] + ["0"] * 18 + [str(birth)]))
    (node / "ns/mnt").symlink_to(f"mnt:[{namespace}]")
    (node / "ns/net").symlink_to("net:[4026533002]")
    (node / "mountinfo").write_text("".join(
        f"{i} 1 0:5 / {t} rw - tmpfs tmpfs rw\n" for i, t in enumerate(targets, 40)
    ))


def _fixture(tmp_path: Path):
    proc = tmp_path / "proc"
    (proc / "sys/kernel/random").mkdir(parents=True)
    (proc / "sys/kernel/random/boot_id").write_text("boot18\n")
    cg = tmp_path / "cgroup"
    cg.mkdir()
    scope = probe.parse_scope({
        "run_id": "run18", "subject_identity": "sha256:" + "b" * 64,
        "cgroup_path": str(cg), "cgroup_device": cg.stat().st_dev,
        "cgroup_inode": cg.stat().st_ino, "tracked_pid": 618,
        "tracked_birth": "boot18:81", "mount_targets": ["/private/original"],
        "network_namespace": "net18", "host_veth": "veth18", "nft_table": "nft18",
    })
    _pid(proc, 618, 81, "4026533100")
    return scope, proc


def test_live_foreign_mount_is_present_with_deterministic_inventory(tmp_path):
    scope, proc = _fixture(tmp_path)
    _pid(proc, 619, 82, "4026533199", ("/private/original", "/other"))
    result = probe._visible_foreign_mount_targets(scope, proc)
    assert result["state"] == "PRESENT"
    assert "foreign_namespaces=1" in result["detail"]
    assert "matched_mount_targets=1" in result["detail"]
    assert result == probe._visible_foreign_mount_targets(scope, proc)
    report = probe.observe(scope, proc_root=proc)
    assert report["schema"] == "oh-goon/b19/local-lace-physical-preflight/v6"
    assert report["observations"]["visible_foreign_mount_targets"] == result
    assert report["proof_obligations"]["VISIBLE_FOREIGN_MOUNT_TARGETS"] == "UNVERIFIED"
    assert report["gate_b"] == "BLOCKED"
    assert report["effect_permission"] == "NONE"
    assert not report["physical_absence_proven"] and not report["quiescence_proven"] and not report["teardown_proven"]


def test_empty_visible_foreign_matches_cannot_establish_absence(tmp_path):
    scope, proc = _fixture(tmp_path)
    _pid(proc, 619, 82, "4026533199")
    assert probe._visible_foreign_mount_targets(scope, proc)["state"] == "NOT_OBSERVED"
    report = probe.observe(scope, proc_root=proc)
    assert "DETACHED_MOUNTS_UNOBSERVABLE" in report["unresolved"]
    assert report["gate_b"] == "BLOCKED" and report["physical_absence_proven"] is False


@pytest.mark.parametrize("mutation", [
    "original-lost", "original-reused", "boot-drift", "foreign-birth", "foreign-missing", "foreign-symlink",
    "bad-mountinfo", "duplicate-id", "invalid-escape", "missing-mountinfo", "inconsistent-shared-namespace",
    "unbounded-pids", "unbounded-mounts",
])
def test_incomplete_or_changed_foreign_observation_is_unknown(tmp_path, mutation):
    scope, proc = _fixture(tmp_path)
    _pid(proc, 619, 82, "4026533199")
    foreign = proc / "619"
    if mutation == "original-lost":
        (proc / "618/stat").unlink()
    elif mutation == "original-reused":
        (proc / "618/stat").write_text("618 (new) " + " ".join(["S"] + ["0"] * 18 + ["82"]))
    elif mutation == "boot-drift":
        (proc / "sys/kernel/random/boot_id").write_text("newboot")
    elif mutation == "foreign-birth":
        (foreign / "stat").write_text("900 (wrong) " + " ".join(["S"] + ["0"] * 18 + ["82"]))
    elif mutation == "foreign-missing":
        (foreign / "ns/mnt").unlink()
    elif mutation == "foreign-symlink":
        foreign.rename(proc / "renamed")
        foreign.symlink_to(proc / "renamed", target_is_directory=True)
    elif mutation == "bad-mountinfo":
        (foreign / "mountinfo").write_text("bad")
    elif mutation == "duplicate-id":
        (foreign / "mountinfo").write_text("40 1 0:5 / /one rw - tmpfs tmpfs rw\n40 1 0:5 / /two rw - tmpfs tmpfs rw\n")
    elif mutation == "invalid-escape":
        (foreign / "mountinfo").write_text("40 1 0:5 / /private\\invalid rw - tmpfs tmpfs rw\n")
    elif mutation == "missing-mountinfo":
        (foreign / "mountinfo").unlink()
    elif mutation == "inconsistent-shared-namespace":
        _pid(proc, 620, 83, "4026533199", ("/different",))
    elif mutation == "unbounded-pids":
        for n in range(513):
            _pid(proc, 1000 + n, 90, "4026533000")
    elif mutation == "unbounded-mounts":
        (foreign / "mountinfo").write_text("".join(f"{i} 1 0:5 / /other rw - tmpfs tmpfs rw\n" for i in range(9000)))
    assert probe._visible_foreign_mount_targets(scope, proc)["state"] == "UNKNOWN"


def test_double_scan_detects_new_foreign_mount(tmp_path, monkeypatch):
    scope, proc = _fixture(tmp_path)
    original = probe._visible_foreign_mount_targets
    counter = 0

    def raced(*args):
        nonlocal counter
        counter += 1
        if counter == 2:
            _pid(proc, 619, 82, "4026533199", ("/private/original",))
        return original(*args)

    monkeypatch.setattr(probe, "_visible_foreign_mount_targets", raced)
    report = probe.observe(scope, proc_root=proc)
    assert report["observations"]["visible_foreign_mount_targets"]["state"] == "UNKNOWN"
    assert report["gate_b"] == "BLOCKED"


def test_no_original_authority_or_effect_modules_imported():
    source = Path(probe.__file__).read_text()
    assert "from oh_goon.services" not in source
    assert "from oh_goon.runtime" not in source
    assert '"effect_permission": "NONE"' in source


def test_embedded_b18_manifest_selection_keeps_cleanup_first():
    root = Path(__file__).resolve().parents[4]
    script = (root / "scripts/qualify_b12_development_wsl.sh").read_text()
    assert "if [[ -f .checkpoint/B18_SOURCE.sha256 ]]; then embedded='.checkpoint/B18_SOURCE.sha256'; fi" in script
    assert script.index("B17_SOURCE.sha256") < script.index("B18_SOURCE.sha256")
    make = (root / "Makefile").read_text()
    recipe = make.split("qualify-development-wsl:", 1)[1].split("\n\n", 1)[0]
    assert recipe.index("source-transport-clean") < recipe.index("qualify_b12_development_wsl.sh")
