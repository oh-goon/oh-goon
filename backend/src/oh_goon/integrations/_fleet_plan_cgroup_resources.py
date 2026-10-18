from __future__ import annotations

import hashlib
import json
import os
import signal
import stat
import sys
import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.path_authority import ensure_private_directory
from oh_goon.core.models.fleet_plan_evidence import PlanResourceEvidence
from oh_goon.core.models.fleet_plan_isolation import CicdResourcePolicy
from oh_goon.integrations._fleet_plan_resource_evidence import write_attempt_resource_evidence
from oh_goon.integrations.exact_file_write import publish_text_file, remove_file_durably

_RESOURCE_PROTOCOL = 1
NESTED_CGROUP_DELEGATED_ENV = "OH_GOON_NESTED_CGROUP_DELEGATED"
NESTED_CGROUP_SUBGROUP_ENV = "OH_GOON_NESTED_CGROUP_SUBGROUP"
NESTED_CGROUP_PROCESS_SUBGROUP = "oh-goon-nested-process"
_NESTED_DELEGATION_SUBTREE = "nested-execution"
_NESTED_MONITOR_SUBTREE = "nested-monitor"
_NESTED_STRICT_CONTROLLERS = {"cpu", "memory", "pids"}
# The -S child has no original LACE host-capability verification channel.
# Never assemble a second process-attachment authority inside an inline script.
# CO-R3U2B cannot re-enable this until the ORIGINAL independently provisioned
# LACE owner securely binds the child at the actual kernel effect cut.
_CGROUP_EXEC = """raise RuntimeError('original LACE independent kernel custody NOT PROVEN before process attachment')
"""


def require_independent_native_parent_custody(
    *,
    operation: str,
    requirement: object | None = None,
) -> None:
    """Fail BEFORE native mutation until original LACE gains OS-enforced custody.

    This is the original cgroup effect owner's only physical custody gate, not
    an issuer. Even a syntactically complete immutable scope is NOT a native
    capability. Do not accept environment flags, callbacks or local receipts as
    evidence of exclusive parent mutation control. CO-R3U host provisioning is
    still required before this gate can ever grant an effect.
    """
    from oh_goon.core.models.original_native_custody import OriginalNativeCustodyRequirement

    if requirement is not None and type(requirement) is not OriginalNativeCustodyRequirement:
        raise FleetPlanInvalid("original LACE native custody requirement type invalid")
    if requirement is not None and not requirement.complete_scope:
        # No future host custodian may interpret a scope missing generation,
        # parent identity or child identity as permission to perform effects.
        # Even a complete scope still has NO self-issued kernel capability.
        raise FleetPlanInvalid(
            f"original LACE physical custody NOT PROVEN for {operation}; "
            "authenticated generation and kernel parent/child scope incomplete"
        )
    raise FleetPlanInvalid(
        f"original LACE physical custody NOT PROVEN for {operation}; "
        "no independently provisioned kernel parent owner"
    )


def _refuse_unprovisioned_kernel_effect(cgroup: Path, *, operation: str) -> None:
    """Unconditionally refuse original cgroup effects without kernel custody.

    CO-R3U2B2A closes the mount-detection fail-open: mountinfo read failures,
    missing paths, bind/overmount ambiguity and negative filesystem guesses
    cannot issue work. Any simulated post-custody behavior belongs *only* in
    explicitly injected tests; the production effect path never self-issues.
    The path is intentionally not inspected: path identity is not authority.
    """
    require_independent_native_parent_custody(operation=operation)


def delegated_process_subgroup(
    environment: Mapping[str, str],
    *,
    outer_delegated_env: str,
    outer_subgroup_env: str,
) -> str | None:
    if environment.get(NESTED_CGROUP_DELEGATED_ENV) == "1":
        return environment.get(NESTED_CGROUP_SUBGROUP_ENV, "").strip()
    if environment.get(outer_delegated_env) == "1":
        return environment.get(outer_subgroup_env, "").strip()
    return None

class CgroupResourceHost(Protocol):
    cgroup_root: Path | None
    cgroup_lock: Path
    cgroup_state_dir: Path

