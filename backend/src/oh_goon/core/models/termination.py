from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

TerminationCause = Literal["watchdog", "cancel", "trust_revoke", "operator", "cleanup"]


@dataclass(frozen=True, slots=True)
class TerminationSignal:
    """Unbound request to stop whichever exact physical execution observes it."""

    cause: TerminationCause
    reason: str
    grace_seconds: float = 1.0

    def __post_init__(self) -> None:
        if not self.reason:
            raise ValueError("termination signal requires reason")
        if not 0 <= self.grace_seconds <= 60:
            raise ValueError("termination grace must be between 0 and 60 seconds")
