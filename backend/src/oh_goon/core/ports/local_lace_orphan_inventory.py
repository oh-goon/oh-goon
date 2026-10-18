"""Read-only original-Journal local LACE orphan inventory transport.

This is NOT a cleanup capability, an absence witness or an isolation receipt.
The original Fleet session revalidates the subject before appending observations.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class LocalLaceOrphanSubject:
    run_id: str
    intent_run_seq: int
    claim_run_seq: int
    control_epoch: int
    step_id: str
    attempt_identity: str
    resources: dict[str, str]
    observation: dict[str, Any] | None
    subject_identity: str


@dataclass(frozen=True, slots=True)
class LocalLaceOrphanInventory:
    subject_identity: str
    state: str  # OBSERVED_PRESENT or INTENT_ONLY_UNKNOWN (never ABSENT)
    cgroup_identity: str | None
    cgroup_device: int | None
    cgroup_inode: int | None
    cgroup_processes: tuple[tuple[int, str, str, str], ...]
    network_identity: str | None
    native_record_identity: str | None
    # Deliberately false pending independent B12E native quiescence proof.
    process_tree_proven: bool = False
    mounts_proven: bool = False
    native_absence_proven: bool = False
    teardown_proven: bool = False
