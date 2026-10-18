from __future__ import annotations

from collections.abc import Iterable

from oh_goon.core.models.game_tape_runtime_authority import (
    GameTapeRuntimeAuthorityReceipt,
    RuntimeAuthorityEvidence,
)

GAME_TAPE_MAX_RUNTIME_AUTHORITY_EVIDENCE = 256


def project_runtime_authority_evidence(
    evidence: Iterable[RuntimeAuthorityEvidence],
    *,
    limit: int = GAME_TAPE_MAX_RUNTIME_AUTHORITY_EVIDENCE,
) -> GameTapeRuntimeAuthorityReceipt:
    """Project already-authoritative facts into bounded Game Tape evidence.

    The projection deliberately has no callback into execution, scheduling, watchdog,
    termination, recovery, or publication authorities. Newest supplied evidence is
    retained when the diagnostic window is full, while omission remains explicit.
    """
    if not 1 <= limit <= GAME_TAPE_MAX_RUNTIME_AUTHORITY_EVIDENCE:
        raise ValueError("runtime authority evidence limit must be between 1 and 256")
    rows = tuple(evidence)
    observed = len(rows)
    kept = rows[-limit:]
    omitted = observed - len(kept)
    return GameTapeRuntimeAuthorityReceipt(
        evidence=kept,
        observed_count=observed,
        omitted_count=omitted,
        truncated=omitted > 0,
        max_evidence=limit,
    )
