from __future__ import annotations

from oh_goon.core.game_tape_failure_analysis import GameTapeFailureAnalysis
from oh_goon.core.game_tape_failure_fact import GameTapeComparableStepFact
from oh_goon.core.game_tape_failure_transition import (
    project_selected_context_window,
    project_sequence_transitions,
    summarize_sequence_transitions,
)
from oh_goon.core.models.game_tape_failure_sequence import (
    GameTapeFailureSequence,
    GameTapeFailureSequenceEntry,
    GameTapeFailureSequencePosition,
    GameTapeFailureSequenceSummary,
)
from oh_goon.core.vocabulary import GameTapeStatus


def project_failure_sequence(
    analysis: GameTapeFailureAnalysis,
    *,
    sample_limit: int,
) -> GameTapeFailureSequence:
    """Project one bounded slice of the canonical chronological failure analysis."""
    sample = analysis.sample
    positions = (
        *("PRIOR" for _ in range(sample.prior_count)),
        "SELECTED",
        *("LATER" for _ in range(sample.later_count)),
    )
    transitions = project_sequence_transitions(sample.transitions)
    return GameTapeFailureSequence(
        sample_limit=sample_limit,
        summary=_summary(
            analysis,
            sampled_prior=sample.prior_count,
            sampled_later=sample.later_count,
        ),
        selected_context_window=project_selected_context_window(analysis.prior, analysis.later),
        transition_summary=summarize_sequence_transitions(transitions),
        entries=tuple(
            _entry(fact, position) for fact, position in zip(sample.facts, positions, strict=True)
        ),
        transitions=transitions,
    )


def _entry(
    fact: GameTapeComparableStepFact,
    position: GameTapeFailureSequencePosition,
) -> GameTapeFailureSequenceEntry:
    return GameTapeFailureSequenceEntry(
        fleet_run_id=fact.run_id,
        accepted_at=fact.accepted_at,
        position=position,
        status=fact.status,
        duration_ms=fact.duration_ms,
        signature_relation=fact.signature_relation,
        context=fact.selected_context,
    )


def _summary(
    analysis: GameTapeFailureAnalysis,
    *,
    sampled_prior: int,
    sampled_later: int,
) -> GameTapeFailureSequenceSummary:
    same_context_passes = _same_context_later_passes(analysis.later)
    latest = analysis.later[-1] if analysis.later else analysis.current
    return GameTapeFailureSequenceSummary(
        prior_comparable_runs=len(analysis.prior),
        later_comparable_runs=len(analysis.later),
        sampled_prior_runs=sampled_prior,
        sampled_later_runs=sampled_later,
        complete_context_entries=sum(
            item.selected_context.summary.known_dimensions == 5 for item in analysis.chronological
        ),
        incomplete_context_entries=sum(
            item.selected_context.summary.known_dimensions < 5 for item in analysis.chronological
        ),
        later_same_context_passes=len(same_context_passes),
        later_same_context_exact_failures=_same_context_exact_failures(analysis.later),
        first_later_same_context_pass_run_id=(
            same_context_passes[0].run_id if same_context_passes else None
        ),
        exact_failure_recurred_in_same_context_after_same_context_pass=(
            _same_context_recurrence_after_pass(analysis.later, same_context_passes)
        ),
        latest_comparable_run_id=latest.run_id,
        latest_comparable_status=latest.status,
        latest_signature_relation=latest.signature_relation,
        latest_context_classification=latest.selected_context.summary.classification,
    )


def _same_context_later_passes(
    later: tuple[GameTapeComparableStepFact, ...],
) -> tuple[GameTapeComparableStepFact, ...]:
    return tuple(
        item for item in later if item.status is GameTapeStatus.PASSED and _same_context(item)
    )


def _same_context_exact_failures(
    later: tuple[GameTapeComparableStepFact, ...],
) -> int:
    return sum(
        item.signature_relation == "EXACT_SIGNATURE" and _same_context(item) for item in later
    )


def _same_context_recurrence_after_pass(
    later: tuple[GameTapeComparableStepFact, ...],
    passes: tuple[GameTapeComparableStepFact, ...],
) -> bool:
    if not passes:
        return False
    first_pass_id = passes[0].run_id
    pass_seen = False
    for item in later:
        if item.run_id == first_pass_id:
            pass_seen = True
            continue
        if pass_seen and item.signature_relation == "EXACT_SIGNATURE" and _same_context(item):
            return True
    return False


def _same_context(fact: GameTapeComparableStepFact) -> bool:
    return fact.selected_context.summary.classification == "SAME_CONTEXT"