@dataclass(slots=True)
class CgroupV2AttemptLease:
    """One cgroup-v2 process-tree envelope owned by a single Fleet attempt."""

    root: Path
    cgroup: Path
    state_file: Path
    lock_path: Path
    policy: CicdResourcePolicy
    attempt_root: Path
    nested_delegation: Path | None = None
    nested_monitor: Path | None = None
    lace_intent: dict[str, object] | None = None
    # Pinned from original cgroup creation, never reconstructed after trust loss.
    cgroup_identity: tuple[int, int] | None = None
    nested_identity: tuple[int, int] | None = None
    monitor_identity: tuple[int, int] | None = None

    @classmethod
    def create(
        cls,
        backend: CgroupResourceHost,
        attempt_root: Path,
        policy: CicdResourcePolicy,
        *,
        nested_strict_execution: bool = False,
    ) -> CgroupV2AttemptLease:
        required_controllers = _required_resource_controllers(policy)
        if nested_strict_execution:
            required_controllers |= _NESTED_STRICT_CONTROLLERS
            require_cgroup_v2_host(
                backend,
                policy,
                required_controllers=required_controllers,
            )
        else:
            require_cgroup_v2_host(backend, policy)
        # No native allocation/write may start while real parent custody is absent.
        require_independent_native_parent_custody(operation="native cgroup admission")
        assert backend.cgroup_root is not None
        digest = hashlib.sha256(f"{attempt_root.resolve()}:{os.getpid()}".encode()).hexdigest()[:16]
        cgroup = backend.cgroup_root.resolve() / f"attempt-{digest}"
        return cls(
            root=backend.cgroup_root.resolve(),
            cgroup=cgroup,
            state_file=backend.cgroup_state_dir / f"{digest}.json",
            lock_path=backend.cgroup_lock,
            policy=policy,
            attempt_root=attempt_root,
            nested_delegation=(
                cgroup / _NESTED_DELEGATION_SUBTREE if nested_strict_execution else None
            ),
            nested_monitor=(
                cgroup / _NESTED_MONITOR_SUBTREE if nested_strict_execution else None
            ),
        )

    def open(self) -> None:
        # A constructed lease is not independent OS authority. Guard direct
        # open() callers too, including pre-creation pending-record mutation.
        _refuse_unprovisioned_kernel_effect(self.root, operation="native cgroup allocation")
        with _resource_lock(self.lock_path):
            _reconcile_stale_cgroups(self.root, self.state_file.parent)
            # A durable pending allocation must precede the first mkdir effect.
            # If the process dies between mkdir and inode publication, recovery
            # sees this record and refuses to infer deletion authority from name.
            _write_pending_resource_lease(self)
            try:
                self.cgroup.mkdir(mode=0o700)
            except OSError as exc:
                # An error or lost acknowledgement does not prove that mkdir
                # had no physical effect. Preserve the pending quarantine.
                raise FleetPlanInvalid(
                    f"Fleet cgroup allocation result unproven; retaining pending custody: {exc}"
                ) from exc
            try:
                self.cgroup_identity = _exact_cgroup_identity(self.cgroup)
                # Durable identity must precede all mutable controller effects.
                # If a later initialization effect fails, the original native
                # recovery owner can rediscover exactly the allocated inode.
                _write_resource_lease(self)
                _apply_resource_limits(
                    self.cgroup, self.policy, expected_identity=self.cgroup_identity,
                )
                _open_nested_delegation(self)
            except BaseException as original_error:
                if self.cgroup_identity is None:
                    # The mkdir may have succeeded, but it has no proven kernel
                    # identity. In particular, never kill an unknown pathname.
                    raise FleetPlanInvalid(
                        "Fleet allocation identity unproven; refusing unbound rollback"
                    ) from original_error
                try:
                    _require_original_cgroup(self.cgroup, self.cgroup_identity)
                    _kill_cgroup(self.cgroup, expected_identity=self.cgroup_identity)
                    _wait_for_cgroup_empty(
                        self.cgroup, expected_identity=self.cgroup_identity,
                    )
                    _remove_cgroup(self.cgroup, expected_identity=self.cgroup_identity)
                except BaseException as rollback_error:
                    # Keep the persisted identity record for original recovery.
                    # This is not a completed teardown or physical-absence ACK.
                    raise FleetPlanInvalid(
                        "Fleet allocation rollback unproven; retaining original recovery custody"
                    ) from rollback_error
                remove_file_durably(self.state_file)
                raise

    def command_prefix(self) -> tuple[str, ...]:
        # Constructing a launch command is a work-admission cut. A directly
        # constructed lease must not bypass the canonical native LACE owner.
        _refuse_unprovisioned_kernel_effect(
            self.cgroup, operation="native cgroup process launch",
        )
        if self.nested_delegation is not None:
            if self.nested_monitor is None or self.nested_identity is None or self.monitor_identity is None:
                raise FleetPlanInvalid("Fleet nested process launch lacks original cgroup identities")
            return (
                sys.executable,
                "-m",
                "oh_goon.integrations._fleet_plan_nested_cgroup_launcher",
                str(self.nested_delegation),
                str(self.nested_monitor),
                *(str(value) for value in (*self.nested_identity, *self.monitor_identity)),
                "--",
            )
        if self.cgroup_identity is None:
            raise FleetPlanInvalid("Fleet direct process launch lacks original cgroup identity")
        return (
            sys.executable, "-S", "-c", _CGROUP_EXEC, str(self.cgroup),
            str(self.cgroup_identity[0]), str(self.cgroup_identity[1]),
        )

    def close(self) -> PlanResourceEvidence:
        _refuse_unprovisioned_kernel_effect(self.cgroup, operation="native cgroup teardown")
        with _resource_lock(self.lock_path):
            if self.cgroup_identity is None:
                raise FleetPlanInvalid("Fleet original cgroup creation identity is unavailable")
            _require_original_cgroup(self.cgroup, self.cgroup_identity)
            _kill_cgroup(self.cgroup, expected_identity=self.cgroup_identity)
            _wait_for_cgroup_empty(self.cgroup, expected_identity=self.cgroup_identity)
            evidence = _read_resource_evidence(self.cgroup, self.policy)
            _require_original_cgroup(self.cgroup, self.cgroup_identity)
            _remove_cgroup(self.cgroup, expected_identity=self.cgroup_identity)
            # Publish attempt metrics only AFTER native cgroup retirement.
            # A failed removal must not leave a misleading completion receipt.
            write_attempt_resource_evidence(self.attempt_root, evidence)
            remove_file_durably(self.state_file)
        return evidence

