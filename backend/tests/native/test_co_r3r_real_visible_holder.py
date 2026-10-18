"""Native, read-only /proc negative witness for a detached-session FD holder.

This cannot qualify a foreign PID namespace, missing tasks, detached mounts or
independently controlled observer.  No cgroup or mount mutations occur.
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


@pytest.mark.skipif(sys.platform != "linux", reason="requires real Linux procfs")
def test_detached_session_inherited_namespace_fd_remains_visible_but_not_proof(tmp_path):
    pid = os.getpid()
    boot = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    start = linux._birth(Path(f"/proc/{pid}/stat").read_text())
    cg = tmp_path / "witness-only"
    cg.mkdir()
    scope = parse_scope({
        "run_id": "co-r3r-native", "subject_identity": "sha256:" + "e" * 64,
        "cgroup_path": str(cg), "cgroup_device": cg.stat().st_dev,
        "cgroup_inode": cg.stat().st_ino, "tracked_pid": pid,
        "tracked_birth": f"{boot}:{start}", "mount_targets": ["/not-an-original-real-mount"],
        "network_namespace": None, "host_veth": None, "nft_table": None,
    })
    fd = os.open("/proc/self/ns/mnt", os.O_RDONLY | os.O_CLOEXEC)
    child = None
    try:
        child = subprocess.Popen(
            [sys.executable, "-c", "import os,sys,time; os.fstat(int(sys.argv[1])); print('READY',flush=True); time.sleep(8)", str(fd)],
            pass_fds=(fd,), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL, start_new_session=True, text=True,
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
