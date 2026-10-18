"""B19: visible mount FD evidence never elevates to physical teardown proof."""
from pathlib import Path

import pytest

from oh_goon.repository_tooling import local_lace_physical_preflight as probe


def _pid(proc: Path, pid: int, birth: int, ns: str = "4026533001", target: str = "/mnt/other"):
    root = proc / str(pid)
    (root / "ns").mkdir(parents=True)
    (root / "fd").mkdir()
    (root / "fdinfo").mkdir()
    (root / "stat").write_text(f"{pid} (test) " + " ".join(["S"] + ["0"] * 18 + [str(birth)]))
    (root / "ns/mnt").symlink_to(f"mnt:[{ns}]")
    (root / "ns/net").symlink_to("net:[4026533002]")
    (root / "mountinfo").write_text(f"42 1 0:5 / {target} rw - tmpfs tmpfs rw\n")
    return root


def fixture(tmp_path):
    proc = tmp_path / "proc"
    (proc / "sys/kernel/random").mkdir(parents=True)
    (proc / "sys/kernel/random/boot_id").write_text("boot19\n")
    cg = tmp_path / "cgroup"
    cg.mkdir()
    scope = probe.parse_scope({
        "run_id": "run19", "subject_identity": "sha256:" + "d" * 64,
        "cgroup_path": str(cg), "cgroup_device": cg.stat().st_dev,
        "cgroup_inode": cg.stat().st_ino, "tracked_pid": 619,
        "tracked_birth": "boot19:81", "mount_targets": ["/mnt/original"],
        "network_namespace": "net19", "host_veth": "veth19", "nft_table": "nft19",
    })
    _pid(proc, 619, 81, target="/mnt/original")
    return scope, proc


def _fd(root: Path, num: int, mount_id: str):
    (root / "fd" / str(num)).symlink_to("socket:[123]")
    (root / "fdinfo" / str(num)).write_text(f"pos:\t0\nflags:\t0100002\nmnt_id:\t{mount_id}\n")


def test_original_namespace_visible_fd_holder_is_present(tmp_path):
    scope, proc = fixture(tmp_path)
    foreign = _pid(proc, 620, 82)
    _fd(foreign, 3, "42")
    result = probe._visible_mount_fd_holders(scope, proc)
    assert result["state"] == "PRESENT"
    assert "matching_mount_fds=1" in result["detail"]
    assert result == probe._visible_mount_fd_holders(scope, proc)
    report = probe.observe(scope, proc_root=proc)
    assert report["schema"] == "oh-goon/b19/local-lace-physical-preflight/v6"
    assert report["observations"]["visible_mount_fd_holders"] == result
    assert report["proof_obligations"]["VISIBLE_MOUNT_FD_HOLDERS"] == "UNVERIFIED"
    assert "OPEN_MOUNT_FD_COVERAGE_UNPROVEN" in report["unresolved"]
    assert report["gate_b"] == "BLOCKED" and report["effect_permission"] == "NONE"
    assert report["quiescence_proven"] is False and report["teardown_proven"] is False


def test_fd_in_different_namespace_cannot_match_identical_mount_id(tmp_path):
    scope, proc = fixture(tmp_path)
    foreign = _pid(proc, 620, 82, ns="4026534999")
    _fd(foreign, 3, "42")
    assert probe._visible_mount_fd_holders(scope, proc)["state"] == "NOT_OBSERVED"
    assert probe.observe(scope, proc_root=proc)["physical_absence_proven"] is False


def test_unrelated_mount_fd_is_local_negative_only(tmp_path):
    scope, proc = fixture(tmp_path)
    _fd(proc / "619", 4, "99")
    result = probe._visible_mount_fd_holders(scope, proc)
    assert result["state"] == "NOT_OBSERVED"
    assert "matching_mount_fds=0" in result["detail"]


