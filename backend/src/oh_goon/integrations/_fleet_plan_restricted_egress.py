from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass
from ipaddress import IPv4Network, ip_network
from pathlib import Path
from typing import Protocol

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.models.fleet_plan_step_contract import CidrAllowlistPlanEgress, PlanStepEgress
from oh_goon.core.path_authority import ensure_private_directory
from oh_goon.engine.command import run_command
from oh_goon.integrations._fleet_plan_cgroup_resources import (
    require_independent_native_parent_custody,
)
from oh_goon.integrations.exact_file_write import publish_text_file, remove_file_durably


class RestrictedEgressHost(Protocol):
    ip_executable: str | None
    nft_executable: str | None
    egress_pool: str | None
    egress_lock: Path
    egress_state_dir: Path


@dataclass(slots=True)
class RestrictedEgressLease:
    """Private process-scoped Linux netns + nftables implementation."""

    ip: str
    nft: str
    namespace: str
    host_veth: str
    peer_veth: str
    table: str
    pool: IPv4Network
    lock_path: Path
    rules_file: Path
    egress: CidrAllowlistPlanEgress
    state_file: Path
    host_address: str = ""
    guest_address: str = ""
    subnet: str = ""
    lace_intent: dict[str, object] | None = None
    _owned_record_written: bool = False

    @classmethod
    def create(
        cls,
        backend: RestrictedEgressHost,
        attempt_root: Path,
        egress: PlanStepEgress,
    ) -> RestrictedEgressLease | None:
        if not isinstance(egress, CidrAllowlistPlanEgress):
            return None
        require_restricted_egress_host(backend)
        # A root-owned netns is NOT exclusive original LACE physical custody.
        # The original cgroup/LACE gate is shared, not reimplemented here.
        require_independent_native_parent_custody(operation="native restricted-egress admission")
        digest = hashlib.sha256(f"{attempt_root}:{os.getpid()}".encode()).hexdigest()
        suffix = digest[:10]
        return cls(
            ip=str(backend.ip_executable),
            nft=str(backend.nft_executable),
            namespace=f"uvf{suffix}",
            host_veth=f"ufh{suffix}",
            peer_veth=f"ufp{suffix}",
            table=f"uvf_{suffix}",
            pool=_restricted_egress_pool(backend),
            lock_path=backend.egress_lock,
            rules_file=attempt_root / "egress.nft",
            egress=egress,
            state_file=backend.egress_state_dir / f"{suffix}.json",
        )

    def open(self, *, cancel_requested: Callable[[], bool] | None) -> None:
        # Fail before even the attempt-local rules file or durable record.
        # A constructed lease cannot bypass creation/admission.
        require_independent_native_parent_custody(operation="native restricted-egress allocation")
        try:
            self._open_link(cancel_requested)
            self.rules_file.write_text(self._nft_rules(), encoding="utf-8")
            for command in (
                (
                    self.ip,
                    "netns",
                    "exec",
                    self.namespace,
                    self.ip,
                    "addr",
                    "add",
                    self.guest_address,
                    "dev",
                    self.peer_veth,
                ),
                (self.ip, "netns", "exec", self.namespace, self.ip, "link", "set", "lo", "up"),
                (
                    self.ip,
                    "netns",
                    "exec",
                    self.namespace,
                    self.ip,
                    "link",
                    "set",
                    self.peer_veth,
                    "up",
                ),
                (
                    self.ip,
                    "netns",
                    "exec",
                    self.namespace,
                    self.ip,
                    "route",
                    "add",
                    "default",
                    "via",
                    self.host_address.split("/", 1)[0],
                ),
                (self.nft, "-f", str(self.rules_file)),
            ):
                self._run_setup(command, cancel_requested)
        except BaseException:
            # Reconciliation may fail before this attempt creates ANY resource.
            # Never delete a prior owner's namespace merely because its names
            # collide with this attempt. Only clean up after our own durable
            # native lease record has actually been published.
            if self._owned_record_written:
                self.close()
            raise

    def _open_link(self, cancel_requested: Callable[[], bool] | None) -> None:
        require_independent_native_parent_custody(
            operation="native restricted-egress link creation"
        )
        with _egress_allocation_lock(self.lock_path):
            _reconcile_stale_egress_leases(self)
            subnet = _select_free_link_subnet(
                self.ip,
                self.pool,
                seed=self.namespace,
                cancel_requested=cancel_requested,
            )
            hosts = tuple(subnet.hosts())
            self.subnet = str(subnet)
            self.host_address = f"{hosts[0]}/{subnet.prefixlen}"
            self.guest_address = f"{hosts[1]}/{subnet.prefixlen}"
            _write_egress_lease_record(self)
            self._owned_record_written = True
            for command in (
                (self.ip, "netns", "add", self.namespace),
                (
                    self.ip,
                    "link",
                    "add",
                    self.host_veth,
                    "type",
                    "veth",
                    "peer",
                    "name",
                    self.peer_veth,
                ),
                (self.ip, "link", "set", self.peer_veth, "netns", self.namespace),
                (self.ip, "addr", "add", self.host_address, "dev", self.host_veth),
                (self.ip, "link", "set", self.host_veth, "up"),
            ):
                self._run_setup(command, cancel_requested)

    def _run_setup(
        self,
        command: tuple[str, ...],
        cancel_requested: Callable[[], bool] | None,
    ) -> None:
        # Callers bypassing open/_open_link cannot issue netns/nft mutations.
        require_independent_native_parent_custody(
            operation="native restricted-egress setup command"
        )
        result = run_command(
            *command,
            timeout=10.0,
            env=_control_environment(),
            preserve_native_executable_path=False,
            cancel_requested=cancel_requested,
        )
        if result.returncode != 0:
            raise FleetPlanInvalid(
                f"Fleet restricted egress setup failed: {' '.join(command)}: {result.stderr}"
            )

    def close(self) -> None:
        # Never unlink a record or enter deletion from a rollback/close bypass.
        require_independent_native_parent_custody(operation="native restricted-egress teardown")
        # The same native allocator lock guards delete -> absence -> retirement.
        # A parallel creator must not race with the absence observation.
        with _egress_allocation_lock(self.lock_path):
            _cleanup_egress_resources(self.ip, self.nft, self.namespace, self.host_veth, self.table)
            self.rules_file.unlink(missing_ok=True)
            remove_file_durably(self.state_file)
            self._owned_record_written = False

    def command_prefix(self) -> tuple[str, ...]:
        # No direct netns command may be released from a hand-built lease
        # without independent original LACE namespace custody.
        require_independent_native_parent_custody(
            operation="native restricted-egress process launch"
        )
        return (self.ip, "netns", "exec", self.namespace)

    def _nft_rules(self) -> str:
        lines = [
            f"table inet {self.table} {{",
            "  chain input {",
            "    type filter hook input priority 0; policy accept;",
        ]
        self._append_destination_rules(lines)
        lines.extend(
            [
                f'    iifname "{self.host_veth}" drop',
                "  }",
                "  chain forward {",
                "    type filter hook forward priority 0; policy accept;",
                f'    oifname "{self.host_veth}" ct state established,related accept',
            ]
        )
        self._append_destination_rules(lines)
        lines.extend(
            [
                f'    iifname "{self.host_veth}" drop',
                "  }",
                "  chain postrouting {",
                "    type nat hook postrouting priority srcnat; policy accept;",
                f"    ip saddr {self.subnet} masquerade",
                "  }",
                "}",
                "",
            ]
        )
        return "\n".join(lines)

    def _append_destination_rules(self, lines: list[str]) -> None:
        for rule in self.egress.rules:
            ports = ", ".join(str(port) for port in rule.ports)
            lines.append(
                f'    iifname "{self.host_veth}" ip daddr {rule.cidr} '
                f"{rule.protocol} dport {{ {ports} }} accept"
            )


