"""CO-R3N: opt-in, REAL kernel cgroup-v2 proof in the original QENV1 CORE ring.

Never a substitute for Enforcer/LACE or independent physical absence.  The caller
provides a fresh, empty, exclusively delegated disposable cgroup-v2 parent.
An allocation_pending crash intentionally leaves a quarantined native child;
this test never invents cleanup authority for the unknown original inode.
"""
from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import pytest

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.models.fleet_plan_isolation import CicdResourcePolicy
from oh_goon.integrations import _fleet_plan_cgroup_resources as cg


def _mount_path(text: str) -> str:
    import re
    return re.sub(r"\\([0-7]{3})", lambda m: chr(int(m.group(1), 8)), text)


def _require_disposable_delegated_cgroup(root: Path, mountinfo: str) -> dict[str, str]:
    """Pure fail-before-effects preflight; no probes may create/attach/kill."""
    if (not root.is_absolute() or root.is_symlink() or root.name.startswith(".")
            or not root.name.startswith("oh-goon-native-")
            or root.resolve() != root):
        raise RuntimeError("CO-R3N requires a canonical dedicated oh-goon-native-* cgroup")
    metadata = root.stat()
    if not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != os.getuid():
        raise RuntimeError("CO-R3N requires a delegated cgroup directory owned by this user")
    mounts: list[tuple[Path, str]] = []
    for line in mountinfo.splitlines():
        left, sep, right = line.partition(" - ")
        if not sep:
            continue
        columns, detail = left.split(), right.split()
        if len(columns) < 5 or not detail:
            continue
        mount = Path(_mount_path(columns[4]))
        if root == mount or mount not in root.parents:
            continue
        mounts.append((mount, detail[0]))
    if not mounts or max(mounts, key=lambda pair: len(pair[0].parts))[1] != "cgroup2":
        raise RuntimeError("CO-R3N requires a non-mount-root delegated cgroup-v2 filesystem")
    if any(child.is_dir() or child.is_symlink() for child in root.iterdir()):
        raise RuntimeError("CO-R3N requires a fresh empty delegated parent")
    for name in ("cgroup.procs", "cgroup.events", "cgroup.subtree_control"):
        path = root / name
        if path.is_symlink() or not path.is_file():
            raise RuntimeError(f"CO-R3N real kernel control unavailable: {name}")
    if (root / "cgroup.procs").read_text(encoding="ascii").strip():
        raise RuntimeError("CO-R3N delegated parent already contains processes")
    if not os.access(root, os.W_OK | os.X_OK):
        raise RuntimeError("CO-R3N delegated parent is not writable")
    return {"root": str(root), "filesystem": "cgroup2", "mount": str(max(mounts, key=lambda pair: len(pair[0].parts))[0])}


def _native_parent() -> Path:
    selected = os.environ.get("QUALIFY_R3N_CGROUP_ROOT", "")
    if not selected:
        raise RuntimeError("CO-R3N native witness was run without explicit QUALIFY_R3N_CGROUP_ROOT")
    root = Path(selected)
    _require_disposable_delegated_cgroup(root, Path("/proc/self/mountinfo").read_text(encoding="utf-8"))
    return root


def _lease(root: Path, state_root: Path) -> cg.CgroupV2AttemptLease:
    identity = uuid4().hex[:16]
    return cg.CgroupV2AttemptLease(
        root=root, cgroup=root / f"attempt-{identity}",
        state_file=state_root / "state" / f"{identity}.json",
        lock_path=state_root / "lock" / "original.lock",
        policy=CicdResourcePolicy(), attempt_root=state_root / "attempt",
    )


def test_real_cgroup_v2_original_process_attachment_and_teardown(tmp_path: Path) -> None:
    parent = _native_parent()
    lease = _lease(parent, tmp_path)
    # This invokes the REAL original Fleet owner: durable custody, mkdir,
    # identity binding, native cgroup.procs attachment, kill and removal.
    lease.open()
    assert lease.cgroup_identity is not None
    assert json.loads(lease.state_file.read_text())["phase"] == "identity_bound"
    command = (*lease.command_prefix(), sys.executable, "-c",
               "from pathlib import Path;print(Path('/proc/self/cgroup').read_text().strip())")
    result = subprocess.run(command, check=False, text=True, capture_output=True, timeout=15)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().endswith("/" + lease.cgroup.name)
    lease.close()
    assert not lease.state_file.exists() and not lease.cgroup.exists()
    print("CO_R3N_NATIVE_ATTACH=PASS original-owner=1 kernel=cgroup2", flush=True)


