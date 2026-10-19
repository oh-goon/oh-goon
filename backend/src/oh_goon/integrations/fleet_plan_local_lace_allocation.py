"""Observe original local kernel-owned resources before launching work.

This is an executor-side observation, not independent host attestation. Never
infer presence from the allocator's success code or a recovery filename alone.
"""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

from oh_goon.core.enforcer_lace_trust import canonical_bytes, digest
from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.ports.local_lace_allocation import LocalLaceAllocationObservation
from oh_goon.core.ports.local_lace_intent import LocalLaceIntentProof
from oh_goon.engine.command import run_command
from oh_goon.integrations._fleet_plan_cgroup_resources import _is_cgroup2_path, _process_identity
from oh_goon.integrations._fleet_plan_restricted_egress import _control_environment
from oh_goon.integrations.fleet_plan_local_lace_intent import _identity, local_resource_identities
from oh_goon.integrations.fleet_plan_local_lace_recovery import lace_resource_binding


def _original_record(
    path: Path, *, expected: dict[str, object], proof: LocalLaceIntentProof | None = None
) -> dict[str, object]:
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0))
        try:
            metadata = os.fstat(fd)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_mode & 0o077
                or metadata.st_uid != os.geteuid()
            ):
                raise FleetPlanInvalid("local LACE resource owner record has unsafe permissions")
            raw = os.read(fd, 16_385)
            if len(raw) > 16_384:
                raise FleetPlanInvalid("local LACE resource owner record exceeds bounded size")
            after_fd = os.fstat(fd)
            after_path = path.lstat()
            before = (metadata.st_ino, metadata.st_dev, metadata.st_mtime_ns, metadata.st_size)
            after = (after_fd.st_ino, after_fd.st_dev, after_fd.st_mtime_ns, after_fd.st_size)
            visible = (
                after_path.st_ino,
                after_path.st_dev,
                after_path.st_mtime_ns,
                after_path.st_size,
            )
            if before != after or before != visible:
                raise FleetPlanInvalid(
                    "local LACE resource owner record changed during observation"
                )
            record = json.loads(raw)
        finally:
            os.close(fd)
    except (OSError, ValueError, TypeError) as exc:
        raise FleetPlanInvalid("local LACE native owner record unavailable") from exc
    required = {*expected, "pid", "process_identity"}
    if proof is not None:
        required.add("lace_intent")
    if (
        type(record) is not dict
        or set(record) != required
        or type(record.get("pid")) is not int
        or record["pid"] != os.getpid()
        or type(record.get("process_identity")) is not str
        or record["process_identity"] != _process_identity(os.getpid())
    ):
        raise FleetPlanInvalid("local LACE native owner record differs from live executor")
    if proof is not None and record["lace_intent"] != lace_resource_binding(proof):
        raise FleetPlanInvalid("native recovery record does not bind original LACE journal intent")
    for key, value in expected.items():
        if record.get(key) != value:
            raise FleetPlanInvalid("local LACE native owner record has foreign resource identity")
    return record


def _native_list(executable: str, args: tuple[str, ...], *, kind: str) -> list[dict[str, object]]:
    result = run_command(executable, *args, timeout=5.0, env=_control_environment())
    if result.returncode != 0:
        raise FleetPlanInvalid(f"local LACE {kind} native observation failed")
    try:
        data = json.loads(result.stdout)
        if kind == "nft":
            if type(data) is not dict or type(data.get("nftables")) is not list:
                raise ValueError("invalid nft response")
            data = data["nftables"]
        if type(data) is not list or any(type(item) is not dict for item in data):
            raise ValueError("invalid native response")
        return data
    except (ValueError, TypeError) as exc:
        raise FleetPlanInvalid(f"local LACE {kind} native observation malformed") from exc


def observe_local_allocation(
    cgroup: object,
    network: object | None,
    request: object,
    *,
    proof: LocalLaceIntentProof | None = None,
) -> LocalLaceAllocationObservation:
    """Observe original resource leases; not the workload's synthetic claims."""
    resource_intent_identity = digest(
        "oh-goon/b12d/resource-binding/v1",
        canonical_bytes(local_resource_identities(request)),
    )
    path = cgroup.cgroup
    if (
        not isinstance(path, Path)
        or not path.is_dir()
        or not _is_cgroup2_path(path)
        or not (path / "cgroup.procs").is_file()
        or not (path / "cgroup.events").is_file()
    ):
        raise FleetPlanInvalid("local LACE cannot witness actual cgroup-v2 allocation")
    cg_record = _original_record(cgroup.state_file, expected={"cgroup": path.name}, proof=proof)
    cg_stat = path.stat()
    cgroup_identity = _identity(
        "observed-cgroup",
        {
            "path": str(path),
            "inode": cg_stat.st_ino,
            "device": cg_stat.st_dev,
            "owner_record": cg_record,
        },
    )
    if network is None:
        if request.attempt.egress.mode in {"cidr_allowlist", "hostname_allowlist"}:
            raise FleetPlanInvalid("local LACE restricted egress requires native network lease")
        return LocalLaceAllocationObservation(
            resource_intent_identity,
            str(path),
            cgroup_identity,
            "not-allocated",
            None,
            None,
            None,
            _identity("observed-network-absent", {"kind": "no-host-egress-lease"}),
        )
    net_record = _original_record(
        network.state_file,
        expected={
            "namespace": network.namespace,
            "host_veth": network.host_veth,
            "table": network.table,
        },
        proof=proof,
    )
    namespaces = _native_list(network.ip, ("-j", "netns", "list"), kind="netns")
    links = _native_list(network.ip, ("-j", "link", "show"), kind="link")
    nftables = _native_list(network.nft, ("-j", "list", "tables"), kind="nft")
    if (
        not any(item.get("name") == network.namespace for item in namespaces)
        or not any(
            item.get("ifname") == network.host_veth
            or (
                type(item.get("ifname")) is str
                and item["ifname"].startswith(network.host_veth + "@")
            )
            for item in links
        )
        or not any(
            type(item.get("table")) is dict
            and item["table"].get("family") == "inet"
            and item["table"].get("name") == network.table
            for item in nftables
        )
    ):
        raise FleetPlanInvalid("local LACE native netns/veth/nft resource presence unproven")
    return LocalLaceAllocationObservation(
        resource_intent_identity,
        str(path),
        cgroup_identity,
        "host-netns",
        network.namespace,
        network.host_veth,
        network.table,
        _identity("observed-host-network", {"owner_record": net_record}),
    )
