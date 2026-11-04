"""Native, read-only /proc negative witness for a detached-session FD holder.

This cannot qualify a foreign PID namespace, missing tasks, detached mounts or
independently controlled observer.  No cgroup or mount mutations occur.
The real probe requires complete host procfs visibility and is explicit native
qualification; ordinary verification uses the deterministic holder-race case.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from oh_goon.integrations import linux_physical_observer as linux
from oh_goon.repository_tooling.local_lace_physical_preflight import parse_scope


def test_visible_namespace_fd_holder_disappearing_during_census_is_unknown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    proc = tmp_path / "proc"
    (proc / "sys/kernel/random").mkdir(parents=True)
    (proc / "sys/kernel/random/boot_id").write_text("holder-boot\n")
    for pid, birth, namespace in ((710, 87, 4026531100), (711, 88, 4026531199)):
        directory = proc / str(pid)
        (directory / "ns").mkdir(parents=True)
        (directory / "fd").mkdir()
        (directory / "stat").write_text(
            f"{pid} (worker) " + " ".join(["S"] + ["0"] * 18 + [str(birth)])
        )
        (directory / "ns/mnt").symlink_to(f"mnt:[{namespace}]")
        (directory / "ns/net").symlink_to(f"net:[{namespace + 1}]")
    (proc / "711/fd/4").symlink_to("mnt:[4026531100]")
    group = tmp_path / "group"
    group.mkdir()
    scope = parse_scope(
        {
            "run_id": "co-r3r-census",
            "subject_identity": "sha256:" + "e" * 64,
            "cgroup_path": str(group),
            "cgroup_device": group.stat().st_dev,
            "cgroup_inode": group.stat().st_ino,
            "tracked_pid": 710,
            "tracked_birth": "holder-boot:87",
            "mount_targets": ["/original"],
            "network_namespace": None,
            "host_veth": None,
            "nft_table": None,
        }
    )
    baseline = linux._namespace_fd_holders(scope, proc)
    assert baseline["state"] == "PRESENT"
    assert "matching_namespace_fds=1" in baseline["detail"]
    read = linux._read_regular
    holder = proc / "711/stat"

    def holder_disappears(path: Path, limit: int) -> str:
        value = read(path, limit)
        if path == holder:
            holder.unlink()
        return value

    monkeypatch.setattr(linux, "_read_regular", holder_disappears)
    assert linux._namespace_fd_holders(scope, proc)["state"] == "UNKNOWN"


@pytest.mark.certification
@pytest.mark.skipif(sys.platform != "linux", reason="requires real Linux procfs")
def test_detached_session_inherited_namespace_fd_remains_visible_but_not_proof(tmp_path):
    pid = os.getpid()
    boot = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    start = linux._birth(Path(f"/proc/{pid}/stat").read_text())
    cg = tmp_path / "witness-only"
    cg.mkdir()
    scope = parse_scope(
        {
            "run_id": "co-r3r-native",
            "subject_identity": "sha256:" + "e" * 64,
            "cgroup_path": str(cg),
            "cgroup_device": cg.stat().st_dev,
            "cgroup_inode": cg.stat().st_ino,
            "tracked_pid": pid,
            "tracked_birth": f"{boot}:{start}",
            "mount_targets": ["/not-an-original-real-mount"],
            "network_namespace": None,
            "host_veth": None,
            "nft_table": None,
        }
    )
    fd = os.open("/proc/self/ns/mnt", os.O_RDONLY | os.O_CLOEXEC)
    child = None
    try:
        child = subprocess.Popen(
            [
                sys.executable,
                "-c",
                "import os,sys,time; os.fstat(int(sys.argv[1])); print('READY',flush=True); time.sleep(8)",
                str(fd),
            ],
            pass_fds=(fd,),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
            text=True,
        )
        assert child.stdout is not None
        assert child.stdout.readline().strip() == "READY"
        expected = os.readlink("/proc/self/ns/mnt")
        assert os.readlink(f"/proc/{child.pid}/fd/{fd}") == expected
        fact = linux._namespace_fd_holders(scope, Path("/proc"))
        if fact["state"] == "UNKNOWN":
            pytest.skip("real procfs census incomplete; holder NOT qualified")
        assert fact["state"] == "PRESENT"
        match = re.search(r"matching_namespace_fds=(\d+)", fact["detail"])
        assert match is not None and int(match[1]) >= 2
        # Even a positive real holder observation is not authorization to
        # assert all writers absent or to perform physical deletion.
        assert linux._namespace_fd_holders(scope, Path("/proc"))["state"] != "ABSENT"
    finally:
        if child is not None:
            child.terminate()
            try:
                child.communicate(timeout=3)
            except subprocess.TimeoutExpired:
                child.kill()
                child.communicate(timeout=3)
        os.close(fd)