def require_restricted_egress_host(backend: RestrictedEgressHost) -> None:
    if os.name == "nt" or not hasattr(os, "geteuid") or os.geteuid() != 0:
        raise FleetPlanInvalid(
            "Fleet cidr_allowlist egress currently requires Linux root network namespace authority"
        )
    _restricted_egress_pool(backend)
    for name, executable in (("ip", backend.ip_executable), ("nft", backend.nft_executable)):
        if not executable or not Path(executable).is_file():
            raise FleetPlanInvalid(
                f"Fleet cidr_allowlist egress requires host {name} for kernel enforcement"
            )
    forwarding = Path("/proc/sys/net/ipv4/ip_forward")
    if not forwarding.is_file() or forwarding.read_text(encoding="utf-8").strip() != "1":
        raise FleetPlanInvalid(
            "Fleet cidr_allowlist egress requires IPv4 forwarding enabled before Fleet starts; "
            "Fleet does not mutate this host-global setting"
        )


_EGRESS_NAME = re.compile(r"^[0-9a-f]{10}$")


def _write_egress_lease_record(lease: RestrictedEgressLease) -> None:
    # The pending record may mutate a directory, so deny direct helper entry.
    require_independent_native_parent_custody(operation="native restricted-egress pending record")
    ensure_private_directory(lease.state_file.parent)
    identity = _process_identity(os.getpid())
    if identity is None:
        raise FleetPlanInvalid(
            "Fleet cannot identify its own process for restricted-egress recovery"
        )
    payload = {
        "pid": os.getpid(),
        "process_identity": identity,
        "namespace": lease.namespace,
        "host_veth": lease.host_veth,
        "table": lease.table,
    }
    if lease.lace_intent is not None:
        payload["lace_intent"] = lease.lace_intent
    publish_text_file(lease.state_file, json.dumps(payload, sort_keys=True))


