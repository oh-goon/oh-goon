from __future__ import annotations

from typing import Literal

from oh_goon.core.models.game_tape import (
    GameTapeAttemptTransition,
    GameTapeRetryContextSummary,
)


def summarize_retry_context(
    attempt_count: int,
    transitions: tuple[GameTapeAttemptTransition, ...],
) -> GameTapeRetryContextSummary:
    """Summarize only physical retry evidence; never reinterpret retry policy."""
    if attempt_count == 0:
        return GameTapeRetryContextSummary(
            classification="NO_ATTEMPT_EVIDENCE",
            retry_count=0,
            linked_retry_count=0,
            physical_context_changed=False,
        )
    if attempt_count == 1:
        return GameTapeRetryContextSummary(
            classification="NOT_RETRIED",
            retry_count=0,
            linked_retry_count=0,
            physical_context_changed=False,
        )
    linked = sum(item.retry_linked for item in transitions)
    changed = any(_physical_context_changed(item) for item in transitions)
    classification: Literal[
        "INCOMPLETE_RETRY_EVIDENCE", "CHANGED_PHYSICAL_CONTEXT", "SAME_PHYSICAL_CONTEXT"
    ]
    if len(transitions) != attempt_count - 1 or linked != len(transitions):
        classification = "INCOMPLETE_RETRY_EVIDENCE"
    elif changed:
        classification = "CHANGED_PHYSICAL_CONTEXT"
    else:
        classification = "SAME_PHYSICAL_CONTEXT"
    return GameTapeRetryContextSummary(
        classification=classification,
        retry_count=max(0, attempt_count - 1),
        linked_retry_count=linked,
        physical_context_changed=changed,
    )


def _physical_context_changed(transition: GameTapeAttemptTransition) -> bool:
    return any(
        (
            not transition.repository_state_continuity,
            transition.argv_changed,
            transition.tool_changed,
            transition.egress_changed,
            transition.isolation_changed,
            transition.resource_policy_changed,
            transition.cache_input_changed,
        )
    )
