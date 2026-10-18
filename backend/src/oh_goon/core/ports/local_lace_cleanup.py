"""Data-only local LACE cleanup transports (no native effect authority)."""
from __future__ import annotations

from dataclasses import dataclass

@dataclass(frozen=True, slots=True)
class LocalLaceCleanupTicket:
    run_id: str
    intent_run_seq: int
    claim_run_seq: int
    inventory_run_seq: int
    prepare_run_seq: int
    control_epoch: int
    subject_identity: str
    inventory_identity: str
    ticket_identity: str
    # No executable/native operation is carried by this transport.


@dataclass(frozen=True, slots=True)
class NativeCleanupResult:
    # Authoritative only about which owner operations were attempted, not absence.
    attempted: tuple[str, ...]
    result: str  # CLEANUP_ACTIONS_ATTEMPTED | NATIVE_FAILED



class NativeCleanupFailure(Exception):
    """Bounded effect-attempt labels, never the native stderr or workload data."""

    def __init__(self, attempted: tuple[str, ...]):
        if (type(attempted) is not tuple or len(attempted) > 2
                or any(x not in {"NETWORK", "CGROUP"} for x in attempted)
                or len(set(attempted)) != len(attempted)):
            raise ValueError("invalid native cleanup effect labels")
        self.attempted = attempted
        super().__init__("native cleanup operation failed")