def _reconcile_stale_egress_leases(lease: RestrictedEgressLease) -> None:
    state_dir = lease.state_file.parent
    if not state_dir.exists():
        return
    for path in state_dir.glob("*.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            pid = int(payload["pid"])
            process_identity = str(payload["process_identity"])
            namespace = str(payload["namespace"])
            host_veth = str(payload["host_veth"])
            table = str(payload["table"])
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
            raise FleetPlanInvalid(
                f"Fleet restricted-egress recovery metadata is unreadable; "
                f"refusing reconciliation: {path}"
            ) from exc
        suffix = namespace.removeprefix("uvf")
        if (
            not _EGRESS_NAME.fullmatch(suffix)
            or host_veth != f"ufh{suffix}"
            or table != f"uvf_{suffix}"
        ):
            raise FleetPlanInvalid(
                f"Fleet restricted-egress recovery metadata has invalid resource identity: {path}"
            )
        # LACE cleanup needs the authenticated journal recovery owner; a dead
        # process alone never authorizes namespace/nft destruction.
        if _process_identity(pid) == process_identity:
            continue
        if "lace_intent" in payload:
            raise FleetPlanInvalid(
                "Fleet LACE-owned network requires original journal recovery; refusing PID scavenging"
            )
        # CO-R3T: dead process and matching resource names are not a kernel
        # custody permit. A stale namespace may be held by foreign processes;
        # deleting it here could destroy resources outside the Run authority.
        # Preserve this recovery record for the original LACE recovery owner.
        raise FleetPlanInvalid(
            "Fleet legacy network orphan lacks independent physical custody; refusing destructive PID scavenging"
        )


def _process_identity(pid: int) -> str | None:
    try:
        stat_text = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
        start_ticks = stat_text.rsplit(")", 1)[1].split()[19]
        boot_id = Path("/proc/sys/kernel/random/boot_id").read_text(encoding="utf-8").strip()
    except (OSError, IndexError):
        return None
    return f"{boot_id}:{start_ticks}"


def _cleanup_egress_resources(
    ip: str, nft: str, namespace: str, host_veth: str, table: str
) -> None:
    """Original native network deletion owner; unproven physical custody REFUSES.

    The post-delete probes were never authority to start effects and cannot
    establish global writer/namespace absence. They remain useful ONLY after
    independently provisioned host custody is enforced at this same native cut.
    """
    require_independent_native_parent_custody(operation="native restricted-egress removal")
    failures: list[str] = []
    for command in (
        (nft, "delete", "table", "inet", table),
        (ip, "netns", "delete", namespace),
        (ip, "link", "delete", host_veth),
    ):
        try:
            result = run_command(
                *command,
                timeout=5.0,
                env=_control_environment(),
                preserve_native_executable_path=False,
            )
            if result.returncode != 0:
                failures.append(f"{command[1:]}: exit={result.returncode}")
        except Exception as exc:
            failures.append(f"{command[1:]}: {type(exc).__name__}")
    if not failures:
        # Strictly parse supported *native* JSON output, rather than interpreting
        # failures from 'show <name>' as evidence that the name is absent.
        probes = (
            ((ip, "-j", "netns", "list"), "namespace", namespace),
            ((ip, "-j", "link", "show"), "host_veth", host_veth),
            ((nft, "-j", "list", "tables"), "nft_table", table),
        )
        for command, kind, name in probes:
            try:
                result = run_command(
                    *command,
                    timeout=5.0,
                    env=_control_environment(),
                    preserve_native_executable_path=False,
                )
                if result.returncode != 0:
                    failures.append(f"{kind} absence probe: exit={result.returncode}")
                    continue
                if not _native_resource_absent(result.stdout, kind, name):
                    failures.append(f"{kind} native absence NOT PROVEN")
            except Exception as exc:
                failures.append(f"{kind} absence probe: {type(exc).__name__}")
    if failures:
        raise FleetPlanInvalid(
            "Fleet restricted-egress cleanup UNPROVEN; retaining recovery record: "
            + "; ".join(failures)
        )


def _native_resource_absent(stdout: str, kind: str, name: str) -> bool:
    """Closed-world check for iproute2/nft JSON list surfaces, not a delete-RC proxy."""
    try:
        decoded = json.loads(stdout)
        if kind == "nft_table":
            if type(decoded) is not dict or type(decoded.get("nftables")) is not list:
                return False
            for item in decoded["nftables"]:
                if type(item) is not dict:
                    return False
                if "table" in item:
                    table = item["table"]
                    if (
                        type(table) is not dict
                        or type(table.get("family")) is not str
                        or type(table.get("name")) is not str
                    ):
                        return False
                    if table["family"] == "inet" and table["name"] == name:
                        return False
                elif "metainfo" not in item:
                    return False
            return True
        if kind not in {"namespace", "host_veth"} or type(decoded) is not list:
            return False
        field = "name" if kind == "namespace" else "ifname"
        for item in decoded:
            if type(item) is not dict or type(item.get(field)) is not str or not item[field]:
                return False
            if item[field] == name or (kind == "host_veth" and item[field].startswith(name + "@")):
                return False
        return True
    except (ValueError, TypeError):
        return False


def _restricted_egress_pool(backend: RestrictedEgressHost) -> IPv4Network:
    value = backend.egress_pool
    if not value:
        raise FleetPlanInvalid(
            "Fleet cidr_allowlist egress requires executor egress_pool "
            "(an otherwise unrouted IPv4 /16..../30 pool used only for Fleet veth links)"
        )
    try:
        network = ip_network(value, strict=True)
    except ValueError as exc:
        raise FleetPlanInvalid(f"Invalid executor egress_pool {value!r}") from exc
    if network.version != 4 or network.prefixlen < 16 or network.prefixlen > 30:
        raise FleetPlanInvalid(
            "Executor egress_pool must be a canonical IPv4 network between /16 and /30"
        )
    return IPv4Network(str(network))


@contextmanager
def _egress_allocation_lock(path: Path):
    import fcntl
    import stat

    ensure_private_directory(path.parent)
    flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags, 0o600)
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise FleetPlanInvalid(f"Fleet egress allocation lock is not a regular file: {path}")
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


