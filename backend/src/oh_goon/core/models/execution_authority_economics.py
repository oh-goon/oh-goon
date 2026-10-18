from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True, slots=True)
class ExecutionAuthorityEconomicsReceipt:
    """Bounded diagnostic evidence for one authority-revalidation operation.

    This receipt is never admission or execution authority. It only explains how much
    identity work was reused versus recomputed while terminal proof remained intact.
    """

    scope: Literal["runtime-launch", "materialization"]
    candidate_units: int
    reused_units: int
    recomputed_units: int
    reused_bytes: int
    recomputed_bytes: int
    terminal_proof_bytes: int = 0
    reuse_provenance: tuple[str, ...] = ()
    reuse_identities: tuple[str, ...] = ()
    recompute_reasons: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name in (
            "candidate_units",
            "reused_units",
            "recomputed_units",
            "reused_bytes",
            "recomputed_bytes",
            "terminal_proof_bytes",
        ):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} must be non-negative")
        if self.reused_units + self.recomputed_units != self.candidate_units:
            raise ValueError("candidate_units must equal reused_units + recomputed_units")

    @property
    def receipt_identity(self) -> str:
        payload = {
            "schema": "oh-goon.execution-authority-economics/v1",
            "scope": self.scope,
            "candidate_units": self.candidate_units,
            "reused_units": self.reused_units,
            "recomputed_units": self.recomputed_units,
            "reused_bytes": self.reused_bytes,
            "recomputed_bytes": self.recomputed_bytes,
            "terminal_proof_bytes": self.terminal_proof_bytes,
            "reuse_provenance": list(self.reuse_provenance),
            "reuse_identities": list(self.reuse_identities),
            "recompute_reasons": list(self.recompute_reasons),
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        return "sha256:" + hashlib.sha256(encoded).hexdigest()