def _apply_resource_limits(
    cgroup: Path, policy: CicdResourcePolicy, *, expected_identity: tuple[int, int],
) -> None:
    """Apply all controller limits through the same original cgroup handle.

    The directory identity remains creation-bound. A changed pathname cannot
    redirect a limit write into a new owner's cgroup. This is not exclusive
    kernel-enforced parent custody or authenticated trust generation proof.
    """
    _refuse_unprovisioned_kernel_effect(cgroup, operation="native cgroup limit write")
    with _bound_cgroup_directory(cgroup, expected_identity) as directory:
        def write(name: str, value: str) -> None:
            _require_original_cgroup(cgroup, expected_identity)
            try:
                control = os.open(
                    name, os.O_WRONLY | os.O_NOFOLLOW | os.O_CLOEXEC,
                    dir_fd=directory,
                )
            except OSError as exc:
                raise FleetPlanInvalid(
                    f"Fleet original cgroup limit {name} unavailable"
                ) from exc
            try:
                metadata = os.fstat(control)
                if not stat.S_ISREG(metadata.st_mode):
                    raise FleetPlanInvalid(
                        f"Fleet original cgroup limit {name} is not a control file"
                    )
                _require_original_cgroup(cgroup, expected_identity)
                encoded = value.encode("ascii")
                if os.write(control, encoded) != len(encoded):
                    raise FleetPlanInvalid(
                        f"Fleet original cgroup limit {name} write incomplete"
                    )
            except OSError as exc:
                raise FleetPlanInvalid(
                    f"Fleet original cgroup limit {name} write failed"
                ) from exc
            finally:
                os.close(control)

        if policy.cpu_millis is not None:
            write("cpu.max", f"{policy.cpu_millis * 100} 100000")
        if policy.memory_bytes is not None:
            write("memory.max", str(policy.memory_bytes))
            try:
                os.stat("memory.swap.max", dir_fd=directory, follow_symlinks=False)
            except FileNotFoundError:
                pass
            except OSError as exc:
                raise FleetPlanInvalid("Fleet original cgroup swap control unobservable") from exc
            else:
                write("memory.swap.max", "0")
        if policy.max_processes is not None:
            write("pids.max", str(policy.max_processes))
        _require_original_cgroup(cgroup, expected_identity)

def _open_nested_delegation(lease: CgroupV2AttemptLease) -> None:
    if lease.nested_delegation is None:
        return
    if lease.cgroup_identity is None:
        raise FleetPlanInvalid("Fleet nested cgroup original identity unavailable")
    enable_delegated_cgroup_v2_controllers(
        lease.cgroup,
        required_controllers=_required_resource_controllers(lease.policy) | _NESTED_STRICT_CONTROLLERS,
        expected_identity=lease.cgroup_identity,
    )
    assert lease.nested_monitor is not None
    lease.nested_identity = _mkdir_bound_cgroup_child(
        lease.cgroup, lease.cgroup_identity, _NESTED_DELEGATION_SUBTREE,
    )
    lease.monitor_identity = _mkdir_bound_cgroup_child(
        lease.cgroup, lease.cgroup_identity, _NESTED_MONITOR_SUBTREE,
    )

def _mkdir_bound_cgroup_child(
    parent: Path, parent_identity: tuple[int, int], name: str,
) -> tuple[int, int]:
    """Create a nested cgroup only below the original allocated parent inode."""
    if not name or name in (".", "..") or "/" in name or "\x00" in name or name.startswith("cgroup."):
        raise FleetPlanInvalid("Fleet invalid original delegated child name")
    _refuse_unprovisioned_kernel_effect(parent, operation="native delegated child creation")
    with _bound_cgroup_directory(parent, parent_identity) as descriptor:
        _require_original_cgroup(parent, parent_identity)
        try:
            os.mkdir(name, mode=0o700, dir_fd=descriptor)
            info = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
        except OSError as exc:
            raise FleetPlanInvalid("Fleet nested cgroup creation failed") from exc
        if not stat.S_ISDIR(info.st_mode):
            raise FleetPlanInvalid("Fleet nested cgroup identity is not a directory")
        _require_original_cgroup(parent, parent_identity)
        child = parent / name
        identity = (info.st_dev, info.st_ino)
        _require_original_cgroup(child, identity)
        return identity


def _write_bound_cgroup_control(
    cgroup: Path, identity: tuple[int, int], name: str, payload: bytes,
) -> None:
    """Single native write boundary for delegated controllers and membership."""
    if name not in ("cgroup.subtree_control", "cgroup.procs"):
        raise FleetPlanInvalid("Fleet unrecognized mutable delegated cgroup control")
    _refuse_unprovisioned_kernel_effect(cgroup, operation="native delegated control write")
    with _bound_cgroup_directory(cgroup, identity) as descriptor:
        _require_original_cgroup(cgroup, identity)
        try:
            control = os.open(
                name, os.O_WRONLY | os.O_NOFOLLOW | os.O_CLOEXEC,
                dir_fd=descriptor,
            )
        except OSError as exc:
            raise FleetPlanInvalid(f"Fleet delegated {name} control unavailable") from exc
        try:
            if not stat.S_ISREG(os.fstat(control).st_mode):
                raise FleetPlanInvalid(f"Fleet delegated {name} is not a kernel control")
            _require_original_cgroup(cgroup, identity)
            if os.write(control, payload) != len(payload):
                raise FleetPlanInvalid(f"Fleet delegated {name} write incomplete")
            _require_original_cgroup(cgroup, identity)
        except OSError as exc:
            raise FleetPlanInvalid(f"Fleet delegated {name} effect failed") from exc
        finally:
            os.close(control)


