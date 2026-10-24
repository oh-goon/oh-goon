"""Read-only local-kernel crash window observation under original native locks.

Never infers physical quiescence; missing/partial native objects remain obligations.
This observer cannot delete resources, replay work or issue a cleanup capability.
"""

from __future__ import annotations

from contextlib import ExitStack
from pathlib import Path
from typing import cast

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.ports.local_lace_crash_reconciliation import LocalLaceCrashObservation
from oh_goon.core.ports.local_lace_orphan_inventory import LocalLaceOrphanSubject
from oh_goon.integrations._fleet_plan_cgroup_resources import (
    _is_cgroup2_path,
    _process_identity,
    _resource_lock,
)
from oh_goon.integrations._fleet_plan_restricted_egress import _egress_allocation_lock
from oh_goon.integrations.fleet_plan_local_lace_allocation import _native_list
from oh_goon.integrations.fleet_plan_local_lace_intent import _identity
from oh_goon.integrations.fleet_plan_local_lace_orphan_inventory import (
    _matching_record,
    _read_kernel,
    rediscover_local_orphan,
)


def observe_native_crash(
    subject: LocalLaceOrphanSubject,
    *,
    cgroup_root: Path,
    cgroup_state_dir: Path,
    cgroup_lock: Path,
    network_state_dir: Path,
    network_lock: Path,
    ip: str,
    nft: str,
) -> LocalLaceCrashObservation:
    if type(subject) is not LocalLaceOrphanSubject:
        raise FleetPlanInvalid("original authenticated recovery subject required")
    if subject.observation is None:
        return LocalLaceCrashObservation(subject.subject_identity, "INTENT_ONLY_UNKNOWN")
    with ExitStack() as locks:
        locks.enter_context(_resource_lock(cgroup_lock))
        locks.enter_context(_egress_allocation_lock(network_lock))
        # Full present inventory is the strongest still-unqualified state.
        try:
            present = rediscover_local_orphan(
                subject,
                cgroup_root=cgroup_root,
                cgroup_state_dir=cgroup_state_dir,
                network_state_dir=network_state_dir,
                ip=ip,
                nft=nft,
            )
        except FleetPlanInvalid:
            pass  # May be partially removed after a crashed native attempt.
        else:
            if present.state != "OBSERVED_PRESENT":
                raise FleetPlanInvalid("unexpected native recovery observation")
            return LocalLaceCrashObservation(subject.subject_identity, "PRESENT_MATCHING")
        observation = subject.observation
        # Only a specifically observed *surviving cgroup* and *fully absent
        # network trio* can be classified as partial. All other drifts stay
        # unresolved and never imply successful cleanup.
        if observation.get("network_kind") != "host-netns":
            return LocalLaceCrashObservation(subject.subject_identity, "UNRESOLVED")
        cgroup = Path(observation.get("cgroup_path", ""))
        if (
            cgroup.parent != cgroup_root
            or cgroup.is_symlink()
            or not cgroup.is_dir()
            or cgroup_root.is_symlink()
            or not _is_cgroup2_path(cgroup)
        ):
            return LocalLaceCrashObservation(subject.subject_identity, "UNRESOLVED")
        try:
            record = _matching_record(
                cgroup_state_dir, kind="cgroup", subject=subject, resource_name=cgroup.name
            )
            network = _matching_record(
                network_state_dir,
                kind="network",
                subject=subject,
                resource_name=observation["network_namespace"],
            )
            if (
                record["pid"] != network["pid"]
                or record["process_identity"] != network["process_identity"]
                or network["host_veth"] != observation["host_veth"]
                or network["table"] != observation["nft_table"]
            ):
                return LocalLaceCrashObservation(subject.subject_identity, "UNRESOLVED")
            current_birth = _process_identity(cast(int, record["pid"]))
            if current_birth is not None and current_birth != record["process_identity"]:
                return LocalLaceCrashObservation(subject.subject_identity, "UNRESOLVED")
            metadata = cgroup.stat()
            identity = _identity(
                "observed-cgroup",
                {
                    "path": str(cgroup),
                    "inode": metadata.st_ino,
                    "device": metadata.st_dev,
                    "owner_record": record,
                },
            )
            if (
                identity != observation["cgroup_identity"]
                or _read_kernel(cgroup / "cgroup.procs", size=65_536).strip()
                or _read_kernel(cgroup / "cgroup.events", size=4096)
                .splitlines()
                .count("populated 0")
                != 1
            ):
                return LocalLaceCrashObservation(subject.subject_identity, "UNRESOLVED")
            namespace = observation["network_namespace"]
            veth = observation["host_veth"]
            table = observation["nft_table"]
            namespaces = _native_list(ip, ("-j", "netns", "list"), kind="netns")
            links = _native_list(ip, ("-j", "link", "show"), kind="link")
            tables = _native_list(nft, ("-j", "list", "tables"), kind="nft")
            if (
                any(x.get("name") == namespace for x in namespaces)
                or any(
                    (ifname := x.get("ifname")) == veth
                    or (isinstance(ifname, str) and ifname.startswith(veth + "@"))
                    for x in links
                )
                or any(
                    isinstance(native_table := x.get("table"), dict)
                    and native_table.get("family") == "inet"
                    and native_table.get("name") == table
                    for x in tables
                )
            ):
                return LocalLaceCrashObservation(subject.subject_identity, "UNRESOLVED")
            if (
                _read_kernel(cgroup / "cgroup.procs", size=65_536).strip()
                or _read_kernel(cgroup / "cgroup.events", size=4096)
                .splitlines()
                .count("populated 0")
                != 1
                or (cgroup.stat().st_dev, cgroup.stat().st_ino)
                != (metadata.st_dev, metadata.st_ino)
            ):
                return LocalLaceCrashObservation(subject.subject_identity, "UNRESOLVED")
        except (FleetPlanInvalid, OSError, KeyError, TypeError, ValueError):
            return LocalLaceCrashObservation(subject.subject_identity, "UNRESOLVED")
        return LocalLaceCrashObservation(subject.subject_identity, "PARTIAL_NETWORK")
