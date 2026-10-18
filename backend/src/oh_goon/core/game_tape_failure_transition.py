from __future__ import annotations

from oh_goon.core.game_tape_failure_fact import (
    GameTapeComparableStepFact,
    GameTapeComparableTransitionFact,
)
from oh_goon.core.models.game_tape_failure_transition import (
    GameTapeFailureSequenceTransition,
    GameTapeFailureTransitionSummary,
    GameTapeSelectedContextWindow,
)


def project_sequence_transitions(
    facts: tuple[GameTapeComparableTransitionFact, ...],
) -> tuple[GameTapeFailureSequenceTransition, ...]:
    """Expose already-interpreted adjacent transition facts without comparing runs again."""
    return tuple(_transition(item) for item in facts)


def summarize_sequence_transitions(
    transitions: tuple[GameTapeFailureSequenceTransition, ...],
) -> GameTapeFailureTransitionSummary:
    changed = tuple(item for item in transitions if item.status_changed)
    return GameTapeFailureTransitionSummary(
        sampled_transition_count=len(transitions),
        status_change_count=len(changed),
        same_context_status_changes=_count_context(changed, "SAME_CONTEXT"),
        changed_context_status_changes=_count_context(changed, "CHANGED_CONTEXT"),
        incomplete_context_status_changes=_count_context(changed, "INCOMPLETE_CONTEXT"),
        signature_relation_change_count=sum(
            item.signature_relation_changed for item in transitions
        ),
    )


def project_selected_context_window(
    prior: tuple[GameTapeComparableStepFact, ...],
    later: tuple[GameTapeComparableStepFact, ...],
) -> GameTapeSelectedContextWindow:
    """Find nearest contiguous selected-context matches without re-comparing the same facts."""
    prior_count, prior_boundary = _walk_nearest(prior)
    later_count, later_boundary = _walk_nearest(later)
    return GameTapeSelectedContextWindow(
        prior_same_context_runs=prior_count,
        later_same_context_runs=later_count,
        total_same_context_runs_including_selected=1 + prior_count + later_count,
        nearest_prior_boundary_run_id=prior_boundary.run_id if prior_boundary else None,
        nearest_prior_boundary_classification=(
            prior_boundary.selected_context.summary.classification if prior_boundary else None
        ),
        nearest_later_boundary_run_id=later_boundary.run_id if later_boundary else None,
        nearest_later_boundary_classification=(
            later_boundary.selected_context.summary.classification if later_boundary else None
        ),
    )


def _transition(fact: GameTapeComparableTransitionFact) -> GameTapeFailureSequenceTransition:
    return GameTapeFailureSequenceTransition(
        from_run_id=fact.from_run_id,
        to_run_id=fact.to_run_id,
        from_status=fact.from_status,
        to_status=fact.to_status,
        status_changed=fact.status_changed,
        from_signature_relation=fact.from_signature_relation,
        to_signature_relation=fact.to_signature_relation,
        signature_relation_changed=fact.signature_relation_changed,
        duration_delta_ms=fact.duration_delta_ms,
        context=fact.context,
    )


def _walk_nearest(
    entries: tuple[GameTapeComparableStepFact, ...],
) -> tuple[int, GameTapeComparableStepFact | None]:
    count = 0
    for entry in entries:
        if entry.selected_context.summary.classification != "SAME_CONTEXT":
            return count, entry
        count += 1
    return count, None


def _count_context(
    transitions: tuple[GameTapeFailureSequenceTransition, ...],
    classification: str,
) -> int:
    return sum(item.context.summary.classification == classification for item in transitions)