def _inspect_delegated_cgroup_v2_root(
    root: Path,
    *,
    required_controllers: set[str],
) -> tuple[Path, set[str]]:
    # The supplied root is the authority being inspected.  Do not pre-empt that
    # inspection with a global /sys probe: certified/unit-test execution may run in
    # a mount namespace where /sys/fs/cgroup is intentionally not exposed even
    # though the caller supplied a bounded test/delegated root.  Physical cgroup-v2
    # proof remains fail-closed below through _is_cgroup2_path(root) + controller
    # files.  Non-Linux platforms still fail before any Linux-specific inspection.
    if os.name == "nt" or not sys.platform.startswith("linux"):
        raise FleetPlanInvalid("Fleet resource enforcement requires Linux cgroup v2")
    root = root.resolve()
    if not root.is_dir():
        raise FleetPlanInvalid(f"Fleet delegated cgroup-v2 root does not exist: {root}")
    if not _is_cgroup2_path(root):
        raise FleetPlanInvalid(
            f"Fleet delegated resource root is not backed by a cgroup-v2 filesystem: {root}"
        )
    required_files = ["cgroup.procs", "cgroup.controllers", "cgroup.subtree_control"]
    controller_files = {
        "cpu": "cpu.max",
        "memory": "memory.max",
        "pids": "pids.max",
    }
    required_files.extend(
        controller_files[name]
        for name in sorted(required_controllers)
        if name in controller_files
    )
    missing = [name for name in required_files if not (root / name).exists()]
    if missing:
        raise FleetPlanInvalid(
            "Fleet delegated cgroup-v2 root lacks required controllers/files: " + ", ".join(missing)
        )
    try:
        available = set((root / "cgroup.controllers").read_text(encoding="ascii").split())
        enabled = set((root / "cgroup.subtree_control").read_text(encoding="ascii").split())
    except OSError as exc:
        raise FleetPlanInvalid(
            f"Fleet cannot inspect delegated cgroup-v2 controllers under {root}: {exc}"
        ) from exc
    missing_available = sorted(required_controllers - available)
    if missing_available:
        raise FleetPlanInvalid(
            "Fleet delegated cgroup-v2 root cannot enforce requested child controllers: "
            "unavailable=" + ",".join(missing_available)
        )
    # os.access(W_OK) is insufficient (especially as root) when a cgroup2
    # superblock is mounted read-only. This is a necessary host prerequisite,
    # NOT exclusive custody and NEVER an authorization grant. Do not try a
    # write to test delegation: qualification itself must be non-destructive.
    try:
        filesystem_flags = os.statvfs(root).f_flag
    except OSError as exc:
        raise FleetPlanInvalid(
            f"Fleet delegated cgroup-v2 root mount status unavailable: {root}"
        ) from exc
    if filesystem_flags & getattr(os, "ST_RDONLY", 1):
        raise FleetPlanInvalid(f"Fleet delegated cgroup-v2 root is read-only: {root}")
    if not os.access(root, os.W_OK):
        raise FleetPlanInvalid(f"Fleet delegated cgroup-v2 root is not writable: {root}")
    return root, enabled

def _write_subtree_controllers(
    root: Path, controllers: set[str], *, expected_identity: tuple[int, int] | None = None,
) -> None:
    if not controllers:
        return
    # Only a creation-time supplied identity qualifies an allocated attempt.
    # Standalone delegated-root setup pins a snapshot, not authenticated custody.
    identity = expected_identity if expected_identity is not None else _exact_cgroup_identity(root)
    payload = " ".join(f"+{name}" for name in sorted(controllers)).encode("ascii")
    _write_bound_cgroup_control(root, identity, "cgroup.subtree_control", payload)


def _write_process_to_cgroup(
    cgroup: Path, pid: int, *, expected_identity: tuple[int, int],
) -> None:
    if pid <= 0:
        raise FleetPlanInvalid("Fleet cannot attach nonpositive process id")
    _write_bound_cgroup_control(
        cgroup, expected_identity, "cgroup.procs", f"{pid}\n".encode("ascii"),
    )


def place_current_process_in_delegated_cgroup_v2_subgroup(
    root: Path,
    *,
    subgroup: str,
    required_controllers: set[str],
) -> Path:
    """Move this process below a delegated unit root before enabling domain controllers."""

    root, _enabled = _inspect_delegated_cgroup_v2_root(
        root, required_controllers=required_controllers
    )
    if (
        not subgroup
        or subgroup in {".", ".."}
        or "/" in subgroup
        or "\x00" in subgroup
        or subgroup.startswith("cgroup.")
    ):
        raise FleetPlanInvalid(f"Invalid delegated cgroup-v2 process subgroup: {subgroup!r}")
    root_identity = _exact_cgroup_identity(root)
    child_identity = _mkdir_bound_cgroup_child(root, root_identity, subgroup)
    child = root / subgroup
    try:
        _write_process_to_cgroup(
            child, os.getpid(), expected_identity=child_identity,
        )
    except FleetPlanInvalid:
        # Failure before an authenticated attachment must not delete a foreign
        # replacement. Reuse the one original guarded native remover.
        try:
            _remove_cgroup(child, expected_identity=child_identity)
        except FleetPlanInvalid as cleanup_error:
            raise FleetPlanInvalid(
                "Fleet delegated process subgroup rollback unproven"
            ) from cleanup_error
        raise
    _require_original_cgroup(child, child_identity)
    return child

def enable_delegated_cgroup_v2_controllers(
    root: Path,
    *,
    required_controllers: set[str],
    expected_identity: tuple[int, int] | None = None,
) -> Path:
    """Enable child controllers inside a cgroup subtree already delegated to us.

    This is establishment, not a weaker validation path: after enabling any missing
    controllers the same strict physical validator used by Fleet execution runs again.
    """

    root, enabled = _inspect_delegated_cgroup_v2_root(
        root, required_controllers=required_controllers
    )
    if expected_identity is not None:
        _require_original_cgroup(root, expected_identity)
        _write_subtree_controllers(
            root, required_controllers - enabled, expected_identity=expected_identity,
        )
        _require_original_cgroup(root, expected_identity)
    else:
        _write_subtree_controllers(root, required_controllers - enabled)
    return require_delegated_cgroup_v2_root(
        root, required_controllers=required_controllers
    )

def require_delegated_cgroup_v2_root(
    root: Path,
    *,
    required_controllers: set[str],
) -> Path:
    """Validate one physical cgroup-v2 delegation boundary for child execution.

    This is the canonical physical validator shared by runtime execution and local
    certification bootstrap. Callers may choose which controllers they require,
    but they must not duplicate filesystem/controller semantics elsewhere.
    """

    root, enabled = _inspect_delegated_cgroup_v2_root(
        root, required_controllers=required_controllers
    )
    missing_enabled = sorted(required_controllers - enabled)
    if missing_enabled:
        raise FleetPlanInvalid(
            "Fleet delegated cgroup-v2 root cannot enforce requested child controllers: "
            "not-enabled-for-children=" + ",".join(missing_enabled)
        )
    return root