@pytest.mark.parametrize("mutation", [
    "original-gone", "original-reused", "boot-drift", "no-target-id",
    "bad-original-mountinfo", "duplicate-mount", "foreign-reused", "fdinfo-missing",
    "fdinfo-bad", "fdinfo-duplicate", "fdinfo-symlink", "fd-root-symlink",
    "foreign-pid-symlink", "over-pid-budget", "over-fd-budget",
])
def test_unverifiable_mount_fd_census_is_unknown(tmp_path, mutation):
    scope, proc = fixture(tmp_path)
    other = _pid(proc, 620, 82)
    _fd(other, 3, "42")
    if mutation == "original-gone":
        (proc / "619/stat").unlink()
    elif mutation == "original-reused":
        _pid_stat = proc / "619/stat"
        _pid_stat.write_text(_pid_stat.read_text().replace("81", "82"))
    elif mutation == "boot-drift":
        (proc / "sys/kernel/random/boot_id").write_text("boot20\n")
    elif mutation == "no-target-id":
        (proc / "619/mountinfo").write_text("42 1 0:5 / /elsewhere rw - tmpfs tmpfs rw\n")
    elif mutation == "bad-original-mountinfo":
        (proc / "619/mountinfo").write_text("broken")
    elif mutation == "duplicate-mount":
        with (proc / "619/mountinfo").open("a") as f:
            f.write("42 1 0:5 / /elsewhere rw - tmpfs tmpfs rw\n")
    elif mutation == "foreign-reused":
        (other / "stat").write_text("900 (wrong) " + " ".join(["S"] + ["0"] * 18 + ["82"]))
    elif mutation == "fdinfo-missing":
        (other / "fdinfo/3").unlink()
    elif mutation == "fdinfo-bad":
        (other / "fdinfo/3").write_text("mnt_id:\tbad\n")
    elif mutation == "fdinfo-duplicate":
        (other / "fdinfo/3").write_text("mnt_id:\t42\nmnt_id:\t42\n")
    elif mutation == "fdinfo-symlink":
        (other / "fdinfo/3").unlink()
        (other / "fdinfo/3").symlink_to(proc / "619/stat")
    elif mutation == "fd-root-symlink":
        (other / "fd").rename(other / "real-fd")
        (other / "fd").symlink_to(other / "real-fd", target_is_directory=True)
    elif mutation == "foreign-pid-symlink":
        other.rename(proc / "renamed")
        other.symlink_to(proc / "renamed", target_is_directory=True)
    elif mutation == "over-pid-budget":
        for n in range(513):
            _pid(proc, 1000 + n, 83)
    elif mutation == "over-fd-budget":
        for n in range(257):
            _fd(other, 100 + n, "42")
    assert probe._visible_mount_fd_holders(scope, proc)["state"] == "UNKNOWN"


def test_fd_change_between_scans_rejected(tmp_path, monkeypatch):
    scope, proc = fixture(tmp_path)
    worker = _pid(proc, 620, 82)
    _fd(worker, 3, "99")
    real = probe._visible_mount_fd_holders
    calls = 0

    def raced(*args):
        nonlocal calls
        calls += 1
        if calls == 2:
            (worker / "fdinfo/3").write_text("mnt_id:\t42\n")
        return real(*args)

    monkeypatch.setattr(probe, "_visible_mount_fd_holders", raced)
    assert probe.observe(scope, proc_root=proc)["observations"]["visible_mount_fd_holders"]["state"] == "UNKNOWN"


def test_no_production_authority_and_source_selection():
    source = Path(probe.__file__).read_text()
    assert "from oh_goon.services" not in source and "from oh_goon.runtime" not in source
    root = Path(__file__).resolve().parents[4]
    script = (root / "scripts/qualify_b12_development_wsl.sh").read_text()
    assert "if [[ -f .checkpoint/B19_SOURCE.sha256 ]]; then embedded='.checkpoint/B19_SOURCE.sha256'; fi" in script
    assert script.index("B18_SOURCE.sha256") < script.index("B19_SOURCE.sha256")
    recipe = (root / "Makefile").read_text().split("qualify-development-wsl:", 1)[1].split("\n\n", 1)[0]
    assert recipe.index("source-transport-clean") < recipe.index("qualify_b12_development_wsl.sh")
