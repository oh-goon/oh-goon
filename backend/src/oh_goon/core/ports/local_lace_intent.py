"""Transport-only local physical intent port; no journal/issuer dependencies."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class LocalLaceIntentProof:
    """Acknowledgment of a committed intent, not physical containment evidence."""
    run_id: str
    step_id: str
    command_ordinal: int
    run_seq: int
    attempt_identity: str
    # Original journal-verified RESOURCE_INTENT.resources.mounts identity.
    # An acknowledgment alone is not physical mount / cleanup proof.
    mounts_identity: str | None = None


class LocalLaceIntentReservation(Protocol):
    def reserve(self, request: object, backend: object) -> LocalLaceIntentProof: ...
    def require_effect_authority(self, proof: LocalLaceIntentProof) -> None: ...
