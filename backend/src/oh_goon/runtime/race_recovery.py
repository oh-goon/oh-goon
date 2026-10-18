from __future__ import annotations

from oh_goon.core.models.race_recovery import TerminalClaim


class TerminalRaceAuthority:
    """Single-winner authority for terminal races within one execution generation.

    Persistence owners may store the winning claim transactionally. This object defines
    the fail-closed comparison semantics: an exact replay is idempotent; every distinct
    later claim loses and cannot rewrite the terminal outcome.
    """

    def __init__(self) -> None:
        self._winner: TerminalClaim | None = None

    @property
    def winner(self) -> TerminalClaim | None:
        return self._winner

    def claim(self, candidate: TerminalClaim) -> TerminalClaim:
        winner = self._winner
        if winner is None:
            self._winner = candidate
            return candidate
        if candidate.execution_id != winner.execution_id or candidate.generation != winner.generation:
            raise ValueError("terminal claim belongs to another execution generation")
        if candidate.claim_id == winner.claim_id:
            return winner
        return winner