def require_cgroup_v2_host(
    backend: CgroupResourceHost,
    policy: CicdResourcePolicy,
    *,
    required_controllers: set[str] | None = None,
) -> None:
    root = backend.cgroup_root
    if root is None:
        raise FleetPlanInvalid(
            "Bubblewrap Fleet CI/CD resource enforcement requires executor cgroup_root "
            "pointing at an operator-delegated cgroup-v2 subtree"
        )
    required = (
        _required_resource_controllers(policy)
        if required_controllers is None
        else set(required_controllers)
    )
    require_delegated_cgroup_v2_root(root, required_controllers=required)

def _required_resource_controllers(policy: CicdResourcePolicy) -> set[str]:
    required_controllers: set[str] = set()
    if policy.cpu_millis is not None:
        required_controllers.add("cpu")
    if policy.memory_mib is not None:
        required_controllers.add("memory")
    if policy.max_processes is not None:
        required_controllers.add("pids")
    return required_controllers

def _is_cgroup2_path(path: Path) -> bool:
    """Prove that ``path`` is covered by a cgroup2 mount in this mount namespace."""

    try:
        lines = Path("/proc/self/mountinfo").read_text(encoding="utf-8").splitlines()
    except OSError:
        return False
    best_match: tuple[int, str] | None = None
    for line in lines:
        before, separator, after = line.partition(" - ")
        if not separator:
            continue
        fields = before.split()
        filesystem_fields = after.split()
        if len(fields) < 5 or not filesystem_fields:
            continue
        mount_point = Path(_decode_mountinfo_path(fields[4]))
        try:
            covered = path == mount_point or path.is_relative_to(mount_point)
        except ValueError:
            covered = False
        if not covered:
            continue
        rank = len(mount_point.parts)
        if best_match is None or rank > best_match[0]:
            best_match = (rank, filesystem_fields[0])
    return best_match is not None and best_match[1] == "cgroup2"

def _decode_mountinfo_path(value: str) -> str:
    for escaped, replacement in (
        ("\\040", " "),
        ("\\011", "\t"),
        ("\\012", "\n"),
        ("\\134", "\\"),
    ):
        value = value.replace(escaped, replacement)
    return value

def cgroup_configuration_identity(backend: CgroupResourceHost) -> dict[str, object]:
    root = backend.cgroup_root
    if root is None:
        return {"resource_protocol": _RESOURCE_PROTOCOL, "cgroup_root": None}
    controllers = ""
    subtree_control = ""
    try:
        controllers = (root / "cgroup.controllers").read_text(encoding="utf-8").strip()
        subtree_control = (root / "cgroup.subtree_control").read_text(encoding="utf-8").strip()
    except OSError:
        pass
    return {
        "resource_protocol": _RESOURCE_PROTOCOL,
        "cgroup_root": str(root.resolve()),
        "controllers": controllers.split(),
        "subtree_control": subtree_control.split(),
    }

def _read_resource_evidence(
    cgroup: Path,
    policy: CicdResourcePolicy,
) -> PlanResourceEvidence:
    cpu_usage = _keyed_int(cgroup / "cpu.stat", "usage_usec")
    memory_peak = _plain_int(cgroup / "memory.peak")
    pids_peak = _plain_int(cgroup / "pids.peak")
    oom_kill = _keyed_int(cgroup / "memory.events", "oom_kill")
    return PlanResourceEvidence(
        cpu_millis=policy.cpu_millis,
        memory_bytes=policy.memory_bytes,
        max_processes=policy.max_processes,
        cpu_usage_usec=cpu_usage,
        memory_peak_bytes=memory_peak,
        pids_peak=pids_peak,
        oom_kill_count=oom_kill,
    )

def _write_limit(path: Path, value: str) -> None:
    # Retained only for old fixture callers; never a second live control writer.
    _refuse_unprovisioned_kernel_effect(path.parent, operation="native cgroup direct limit write")
    try:
        path.write_text(value, encoding="ascii")
    except OSError as exc:
        raise FleetPlanInvalid(
            f"Fleet cannot enforce cgroup-v2 resource limit {path.name}: {exc}"
        ) from exc

def _plain_int(path: Path) -> int | None:
    try:
        value = path.read_text(encoding="ascii").strip()
    except OSError:
        return None
    return int(value) if value.isdigit() else None

def _keyed_int(path: Path, key: str) -> int | None:
    try:
        lines = path.read_text(encoding="ascii").splitlines()
    except OSError:
        return None
    for line in lines:
        name, _, value = line.partition(" ")
        if name == key and value.isdigit():
            return int(value)
    return None

def _write_pending_resource_lease(lease: CgroupV2AttemptLease) -> None:
    """Durably record unresolved authority *before* the native mkdir effect.

    A pending record has intentionally no device/inode. Recovery cannot
    perform pathname-based cleanup; a dead allocation needs independent
    reconciliation instead of guessing ownership.
    """
    ensure_private_directory(lease.state_file.parent)
    if lease.state_file.exists() or lease.state_file.is_symlink():
        raise FleetPlanInvalid("Fleet allocation recovery record already exists; refusing overwrite")
    identity = _process_identity(os.getpid())
    if identity is None:
        raise FleetPlanInvalid("Fleet cannot identify its own process before cgroup allocation")
    payload: dict[str, object] = {
        "phase": "allocation_pending",
        "pid": os.getpid(),
        "process_identity": identity,
        "cgroup": lease.cgroup.name,
    }
    if lease.lace_intent is not None:
        payload["lace_intent"] = lease.lace_intent
    publish_text_file(lease.state_file, json.dumps(payload, sort_keys=True))