def test_real_cgroup_v2_stale_identity_cannot_start_payload(tmp_path: Path) -> None:
    """Negative native control: no payload may execute on a lost inode binding.

    This exercises the ORIGINAL process-attachment bootstrap, rather than a
    stand-alone test process or a simulated cgroupfs.  A failed bootstrap may
    not quietly run the caller's command outside the intended cgroup.
    """
    parent = _native_parent()
    lease = _lease(parent, tmp_path)
    lease.open()
    original = lease.cgroup_identity
    assert original is not None
    try:
        # The forged identity cannot be promoted to authority by argv or a
        # fresh read of a path.  The child must fail before executing payload.
        lease.cgroup_identity = (original[0], original[1] + 1)
        prefix = lease.command_prefix()
        result = subprocess.run(
            (*prefix, sys.executable, "-c", "print('CO_R3O_PAYLOAD_EXECUTED')"),
            check=False, text=True, capture_output=True, timeout=15,
        )
        assert result.returncode != 0
        assert "CO_R3O_PAYLOAD_EXECUTED" not in result.stdout
        assert not (lease.cgroup / "cgroup.procs").read_text(encoding="ascii").strip()
    finally:
        lease.cgroup_identity = original
        lease.close()
    assert not lease.cgroup.exists() and not lease.state_file.exists()
    print("CO_R3O_NATIVE_STALE_IDENTITY=REJECTED payload=NOT_STARTED", flush=True)


def test_real_cgroup_v2_original_nested_delegation(tmp_path: Path) -> None:
    """Real controller activation and child identities in the original owner.

    An explicitly provisioned native delegation must really support cpu,
    memory and pids. Failure is a failed native witness, not a hosted skip.
    This is not an independent proof that processes cannot escape containment.
    """
    parent = _native_parent()
    lease = _lease(parent, tmp_path)
    lease.nested_delegation = lease.cgroup / "nested-execution"
    lease.nested_monitor = lease.cgroup / "nested-monitor"
    lease.open()
    try:
        assert lease.nested_identity is not None
        assert lease.monitor_identity is not None
        actual = set((lease.cgroup / "cgroup.subtree_control").read_text(encoding="ascii").split())
        assert {"cpu", "memory", "pids"} <= actual
        assert cg._exact_cgroup_identity(lease.nested_delegation) == lease.nested_identity
        assert cg._exact_cgroup_identity(lease.nested_monitor) == lease.monitor_identity
        # Missing trusted child identity cannot become execution authority.
        lease.nested_identity = None
        with pytest.raises(FleetPlanInvalid, match="original cgroup identities"):
            lease.command_prefix()
    finally:
        lease.close()
    assert not lease.cgroup.exists() and not lease.state_file.exists()
    print("CO_R3O_NATIVE_CONTROLLER_DELEGATION=PASS identity=BOUND", flush=True)


def test_real_cgroup_v2_pending_allocation_crash_quarantines(tmp_path: Path) -> None:
    parent = _native_parent()
    lease = _lease(parent, tmp_path)
    program = """
import os
from pathlib import Path
from oh_goon.integrations import _fleet_plan_cgroup_resources as cg
from oh_goon.core.models.fleet_plan_isolation import CicdResourcePolicy
root=Path(os.environ['R3N_ROOT'])
name=os.environ['R3N_NAME']
state=Path(os.environ['R3N_STATE'])
lock=Path(os.environ['R3N_LOCK'])
lease=cg.CgroupV2AttemptLease(root=root,cgroup=root/name,state_file=state,lock_path=lock,policy=CicdResourcePolicy(),attempt_root=state.parent/'attempt')
original=Path.mkdir
def die(path,*args,**kwargs):
    original(path,*args,**kwargs)
    if path==lease.cgroup:os._exit(77)
Path.mkdir=die
lease.open()
"""
    env = dict(os.environ, R3N_ROOT=str(lease.root), R3N_NAME=lease.cgroup.name,
               R3N_STATE=str(lease.state_file), R3N_LOCK=str(lease.lock_path))
    result = subprocess.run([sys.executable, "-c", program], env=env, timeout=15, check=False)
    assert result.returncode == 77, result.returncode
    body = json.loads(lease.state_file.read_text(encoding="utf-8"))
    assert body["phase"] == "allocation_pending"
    assert "cgroup_dev" not in body and "cgroup_ino" not in body
    assert lease.cgroup.is_dir() and (lease.cgroup / "cgroup.events").is_file()
    original_identity = (lease.cgroup.stat().st_dev, lease.cgroup.stat().st_ino)
    with pytest.raises(FleetPlanInvalid, match="pending without durable kernel identity"):
        cg._reconcile_stale_cgroups(lease.root, lease.state_file.parent)
    assert lease.state_file.exists()
    assert (lease.cgroup.stat().st_dev, lease.cgroup.stat().st_ino) == original_identity
    print("CO_R3N_NATIVE_PENDING=QUARANTINED (NOT cleaned; independent custody required)", flush=True)
