"""B12D2B read-only rediscovery of journal-correlated orphaned native resources.

This code NEVER reclaims, sends signals, opens Bubblewrap, or issues teardown.
Missing facts block inventory; dead PIDs and empty cgroups NEVER imply absence.
"""
from __future__ import annotations

import json
import os
import stat
from pathlib import Path

from oh_goon.core.enforcer_lace_trust import canonical_bytes, digest
from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.ports.local_lace_orphan_inventory import LocalLaceOrphanSubject, LocalLaceOrphanInventory
from oh_goon.integrations.fleet_plan_local_lace_intent import _identity
from oh_goon.integrations.fleet_plan_local_lace_allocation import _native_list
from oh_goon.integrations._fleet_plan_cgroup_resources import _is_cgroup2_path, _process_identity


def _private_json(path: Path) -> dict[str, object]:
    """Read bounded private native records without following final-component symlinks."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0))
        try:
            before = os.fstat(fd)
            if (not stat.S_ISREG(before.st_mode) or before.st_mode & 0o077
                    or before.st_uid != os.geteuid() or before.st_nlink != 1):
                raise FleetPlanInvalid("unsafe original LACE native recovery record")
            raw = os.read(fd, 16_385)
            after = os.fstat(fd)
            visible = path.lstat()
            if (len(raw) > 16_384 or (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
                    != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
                    or (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
                    != (visible.st_dev, visible.st_ino, visible.st_size, visible.st_mtime_ns)):
                raise FleetPlanInvalid("original LACE native recovery record changed during scan")
            result = json.loads(raw)
        finally:
            os.close(fd)
    except (OSError, TypeError, ValueError) as exc:
        raise FleetPlanInvalid("original LACE native recovery record unreadable") from exc
    if type(result) is not dict:
        raise FleetPlanInvalid("original LACE native recovery record malformed")
    return result


def _matching_record(directory: Path, *, kind: str, subject: LocalLaceOrphanSubject,
                     resource_name: str) -> dict[str, object]:
    # Scan only a configured native owner directory, never arbitrary workload paths.
    if not directory.is_dir() or directory.is_symlink():
        raise FleetPlanInvalid("original LACE recovery directory unavailable")
    files = sorted(directory.glob("*.json"))
    if len(files) > 4096:
        raise FleetPlanInvalid("original LACE recovery record set exceeds bound")
    matches = []
    expected_binding = {"run_id": subject.run_id, "step_id": subject.step_id,
                        "intent_run_seq": subject.intent_run_seq,
                        "attempt_identity": subject.attempt_identity}
    for path in files:
        record = _private_json(path)
        binding = record.get("lace_intent")
        if binding is None:
            continue
        if type(binding) is not dict or set(binding) != set(expected_binding):
            raise FleetPlanInvalid("unverifiable LACE native lease identity")
        if binding != expected_binding:
            continue
        expected_fields = ({"pid", "process_identity", "cgroup", "lace_intent"} if kind == "cgroup"
                           else {"pid", "process_identity", "namespace", "host_veth", "table", "lace_intent"})
        if (set(record) != expected_fields or type(record["pid"]) is not int or record["pid"] < 1
                or type(record["process_identity"]) is not str or not record["process_identity"]):
            raise FleetPlanInvalid("unverifiable original native recovery owner")
        if record["cgroup" if kind == "cgroup" else "namespace"] != resource_name:
            raise FleetPlanInvalid("original LACE recovery record has mismatched native resource")
        matches.append(record)
    if len(matches) != 1:
        raise FleetPlanInvalid("original LACE orphan native owner missing or ambiguous")
    return matches[0]


def _read_kernel(path: Path, *, size: int = 262_144) -> str:
    try:
        if path.is_symlink():
            raise FleetPlanInvalid("native LACE process path is a symlink")
        with path.open("rb") as stream:
            content = stream.read(size + 1)
        if len(content) > size:
            raise FleetPlanInvalid("native LACE evidence exceeds bounded size")
        return content.decode("ascii")
    except (OSError, UnicodeError) as exc:
        raise FleetPlanInvalid("native LACE process/cgroup observation unavailable") from exc


def rediscover_local_orphan(subject: LocalLaceOrphanSubject, *, cgroup_root: Path,
                            cgroup_state_dir: Path, network_state_dir: Path,
                            ip: str, nft: str, proc_root: Path = Path("/proc")) -> LocalLaceOrphanInventory:
    """Return no-effect kernel observation for original journal subject.

    If the original journal never observed physical allocation, don't infer
    absence from missing recovery files. A future crash-window inventory owner
    must explicitly reconcile such intents under a separate fenced protocol.
    """
    if type(subject) is not LocalLaceOrphanSubject or not subject.subject_identity.startswith("sha256:"):
        raise FleetPlanInvalid("authenticated original LACE recovery subject required")
    if subject.observation is None:
        return LocalLaceOrphanInventory(subject.subject_identity, "INTENT_ONLY_UNKNOWN",
                                        None, None, None, (), None, None)
    observation = subject.observation
    if type(observation) is not dict or type(observation.get("cgroup_path")) is not str:
        raise FleetPlanInvalid("journal native observation malformed")
    cgroup = Path(observation["cgroup_path"])
    # No symlink traversal / substituted physical scope. cgroup_root is chosen by
    # trusted native configuration, not the recovered journal or workload.
    if (cgroup.parent != cgroup_root or cgroup.is_symlink() or cgroup_root.is_symlink()
            or not cgroup.is_dir() or not _is_cgroup2_path(cgroup)):
        raise FleetPlanInvalid("native orphan cgroup absent or outside original delegated root")
    cg_record = _matching_record(cgroup_state_dir, kind="cgroup", subject=subject,
                                 resource_name=cgroup.name)
    # A reused native owner PID is not a dead owner. It is an identity conflict.
    live_owner_birth = _process_identity(cg_record["pid"])
    if live_owner_birth is not None and live_owner_birth != cg_record["process_identity"]:
        raise FleetPlanInvalid("original LACE native owner PID birth changed")
    cg_stat = cgroup.stat()
    cg_identity = _identity("observed-cgroup", {
        "path": str(cgroup), "inode": cg_stat.st_ino, "device": cg_stat.st_dev,
        "owner_record": cg_record,
    })
    if cg_identity != observation.get("cgroup_identity"):
        raise FleetPlanInvalid("native orphan cgroup inode/device/owner drift")
    event_lines = _read_kernel(cgroup / "cgroup.events", size=4096).splitlines()
    populated = [line.split() for line in event_lines if line.startswith("populated ")]
    if len(populated) != 1 or populated[0] not in (["populated", "0"], ["populated", "1"]):
        raise FleetPlanInvalid("native cgroup populated state unverified")
    raw_pids = _read_kernel(cgroup / "cgroup.procs", size=65_536).split()
    if len(raw_pids) > 4096 or any(not item.isdigit() or int(item) < 1 for item in raw_pids):
        raise FleetPlanInvalid("native cgroup process list malformed")
    processes: list[tuple[int, str, str, str]] = []
    for pid in sorted({int(x) for x in raw_pids}):
        birth = _process_identity(pid)
        if birth is None:
            raise FleetPlanInvalid("native orphan process birth is unknown or raced")
        try:
            mount_ns = os.readlink(proc_root / str(pid) / "ns" / "mnt")
        except OSError as exc:
            raise FleetPlanInvalid("native orphan process mount namespace unavailable") from exc
        if not mount_ns.startswith("mnt:[") or not mount_ns.endswith("]"):
            raise FleetPlanInvalid("native orphan process mount namespace malformed")
        mountinfo = _read_kernel(proc_root / str(pid) / "mountinfo", size=1_048_576)
        if _process_identity(pid) != birth:
            raise FleetPlanInvalid("native orphan process birth changed during observation")
        try:
            after_namespace = os.readlink(proc_root / str(pid) / "ns" / "mnt")
        except OSError as exc:
            raise FleetPlanInvalid("native orphan process mount namespace raced") from exc
        if after_namespace != mount_ns or _read_kernel(proc_root / str(pid) / "mountinfo", size=1_048_576) != mountinfo:
            raise FleetPlanInvalid("native orphan process mount identity changed during observation")
        processes.append((pid, birth, mount_ns,
                          digest("oh-goon/b12d2b/mountinfo/v1", mountinfo.encode("ascii"))))
    if (cgroup.stat().st_ino != cg_stat.st_ino
            or cgroup.stat().st_dev != cg_stat.st_dev
            or _read_kernel(cgroup / "cgroup.procs", size=65_536).split() != raw_pids):
        raise FleetPlanInvalid("native orphan cgroup identity/process membership raced")
    # An empty cgroup cannot establish mount absence or complete process-tree
    # quiescence. Those are independent B12E kernel/descendant proofs.
    network_identity = None
    if observation.get("network_kind") == "host-netns":
        namespace = observation.get("network_namespace")
        host_veth = observation.get("host_veth")
        table = observation.get("nft_table")
        if not all(type(x) is str and x for x in (namespace, host_veth, table)):
            raise FleetPlanInvalid("original LACE network observation malformed")
        net_record = _matching_record(network_state_dir, kind="network", subject=subject,
                                      resource_name=namespace)
        if (net_record["pid"] != cg_record["pid"]
                or net_record["process_identity"] != cg_record["process_identity"]):
            raise FleetPlanInvalid("original LACE cgroup/network owner identity differs")
        if net_record["host_veth"] != host_veth or net_record["table"] != table:
            raise FleetPlanInvalid("original LACE native network name drift")
        network_identity = _identity("observed-host-network", {"owner_record": net_record})
        if network_identity != observation.get("network_identity"):
            raise FleetPlanInvalid("original LACE native network owner drift")
        namespaces = _native_list(ip, ("-j", "netns", "list"), kind="netns")
        links = _native_list(ip, ("-j", "link", "show"), kind="link")
        tables = _native_list(nft, ("-j", "list", "tables"), kind="nft")
        if (not any(item.get("name") == namespace for item in namespaces)
                or not any(item.get("ifname") in (host_veth,)
                           or (type(item.get("ifname")) is str and item["ifname"].startswith(host_veth + "@"))
                           for item in links)
                or not any(type(item.get("table")) is dict and item["table"].get("family") == "inet"
                           and item["table"].get("name") == table for item in tables)):
            raise FleetPlanInvalid("native orphan netns/veth/nft drift or absence: unresolved")
    elif observation.get("network_kind") != "not-allocated":
        raise FleetPlanInvalid("unknown native LACE network mode")
    record_identity = digest("oh-goon/b12d2b/native-owner-set/v1", canonical_bytes({
        "cgroup": cg_record, "network": network_identity, "processes": [list(p) for p in processes],
        "populated": populated[0][1],
    }))
    return LocalLaceOrphanInventory(subject.subject_identity, "OBSERVED_PRESENT", cg_identity,
                                    cg_stat.st_dev, cg_stat.st_ino, tuple(processes),
                                    network_identity, record_identity)
