"""Transport-only post-allocation, pre-launch local-LACE observation.

A witness from the local executor, not a certificate of isolation or teardown.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from oh_goon.core.ports.local_lace_intent import LocalLaceIntentProof


@dataclass(frozen=True, slots=True)
class LocalLaceAllocationObservation:
    resource_intent_identity: str
    cgroup_path: str
    cgroup_identity: str
    network_kind: str
    network_namespace: str | None
    host_veth: str | None
    nft_table: str | None
    network_identity: str


class LocalLaceAllocationRecorder(Protocol):
    def observe(self, proof: LocalLaceIntentProof,
                observation: LocalLaceAllocationObservation) -> None: ...
