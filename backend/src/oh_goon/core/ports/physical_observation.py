"""B12E-C1 read-only observation data. NOT an execution or Enforcer authority.

Scope identity is a caller-supplied observation target, not a signed Fleet
authorization. Original authority binding belongs to the existing LACE owner
and is deliberately outside this contract until B12E-C2.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Literal
import re

from oh_goon.core.enforcer_lace_trust import canonical_bytes, digest


ObservationState = Literal["PRESENT", "NOT_OBSERVED", "UNKNOWN"]

# The complete component roster emitted by the ONE shared Linux observer.
# P3 local convergence may assess a recorded round only if this whole roster
# is present; a single nondetection is never full physical coverage.
# This remains a *diagnostic* shape, NOT an absence or signer authority.
ORIGINAL_KERNEL_COMPONENTS = (
    "cgroup", "subtree", "process", "mounts", "network",
    "tracked_mount_namespace", "tracked_network_namespace",
    "visible_namespace_fd_holders", "visible_foreign_mount_targets",
    "visible_mount_fd_holders",
)

# Known holes in signed *declared* scope. Immutable until independent kernel
# inventory and physical observer custody are implemented in original LACE.
UNVERIFIED_NATIVE_CLASSES = (
    "bubblewrap_runtime_mounts", "host_tool_actual_mounts", "detached_mounts",
    "foreign_mount_fds", "all_namespace_holders", "network_kernel_obligations",
    "all_descendants", "independent_observer_custody",
)


@dataclass(frozen=True, slots=True)
class PhysicalObservationScope:
    run_id: str
    subject_identity: str
    cgroup_path: str
    cgroup_device: int
    cgroup_inode: int
    tracked_pid: int
    tracked_birth: str
    mount_targets: tuple[str, ...]
    network_namespace: str | None
    host_veth: str | None
    nft_table: str | None


@dataclass(frozen=True, slots=True)
class PhysicalKernelFact:
    state: ObservationState
    detail: str

    def __post_init__(self) -> None:
        if (type(self.state) is not str or self.state not in
                ("PRESENT", "NOT_OBSERVED", "UNKNOWN")
                or type(self.detail) is not str or len(self.detail) > 4096
                or any(ord(ch) < 32 or ord(ch) == 127 for ch in self.detail)):
            raise ValueError("invalid bounded physical kernel fact")


@dataclass(frozen=True, slots=True)
class PhysicalKernelSnapshot:
    """Immutable diagnostic kernel facts; never absence/teardown proof.

    NOT_OBSERVED is only scoped non-detection. UNKNOWN denotes incomplete
    observation, not physical absence. No gate or effect permission exists.
    """
    components: tuple[tuple[str, PhysicalKernelFact], ...]

    def __post_init__(self) -> None:
        if (type(self.components) is not tuple or not 1 <= len(self.components) <= 32
                or any(type(entry) is not tuple or len(entry) != 2
                       or type(entry[0]) is not str or not entry[0]
                       or len(entry[0]) > 64 or not entry[0].isascii()
                       or not entry[0].replace("_", "").isalnum()
                       or type(entry[1]) is not PhysicalKernelFact
                       for entry in self.components)
                or len({entry[0] for entry in self.components}) != len(self.components)):
            raise ValueError("ambiguous, unbounded or invalid physical snapshot")

    @property
    def identity(self) -> str:
        """One canonical identity for shared facts; never evidence authority."""
        return digest("oh-goon/b12e/shared-kernel-snapshot/v1", canonical_bytes({"components": [
            {"component": name, "state": fact.state, "detail": fact.detail}
            for name, fact in self.components
        ]}))

    def to_mapping(self) -> dict[str, dict[str, str]]:
        return {name: {"state": fact.state, "detail": fact.detail}
                for name, fact in self.components}


@dataclass(frozen=True, slots=True)
class OriginalRunPhysicalEnvelope:
    """Identity of *known* original Run resources, not a physical witness.

    Built only from journal-verified RESOURCE_INTENT, allocation and inventory
    under the original Fleet recovery claim. These identities never imply that
    runtime/detached mounts, foreign namespace holders or independent witness
    custody have been enumerated. No effect or publication methods exist.
    """
    run_id: str
    intent_run_seq: int
    claim_run_seq: int
    control_epoch: int
    subject_identity: str
    signed_intent_identity: str
    allocation_identity: str
    inventory_identity: str
    native_record_identity: str
    mount_intent_identity: str
    declared_mount_targets: tuple[str, ...]
    # Exact native journal inventory, not independently witnessed descendants.
    native_cgroup_device: int = 0
    native_cgroup_inode: int = 0
    observed_processes: tuple[tuple[int, str, str, str], ...] = ()
    observer_custody: str = "UNVERIFIED"
    coverage: str = "INCOMPLETE_NATIVE_RESOURCE_SCOPE"
    effect_permission: str = "NONE"
    unverified_resource_classes: tuple[str, ...] = UNVERIFIED_NATIVE_CLASSES

    def __post_init__(self) -> None:
        identities = (self.subject_identity, self.signed_intent_identity,
                      self.allocation_identity, self.inventory_identity,
                      self.native_record_identity, self.mount_intent_identity)
        if (type(self.run_id) is not str or not self.run_id
                or type(self.intent_run_seq) is not int or self.intent_run_seq < 2
                or type(self.claim_run_seq) is not int or self.claim_run_seq <= self.intent_run_seq
                or type(self.control_epoch) is not int or self.control_epoch < 1
                or any(type(x) is not str or len(x) != 71 or not x.startswith("sha256:")
                       for x in identities)
                or type(self.declared_mount_targets) is not tuple
                or not self.declared_mount_targets or len(self.declared_mount_targets) > 64
                or len(set(self.declared_mount_targets)) != len(self.declared_mount_targets)
                or any(type(x) is not str or not x.startswith("/")
                       for x in self.declared_mount_targets)
                or type(self.native_cgroup_device) is not int or self.native_cgroup_device < 1
                or type(self.native_cgroup_inode) is not int or self.native_cgroup_inode < 1
                or type(self.observed_processes) is not tuple or not self.observed_processes
                or len(self.observed_processes) > 4096
                or any(type(row) is not tuple or len(row) != 4
                       or type(row[0]) is not int or row[0] < 1
                       or type(row[1]) is not str or not re.fullmatch(r"[^\s:]+:[1-9][0-9]*", row[1])
                       or type(row[2]) is not str or not re.fullmatch(r"mnt:\[[1-9][0-9]*\]", row[2])
                       or type(row[3]) is not str or not re.fullmatch(r"sha256:[0-9a-f]{64}", row[3])
                       for row in self.observed_processes)
                or tuple(sorted(self.observed_processes, key=lambda row: row[0])) != self.observed_processes
                or len({row[0] for row in self.observed_processes}) != len(self.observed_processes)
                or len({row[1].rsplit(":", 1)[0] for row in self.observed_processes}) != 1
                or self.observer_custody != "UNVERIFIED"
                or self.coverage != "INCOMPLETE_NATIVE_RESOURCE_SCOPE"
                or self.effect_permission != "NONE"
                or self.unverified_resource_classes != UNVERIFIED_NATIVE_CLASSES):
            raise ValueError("original physical resource envelope cannot grant authority")

    @property
    def identity(self) -> str:
        return digest("oh-goon/b12e/original-resource-envelope/v2", canonical_bytes({
            "run_id": self.run_id, "intent_run_seq": self.intent_run_seq,
            "claim_run_seq": self.claim_run_seq, "control_epoch": self.control_epoch,
            "subject_identity": self.subject_identity,
            "signed_intent_identity": self.signed_intent_identity,
            "allocation_identity": self.allocation_identity,
            "inventory_identity": self.inventory_identity,
            "native_record_identity": self.native_record_identity,
            "mount_intent_identity": self.mount_intent_identity,
            "declared_mount_targets": list(self.declared_mount_targets),
            "native_cgroup_device": self.native_cgroup_device,
            "native_cgroup_inode": self.native_cgroup_inode,
            "observed_processes": [list(row) for row in self.observed_processes],
            "observer_custody": self.observer_custody, "coverage": self.coverage,
            "effect_permission": self.effect_permission,
            "unverified_resource_classes": list(self.unverified_resource_classes),
        }))


@dataclass(frozen=True, slots=True)
class OriginalRecoveryPhysicalSample:
    """Owner-bound, *unqualified* Linux facts, not authenticated absence proof.

    The exact scope must match the original claim/allocation/inventory. Native
    mount-target provenance and independent observer custody are not yet in the
    original journal, so no signer, classifier or effects capability is exposed.
    """
    run_id: str
    intent_run_seq: int
    claim_run_seq: int
    control_epoch: int
    subject_identity: str
    scope: PhysicalObservationScope
    facts: PhysicalKernelSnapshot
    envelope: OriginalRunPhysicalEnvelope
    mount_intent_identity: str = ""
    classification: str = "ORIGINAL_OWNER_READ_ONLY_UNQUALIFIED"
    effect_permission: str = "NONE"
    physical_absence_proven: bool = False
    quiescence_proven: bool = False
    teardown_proven: bool = False

    @property
    def kernel_snapshot_identity(self) -> str:
        """Identity only; original signed fact publication remains unavailable."""
        return self.facts.identity

    @property
    def causal_observation_identity(self) -> str:
        """Run/claim/scope-bound *candidate* identity, never a signed physical fact.

        A raw kernel snapshot digest is not Run-specific and could be replayed
        across attempts. This binds the original journal-verified envelope,
        immutable sampling scope and exact shared reader result together.
        It confers no observer custody, absence or effect permission.
        """
        return digest("oh-goon/b27/original-physical-candidate/v1", canonical_bytes({
            "envelope_identity": self.envelope.identity,
            "scope": {**asdict(self.scope), "mount_targets": list(self.scope.mount_targets)},
            "kernel_snapshot_identity": self.facts.identity,
            "mount_intent_identity": self.mount_intent_identity,
            "classification": self.classification,
            "effect_permission": self.effect_permission,
        }))

    def __post_init__(self) -> None:
        if (self.classification != "ORIGINAL_OWNER_READ_ONLY_UNQUALIFIED"
                or self.effect_permission != "NONE"
                or any((self.physical_absence_proven, self.quiescence_proven, self.teardown_proven))
                or type(self.mount_intent_identity) is not str
                or not self.mount_intent_identity.startswith('sha256:')
                or len(self.mount_intent_identity) != 71
                or type(self.facts) is not PhysicalKernelSnapshot
                or type(self.scope) is not PhysicalObservationScope
                or type(self.envelope) is not OriginalRunPhysicalEnvelope
                or (self.run_id, self.intent_run_seq, self.claim_run_seq,
                    self.control_epoch, self.subject_identity, self.mount_intent_identity)
                != (self.envelope.run_id, self.envelope.intent_run_seq,
                    self.envelope.claim_run_seq, self.envelope.control_epoch,
                    self.envelope.subject_identity, self.envelope.mount_intent_identity)
                or self.scope.mount_targets != self.envelope.declared_mount_targets
                or (self.scope.cgroup_device, self.scope.cgroup_inode) !=
                    (self.envelope.native_cgroup_device, self.envelope.native_cgroup_inode)
                or not any(row[0] == self.scope.tracked_pid and row[1] == self.scope.tracked_birth
                           for row in self.envelope.observed_processes)):

            raise ValueError("original recovery sampling cannot mint physical authority")