def _write_resource_lease(lease: CgroupV2AttemptLease) -> None:
    ensure_private_directory(lease.state_file.parent)
    identity = _process_identity(os.getpid())
    if identity is None:
        raise FleetPlanInvalid("Fleet cannot identify its own process for cgroup recovery")
    if lease.cgroup_identity is None:
        raise FleetPlanInvalid("Fleet cannot persist an unpinned original cgroup lease")
    _require_original_cgroup(lease.cgroup, lease.cgroup_identity)
    payload = {
        "phase": "identity_bound",
        "pid": os.getpid(),
        "process_identity": identity,
        "cgroup": lease.cgroup.name,
        "cgroup_dev": lease.cgroup_identity[0],
        "cgroup_ino": lease.cgroup_identity[1],
    }
    if lease.lace_intent is not None:
        payload["lace_intent"] = lease.lace_intent
    publish_text_file(lease.state_file, json.dumps(payload, sort_keys=True))

def _reconcile_stale_cgroups(root: Path, state_dir: Path) -> None:
    if not state_dir.exists():
        return
    for path in state_dir.glob("*.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            pid = int(payload["pid"])
            identity = str(payload["process_identity"])
            name = str(payload["cgroup"])
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
            raise FleetPlanInvalid(
                f"Fleet cgroup recovery metadata is unreadable; refusing destructive reconciliation: {path}"
            ) from exc
        if (not name.startswith("attempt-") or len(name) != len("attempt-") + 16
                or any(character not in "0123456789abcdef" for character in name[8:])):
            raise FleetPlanInvalid(
                f"Fleet cgroup recovery metadata has invalid cgroup name: {path}"
            )
        # Original authenticated LACE resources are never PID-scavenged. The
        # journal must issue a fenced recovery claim and native teardown must
        # independently prove resource absence before retirement.
        if _process_identity(pid) == identity:
            continue
        if "lace_intent" in payload:
            raise FleetPlanInvalid(
                "Fleet LACE-owned cgroup requires original journal recovery; refusing PID scavenging"
            )
        # A process may have died after mkdir but before an inode-bound
        # publication. Absence/presence of the *name* never supplies the
        # unknown original device/inode, even if it currently looks empty.
        phase = payload.get("phase", "identity_bound")
        if phase == "allocation_pending":
            raise FleetPlanInvalid(
                "Fleet orphan allocation is pending without durable kernel identity; refusing cleanup"
            )
        if phase != "identity_bound":
            raise FleetPlanInvalid("Fleet orphan allocation has unknown recovery phase")
        # This is ONLY the original *non-LACE* legacy orphan path. A dead PID
        # and a cgroup basename alone cannot authorize physical mutation: the
        # same path may now belong to someone else. Old records with no pinned
        # kernel identity must be quarantined rather than guessed at.
        device = payload.get("cgroup_dev")
        inode = payload.get("cgroup_ino")
        if (type(device) is not int or type(inode) is not int
                or device < 1 or inode < 1):
            raise FleetPlanInvalid(
                "Fleet orphan cgroup lacks durable original kernel identity; refusing cleanup"
            )
        cgroup = root / name
        # CO-R3T: even an exact stored inode and a dead creator do NOT prove
        # independently enforced parent mutation custody. In particular,
        # rmdir(name, dir_fd=...) can destroy a substituted foreign entry at
        # the syscall cut. Keep the original record and quarantine the orphan;
        # the original LACE recovery owner must establish host custody first.
        # Reading/checking the identity here is NOT permission to mutate it.
        if cgroup.is_symlink():
            raise FleetPlanInvalid("Fleet orphan cgroup path is an untrusted symlink")
        if cgroup.exists():
            _require_original_cgroup(cgroup, (device, inode))
        raise FleetPlanInvalid(
            "Fleet legacy orphan lacks independent physical custody; refusing destructive PID scavenging"
        )

def _kill_cgroup(
    cgroup: Path, *, expected_identity: tuple[int, int] | None = None,
) -> None:
    """Use the original kernel cgroup directory for a bound destructive write.

    The descriptor is pinned before opening cgroup.kill relative to that same
    descriptor. A path replacement after pinning cannot redirect the write to
    an unrelated cgroup. This is LOCAL cgroup containment, never LACE-wide
    physical writer absence. Unbound pre-publication cleanup retains its
    existing best-effort behavior; durable recovery/close MUST pass identity.
    """
    _refuse_unprovisioned_kernel_effect(cgroup, operation="native cgroup kill")
    if expected_identity is not None:
        try:
            flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
            descriptor = os.open(cgroup, flags)
        except OSError as exc:
            raise FleetPlanInvalid("Fleet original cgroup handle unavailable") from exc
        try:
            directory_stat = os.fstat(descriptor)
            if not stat.S_ISDIR(directory_stat.st_mode) or (
                directory_stat.st_dev, directory_stat.st_ino
            ) != expected_identity:
                raise FleetPlanInvalid("Fleet original cgroup handle identity replaced")
            # An old directory may remain reachable through a held handle after
            # pathname replacement. The physical write is still to that pinned
            # resource, but do not intentionally operate after path loss.
            _require_original_cgroup(cgroup, expected_identity)
            try:
                control = os.open(
                    "cgroup.kill", os.O_WRONLY | os.O_NOFOLLOW | os.O_CLOEXEC,
                    dir_fd=descriptor,
                )
            except OSError as exc:
                raise FleetPlanInvalid("Fleet original cgroup.kill unavailable; refusing PID fallback") from exc
            try:
                control_stat = os.fstat(control)
                if not stat.S_ISREG(control_stat.st_mode):
                    raise FleetPlanInvalid("Fleet original cgroup.kill is not a kernel control file")
                _require_original_cgroup(cgroup, expected_identity)
                written = os.write(control, b"1")
                if written != 1:
                    raise FleetPlanInvalid("Fleet original cgroup.kill write incomplete")
            except OSError as exc:
                raise FleetPlanInvalid("Fleet original cgroup.kill write failed") from exc
            finally:
                os.close(control)
        finally:
            os.close(descriptor)
        return

    # Original best-effort cleanup for a failed *unpublished* allocation only.
    # Never use this weaker route for a durable, identity-bound attempt.
    if not cgroup.exists():
        return
    kill_file = cgroup / "cgroup.kill"
    if kill_file.exists():
        try:
            kill_file.write_text("1", encoding="ascii")
            return
        except OSError:
            pass
    procs = cgroup / "cgroup.procs"
    try:
        pids = [int(value) for value in procs.read_text(encoding="ascii").split()]
    except (OSError, ValueError):
        return
    for pid in pids:
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        except PermissionError:
            pass

def _exact_cgroup_identity(cgroup: Path) -> tuple[int, int]:
    """Pin the ORIGINAL cgroup directory; absence/substitution is uncertainty.

    This is an attempt-owner identity fence, NOT independent LACE writer-absence
    proof. It never authorizes workspace or Git deletion.
    """
    try:
        if cgroup.is_symlink() or any(parent.is_symlink() for parent in cgroup.parents):
            raise FleetPlanInvalid("Fleet original cgroup contains a symlink")
        metadata = cgroup.stat(follow_symlinks=False)
        if not stat.S_ISDIR(metadata.st_mode):
            raise FleetPlanInvalid("Fleet original cgroup is not a directory")
        return (metadata.st_dev, metadata.st_ino)
    except OSError as exc:
        raise FleetPlanInvalid("Fleet original cgroup identity unavailable") from exc


def _require_original_cgroup(cgroup: Path, identity: tuple[int, int]) -> None:
    if _exact_cgroup_identity(cgroup) != identity:
        raise FleetPlanInvalid("Fleet original cgroup was replaced during physical teardown")


@contextmanager
def _bound_cgroup_directory(cgroup: Path, expected_identity: tuple[int, int]) -> Iterator[int]:
    """Open one native object, never reinterpret path lookup as durable identity.

    This is a local owner fence, NOT a verified LACE writer absence proof.
    """
    try:
        descriptor = os.open(
            cgroup, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
        )
    except OSError as exc:
        raise FleetPlanInvalid("Fleet original cgroup directory handle unavailable") from exc
    try:
        observed = os.fstat(descriptor)
        if not stat.S_ISDIR(observed.st_mode) or (
            observed.st_dev, observed.st_ino
        ) != expected_identity:
            raise FleetPlanInvalid("Fleet original cgroup handle identity replaced")
        _require_original_cgroup(cgroup, expected_identity)
        yield descriptor
    finally:
        os.close(descriptor)


def _read_bound_cgroup_control(descriptor: int, name: str, limit: int) -> str:
    """Bounded kernel-control read via the original directory (no path relookup)."""
    if name not in ("cgroup.events", "cgroup.procs"):
        raise FleetPlanInvalid("Fleet unrecognized cgroup observation control")
    try:
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=descriptor)
    except OSError as exc:
        raise FleetPlanInvalid("Fleet original cgroup kernel observations unavailable") from exc
    try:
        metadata = os.fstat(fd)
        if not stat.S_ISREG(metadata.st_mode):
            raise FleetPlanInvalid("Fleet original cgroup kernel observation is not a control file")
        try:
            payload = os.read(fd, limit + 1)
            if len(payload) > limit:
                raise FleetPlanInvalid("Fleet original cgroup kernel observation exceeds bound")
            return payload.decode("ascii")
        except (OSError, UnicodeError) as exc:
            raise FleetPlanInvalid("Fleet original cgroup kernel observations unavailable") from exc
    finally:
        os.close(fd)


