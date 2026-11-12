from __future__ import annotations

import hashlib
import json
import signal
from dataclasses import asdict, dataclass
from typing import Literal

from oh_goon.core.models.runtime_supervision import WatchdogDecision
from oh_goon.core.models.termination import TerminationCause
from oh_goon.core.models.termination import TerminationSignal as TerminationSignal


@dataclass(frozen=True, slots=True)
class TerminationRequest:
    """Generation-bound request to stop one already-owned execution process tree."""

    execution_id: str
    generation: int
    cause: TerminationCause
    reason: str
    grace_seconds: float = 1.0
    signal_number: int = signal.SIGTERM

    def __post_init__(self) -> None:
        if not self.execution_id:
            raise ValueError("termination request requires execution_id")
        if self.generation < 1:
            raise ValueError("termination generation must be positive")
        if not self.reason:
            raise ValueError("termination request requires reason")
        if not 0 <= self.grace_seconds <= 60:
            raise ValueError("termination grace must be between 0 and 60 seconds")
        if not isinstance(self.signal_number, int) or self.signal_number not in (
            signal.SIGTERM,
            signal.SIGINT,
        ):
            raise ValueError("termination signal must be SIGTERM or SIGINT")

    @property
    def request_identity(self) -> str:
        payload = asdict(self)
        if self.signal_number == signal.SIGTERM:
            # Keep existing TERM authorities byte-for-byte compatible with v1.
            payload.pop("signal_number")
        raw = json.dumps(
            {"schema": "oh-goon.termination-request/v1", **payload},
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        return "sha256:" + hashlib.sha256(raw).hexdigest()


@dataclass(frozen=True, slots=True)
class ProcessTreeTerminationReceipt:
    """Bounded cleanup evidence; term_requested means graceful termination was requested."""

    request_identity: str
    term_requested: bool
    grace_seconds: float
    escalated_to_kill: bool
    quiescent: bool
    final_state: Literal["quiescent", "survivors-detected"]

    def __post_init__(self) -> None:
        if self.quiescent != (self.final_state == "quiescent"):
            raise ValueError("termination receipt final state disagrees with quiescence")

    @property
    def receipt_identity(self) -> str:
        raw = json.dumps(
            {"schema": "oh-goon.process-tree-termination-receipt/v1", **asdict(self)},
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        return "sha256:" + hashlib.sha256(raw).hexdigest()


def termination_request_from_watchdog(
    decision: WatchdogDecision, *, execution_id: str, generation: int, grace_seconds: float = 1.0
) -> TerminationRequest | None:
    """Translate a watchdog decision into termination intent; never kill here."""
    if not decision.termination_required:
        return None
    return TerminationRequest(
        execution_id=execution_id,
        generation=generation,
        cause="watchdog",
        reason=decision.reason,
        grace_seconds=grace_seconds,
    )
