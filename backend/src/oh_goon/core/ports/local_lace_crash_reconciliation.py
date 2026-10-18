"""Read-only crash-window evidence; never a native cleanup capability."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class LocalLaceCrashObservation:
    subject_identity: str
    state: str  # PRESENT_MATCHING | PARTIAL_NETWORK | INTENT_ONLY_UNKNOWN | UNRESOLVED
    # No absence or quiescence booleans: the payload cannot attest to them.
    native_absence_proven: bool = False
    teardown_proven: bool = False