def _wait_for_cgroup_empty(
    cgroup: Path, *, timeout_seconds: float = 1.0,
    expected_identity: tuple[int, int] | None = None,
) -> None:
    """Require both original kernel views from an identity-bound directory FD.

    cgroup.events includes descendants; cgroup.procs describes immediate members.
    Local cgroup quiescence is not complete workspace writer custody.
    """
    if expected_identity is None:
        expected_identity = _exact_cgroup_identity(cgroup)
    deadline = time.monotonic() + timeout_seconds
    while True:
        _require_original_cgroup(cgroup, expected_identity)
        with _bound_cgroup_directory(cgroup, expected_identity) as descriptor:
            events = _read_bound_cgroup_control(descriptor, "cgroup.events", 4096).splitlines()
            processes = _read_bound_cgroup_control(descriptor, "cgroup.procs", 65536).split()
            _require_original_cgroup(cgroup, expected_identity)
        rows = [row for row in events if row.startswith("populated ")]
        if len(rows) != 1 or rows[0] not in ("populated 0", "populated 1"):
            raise FleetPlanInvalid("Fleet original cgroup population observation is ambiguous")
        if any(not pid.isdecimal() or int(pid) < 1 for pid in processes):
            raise FleetPlanInvalid("Fleet original cgroup PID observation is malformed")
        _require_original_cgroup(cgroup, expected_identity)
        if rows[0] == "populated 0" and not processes:
            return
        if rows[0] == "populated 0" and processes:
            raise FleetPlanInvalid("Fleet original cgroup observations disagree")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(0.01, remaining))
    raise FleetPlanInvalid(
        f"Fleet original cgroup remained populated after kill and cannot be safely retired: {cgroup}"
    )


def _require_original_cgroup_parent(
    parent: Path, descriptor: int, expected_identity: tuple[int, int],
) -> None:
    """Reject displaced parent lineage without claiming exclusive mutation custody.

    This is a local identity/ACK fence, not a kernel-enforced owner lease.
    Linux rmdir cannot atomically require that its target name still identifies
    the original child inode, even with a pinned parent descriptor.
    """
    try:
        pinned = os.fstat(descriptor)
        current = os.stat(parent, follow_symlinks=False)
    except OSError as exc:
        raise FleetPlanInvalid("Fleet original cgroup parent lineage unobservable") from exc
    if (not stat.S_ISDIR(pinned.st_mode) or not stat.S_ISDIR(current.st_mode)
            or (pinned.st_dev, pinned.st_ino) != expected_identity
            or (current.st_dev, current.st_ino) != expected_identity):
        raise FleetPlanInvalid("Fleet original cgroup parent lineage replaced")


