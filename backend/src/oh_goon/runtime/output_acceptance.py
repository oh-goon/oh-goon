from __future__ import annotations

from oh_goon.core.models.output_acceptance import (
    ExecutionGeneration,
    OutputAcceptance,
    SideEffectAttempt,
    SideEffectDisposition,
    SideEffectReceipt,
)


class OutputAcceptanceAuthority:
    """Generation-bound, replay-safe authority for externally visible outputs.

    Physical work may have been attempted before this boundary. Only an acceptance
    produced here grants authority for the result to be treated as published.
    """

    def __init__(self, active: ExecutionGeneration) -> None:
        self._active = active
        self._accepted: dict[str, OutputAcceptance] = {}
        self._effects: dict[str, SideEffectReceipt] = {}

    def accept_output(self, candidate: OutputAcceptance) -> OutputAcceptance:
        self._require_active(candidate.execution)
        prior = self._accepted.get(candidate.output_name)
        if prior is None:
            self._accepted[candidate.output_name] = candidate
            return candidate
        if prior.acceptance_id == candidate.acceptance_id:
            return prior
        raise ValueError("output name already accepted with different immutable content")

    def record_effect(
        self,
        attempt: SideEffectAttempt,
        *,
        accepted_output: OutputAcceptance | None = None,
        reason: str,
    ) -> SideEffectReceipt:
        self._require_active(attempt.execution)
        prior = self._effects.get(attempt.attempt_id)
        if accepted_output is None:
            candidate = SideEffectReceipt(
                attempt=attempt,
                disposition=SideEffectDisposition.ATTEMPTED,
                reason=reason,
            )
        else:
            self._require_active(accepted_output.execution)
            accepted = self._accepted.get(accepted_output.output_name)
            if accepted is None or accepted.acceptance_id != accepted_output.acceptance_id:
                raise ValueError("side effect cannot be accepted before durable output acceptance")
            candidate = SideEffectReceipt(
                attempt=attempt,
                disposition=SideEffectDisposition.ACCEPTED,
                reason=reason,
                accepted_output_id=accepted.acceptance_id,
            )
        if prior is None:
            self._effects[attempt.attempt_id] = candidate
            return candidate
        if prior == candidate:
            return prior
        raise ValueError("side-effect replay conflicts with the previously recorded disposition")

    def reject_effect(self, attempt: SideEffectAttempt, *, reason: str) -> SideEffectReceipt:
        self._require_active(attempt.execution)
        candidate = SideEffectReceipt(
            attempt=attempt,
            disposition=SideEffectDisposition.REJECTED,
            reason=reason,
        )
        prior = self._effects.get(attempt.attempt_id)
        if prior is None:
            self._effects[attempt.attempt_id] = candidate
            return candidate
        if prior == candidate:
            return prior
        raise ValueError("side-effect replay conflicts with the previously recorded disposition")

    def _require_active(self, candidate: ExecutionGeneration) -> None:
        if candidate.identity != self._active.identity:
            raise ValueError("output or side effect belongs to a stale or foreign execution generation")