def _select_free_link_subnet(
    ip_executable: str,
    pool: IPv4Network,
    *,
    seed: str,
    cancel_requested: Callable[[], bool] | None,
) -> IPv4Network:
    result = run_command(
        ip_executable,
        "-o",
        "-4",
        "route",
        "show",
        timeout=10.0,
        env=_control_environment(),
        preserve_native_executable_path=False,
        cancel_requested=cancel_requested,
    )
    if result.returncode != 0:
        raise FleetPlanInvalid(
            f"Fleet cannot inspect host routes for egress allocation: {result.stderr}"
        )
    occupied: list[IPv4Network] = []
    for line in result.stdout.splitlines():
        token = line.split(maxsplit=1)[0] if line.strip() else ""
        if not token or token == "default":
            continue
        try:
            route = ip_network(token, strict=False)
        except ValueError:
            continue
        if route.version == 4:
            occupied.append(IPv4Network(str(route)))

    candidates = tuple(pool.subnets(new_prefix=30)) if pool.prefixlen < 30 else (pool,)
    start = int(hashlib.sha256(seed.encode("utf-8")).hexdigest()[:8], 16) % len(candidates)
    for offset in range(len(candidates)):
        candidate = candidates[(start + offset) % len(candidates)]
        if not any(candidate.overlaps(route) for route in occupied):
            return candidate
    raise FleetPlanInvalid(
        f"Fleet egress link pool {pool} has no free /30 subnet; reserve a larger unrouted pool"
    )


def _control_environment() -> dict[str, str]:
    return {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C", "LC_ALL": "C"}