def _remove_cgroup(
    cgroup: Path, *, expected_identity: tuple[int, int] | None = None,
) -> None:
    """Remove only a locally verified cgroup subtree through pinned parent handles.

    Directory membership is checked before each syscall; Linux rmdir(name, dir_fd)
    is *not* identity-conditional. This is stronger local checking, but an
    adversary able to exchange names exactly inside rmdir remains a blocker
    for independent physical absence and writer-bearing workspace retirement.
    """
    _refuse_unprovisioned_kernel_effect(cgroup, operation="native cgroup removal")
    if expected_identity is None:
        if not cgroup.exists():
            if cgroup.is_symlink():
                raise FleetPlanInvalid("Fleet cgroup cleanup target is a symlink")
            return
        expected_identity = _exact_cgroup_identity(cgroup)
    _require_original_cgroup(cgroup, expected_identity)
    try:
        parent_fd = os.open(
            cgroup.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
        )
    except OSError as exc:
        raise FleetPlanInvalid("Fleet cgroup cleanup parent handle unavailable") from exc
    try:
        try:
            parent_stat = os.fstat(parent_fd)
        except OSError as exc:
            raise FleetPlanInvalid("Fleet original cgroup parent identity unavailable") from exc
        if not stat.S_ISDIR(parent_stat.st_mode):
            raise FleetPlanInvalid("Fleet original cgroup parent is not a directory")
        original_parent = (parent_stat.st_dev, parent_stat.st_ino)
        _require_original_cgroup_parent(cgroup.parent, parent_fd, original_parent)
        with _bound_cgroup_directory(cgroup, expected_identity) as descriptor:
            try:
                children = sorted(os.scandir(descriptor), key=lambda entry: entry.name)
            except OSError as exc:
                raise FleetPlanInvalid("Fleet cgroup native child inventory unavailable") from exc
            for child in children:
                if child.is_symlink():
                    raise FleetPlanInvalid("Fleet cgroup child is a symlink; refusing removal")
                if child.is_dir(follow_symlinks=False):
                    native = os.stat(child.name, dir_fd=descriptor, follow_symlinks=False)
                    if not stat.S_ISDIR(native.st_mode) or native.st_ino != child.inode():
                        raise FleetPlanInvalid("Fleet original cgroup child inventory was replaced")
                    _remove_cgroup(cgroup / child.name, expected_identity=(native.st_dev, native.st_ino))
            # Keep the parent handle fixed across identity verification and the
            # native rmdir. Never claim this proves adversarial atomic deletion.
            native = os.stat(cgroup.name, dir_fd=parent_fd, follow_symlinks=False)
            if not stat.S_ISDIR(native.st_mode) or (
                native.st_dev, native.st_ino
            ) != expected_identity:
                raise FleetPlanInvalid("Fleet original cgroup removal target replaced")
            _require_original_cgroup(cgroup, expected_identity)
            _require_original_cgroup_parent(cgroup.parent, parent_fd, original_parent)
            try:
                os.rmdir(cgroup.name, dir_fd=parent_fd)
            except OSError as exc:
                raise FleetPlanInvalid(f"Fleet could not remove original cgroup {cgroup}: {exc}") from exc
            # The old NAME disappearing does not prove the ORIGINAL inode was
            # removed. An attacker can rename that directory after our last
            # identity check and put an empty foreign directory in its place.
            # rmdir(name, dir_fd=parent_fd) would then remove the foreign one
            # and a pathname-absence check alone would incorrectly ACK success.
            # An opened Linux directory drops its link count to zero when the
            # kernel actually removes THAT object. This is a post-effect
            # *publication fence*, not an identity-conditional rmdir syscall.
            try:
                retired = os.fstat(descriptor)
            except OSError as exc:
                raise FleetPlanInvalid(
                    "Fleet cannot establish original cgroup inode retirement"
                ) from exc
            if (
                not stat.S_ISDIR(retired.st_mode)
                or (retired.st_dev, retired.st_ino) != expected_identity
                or retired.st_nlink != 0
            ):
                raise FleetPlanInvalid(
                    "Fleet original cgroup inode is still linked after native removal"
                )
            # A changed parent path invalidates this ACK even if the original
            # inode was correctly deleted through the pinned parent handle.
            _require_original_cgroup_parent(cgroup.parent, parent_fd, original_parent)
            # Lost-ACK/replacement uncertainty is not a completed teardown.
            try:
                os.stat(cgroup.name, dir_fd=parent_fd, follow_symlinks=False)
            except FileNotFoundError:
                pass
            except OSError as exc:
                raise FleetPlanInvalid("Fleet cannot verify native cgroup removal") from exc
            else:
                raise FleetPlanInvalid("Fleet native cgroup target reappeared after removal")
    finally:
        os.close(parent_fd)

def _process_identity(pid: int) -> str | None:
    try:
        stat_text = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
        start_ticks = stat_text.rsplit(")", 1)[1].split()[19]
        boot_id = Path("/proc/sys/kernel/random/boot_id").read_text(encoding="utf-8").strip()
    except (OSError, IndexError):
        return None
    return f"{boot_id}:{start_ticks}"

@contextmanager
def _resource_lock(path: Path) -> Iterator[None]:
    import fcntl
    import stat

    ensure_private_directory(path.parent)
    flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags, 0o600)
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise FleetPlanInvalid(f"Fleet cgroup allocation lock is not a regular file: {path}")
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)
