"""B17: visible namespace FD inventory is diagnostic, never physical absence proof."""
from pathlib import Path

import pytest

from oh_goon.repository_tooling import local_lace_physical_preflight as probe


def original(tmp_path: Path):
    proc = tmp_path / "proc"
    (proc / "sys/kernel/random").mkdir(parents=True)
    (proc / "sys/kernel/random/boot_id").write_text("boot17\n")
    root = tmp_path / "cgroup"
    root.mkdir()
    data = dict(run_id="run17", subject_identity="sha256:" + "b" * 64,
                cgroup_path=str(root), cgroup_device=root.stat().st_dev,
                cgroup_inode=root.stat().st_ino, tracked_pid=501,
                tracked_birth="boot17:88", mount_targets=["/resource"],
                network_namespace="net17", host_veth="veth17", nft_table="nft17")
    add_pid(proc, 501, 88)
    return probe.parse_scope(data), proc


def add_pid(proc: Path, pid: int, birth: int):
    directory = proc / str(pid)
    (directory / "ns").mkdir(parents=True)
    (directory / "fd").mkdir()
    (directory / "stat").write_text(f"{pid} (worker) " + " ".join(["S"] + ["0"] * 18 + [str(birth)]))
    (directory / "ns/mnt").symlink_to("mnt:[4026533001]")
    (directory / "ns/net").symlink_to("net:[4026533002]")


def test_foreign_fd_holder_detected_with_birth_and_fingerprint(tmp_path):
    scope, proc = original(tmp_path)
    add_pid(proc, 502, 99)
    (proc / "502/fd/4").symlink_to("mnt:[4026533001]")
    (proc / "502/fd/5").symlink_to("net:[4026533002]")
    result = probe._namespace_fd_holders(scope, proc)
    assert result["state"] == "PRESENT"
    assert "matching_namespace_fds=2" in result["detail"]
    assert "matching_namespace_processes=2" in result["detail"]
    assert "inventory_sha256=" in result["detail"]
    report = probe.observe(scope, proc_root=proc)
    assert report["schema"] == "oh-goon/b19/local-lace-physical-preflight/v6"
    assert report["observations"]["visible_namespace_fd_holders"] == result
    assert report["proof_obligations"]["FOREIGN_NAMESPACE_FD_HOLDERS"] == "UNVERIFIED"
    assert report["physical_absence_proven"] is False
    assert report["quiescence_proven"] is False
    assert report["teardown_proven"] is False
    assert report["effect_permission"] == "NONE" and report["gate_b"] == "BLOCKED"


def test_no_matching_fds_does_not_hide_original_namespace_process(tmp_path):
    scope, proc = original(tmp_path)
    add_pid(proc, 503, 101)
    (proc / "503/fd/6").symlink_to("socket:[444]")
    result = probe._namespace_fd_holders(scope, proc)
    assert result["state"] == "PRESENT"
    assert "matching_namespace_fds=0" in result["detail"]
    assert "matching_namespace_processes=2" in result["detail"]
    assert "hidden FD holders unverified" in result["detail"]


@pytest.mark.parametrize("case", ["missing-tracked", "boot-drift", "birth-drift", "bad-original-namespace", "fd-symlink", "malformed-fd", "missing-fd-target", "pid-stat-mismatch", "over-budget"])
def test_unverifiable_census_is_unknown(tmp_path, case):
    scope, proc = original(tmp_path)
    tracked = proc / "501"
    if case == "missing-tracked":
        (tracked / "stat").unlink()
    if case == "boot-drift":
        (proc / "sys/kernel/random/boot_id").write_text("wrong\n")
    if case == "birth-drift":
        (tracked / "stat").write_text("501 (reused) " + " ".join(["S"] + ["0"] * 18 + ["89"]))
    if case == "bad-original-namespace":
        (tracked / "ns/net").unlink()
        (tracked / "ns/net").symlink_to("net:[invalid]")
    if case == "fd-symlink":
        (tracked / "fd").rmdir()
        (tracked / "fd").symlink_to(tmp_path)
    if case == "malformed-fd":
        (tracked / "fd/9").symlink_to("net:[invalid]")
    if case == "missing-fd-target":
        (tracked / "fd/9").symlink_to("net:[4026533002]")
        (tracked / "fd/9").unlink()
        # A directory with a broken entry is enough to demonstrate incomplete inventory.
        (tracked / "fd/9").mkdir()
    if case == "pid-stat-mismatch":
        add_pid(proc, 502, 90)
        (proc / "502/stat").write_text("900 (wrong) " + " ".join(["S"] + ["0"] * 18 + ["90"]))
    if case == "over-budget":
        for n in range(513):
            add_pid(proc, 1000 + n, 90)
    assert probe._namespace_fd_holders(scope, proc)["state"] == "UNKNOWN"


def test_substituted_pid_directory_is_unknown(tmp_path):
    scope, proc = original(tmp_path)
    add_pid(proc, 502, 90)
    (proc / "502").rename(proc / "original502")
    (proc / "502").symlink_to(proc / "original502", target_is_directory=True)
    assert probe._namespace_fd_holders(scope, proc)["state"] == "UNKNOWN"


def test_double_scan_change_rejected(tmp_path, monkeypatch):
    scope, proc = original(tmp_path)
    original_fn = probe._namespace_fd_holders
    hits = 0
    def altered(*args):
        nonlocal hits
        hits += 1
        result = original_fn(*args)
        if hits == 2:
            return probe._component("PRESENT", "new owner acquired a namespace FD")
        return result
    monkeypatch.setattr(probe, "_namespace_fd_holders", altered)
    report = probe.observe(scope, proc_root=proc)
    assert report["observations"]["visible_namespace_fd_holders"]["state"] == "UNKNOWN"
    assert report["gate_b"] == "BLOCKED"


def test_no_journal_or_effect_authority_imported():
    source = Path(probe.__file__).read_text()
    assert "from oh_goon.runtime" not in source
    assert "from oh_goon.services" not in source
    assert "effect_permission\": \"NONE\"" in source


def test_b17_source_manifest_precedes_stress_witness():
    root = Path(__file__).resolve().parents[4]
    shell = (root / "scripts/qualify_b12_development_wsl.sh").read_text()
    assert "if [[ -f .checkpoint/B17_SOURCE.sha256 ]]; then embedded='.checkpoint/B17_SOURCE.sha256'; fi" in shell
    assert shell.index("B16_SOURCE.sha256") < shell.index("B17_SOURCE.sha256")
    make = (root / "Makefile").read_text()
    recipe = make.split("qualify-development-wsl:", 1)[1].split("\n\n", 1)[0]
    assert recipe.index("source-transport-clean") < recipe.index("qualify_b12_development_wsl.sh")
