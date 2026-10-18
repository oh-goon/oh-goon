from __future__ import annotations

from oh_goon.core.errors import InvalidRequest
from oh_goon.core.game_tape_failure_analysis import (
    GameTapeFailureAnalysis,
    analyze_failure_history,
    exact_entries,
)
from oh_goon.core.game_tape_failure_fact import GameTapeComparableStepFact
from oh_goon.core.game_tape_failure_sequence import project_failure_sequence
from oh_goon.core.models.game_tape import GameTape
from oh_goon.core.models.game_tape_failure_history import (
    GameTapeFailureEvolution,
    GameTapeFailureHistory,
    GameTapeFailureOccurrence,
    GameTapeLaterPass,
)
from oh_goon.core.models.game_tape_reproduction_context import GameTapeFailureContextCounts
from oh_goon.core.vocabulary import GameTapeStatus


def project_game_tape_failure_history(
    current: GameTape,
    prior: tuple[GameTape, ...],
    later: tuple[GameTape, ...] = (),
    *,
    repository_id: str,
    step_id: str,
    sample_limit: int,
) -> GameTapeFailureHistory:
    """Project exact failure history from one canonical analysis of the Journal window."""
    if sample_limit < 1 or sample_limit > 20:
        raise InvalidRequest("Game Tape failure history sample_limit must be between 1 and 20")
    analysis = analyze_failure_history(
        current,
        prior,
        later,
        repository_id=repository_id,
        step_id=step_id,
        sample_limit=sample_limit,
    )
    occurrences, same_plan_step_failures = _prior_occurrences(analysis, sample_limit)
    return GameTapeFailureHistory(
        fleet_run_id=analysis.current.run_id,
        plan_id=analysis.plan_id,
        repository_id=analysis.repository_id,
        step_id=analysis.step_id,
        signature_id=analysis.signature_id,
        sample_limit=sample_limit,
        history_candidates_considered=len(prior),
        same_plan_step_failures=same_plan_step_failures,
        evolution=_evolution(analysis),
        sequence=project_failure_sequence(analysis, sample_limit=sample_limit),
        occurrences=occurrences,
    )


def _prior_occurrences(
    analysis: GameTapeFailureAnalysis,
    sample_limit: int,
) -> tuple[tuple[GameTapeFailureOccurrence, ...], int]:
    failures = tuple(item for item in analysis.prior if item.failure is not None)
    exact = tuple(item for item in failures if item.signature_relation == "EXACT_SIGNATURE")
    return tuple(_occurrence(item) for item in exact[:sample_limit]), len(failures)


def _evolution(analysis: GameTapeFailureAnalysis) -> GameTapeFailureEvolution:
    prior_exact = tuple(reversed(exact_entries(analysis.prior)))
    later_exact = exact_entries(analysis.later)
    all_exact = (*prior_exact, analysis.current, *later_exact)
    first_later_pass = _first_later_pass(analysis.later)
    return GameTapeFailureEvolution(
        exact_occurrence_count=len(all_exact),
        prior_exact_occurrences=len(prior_exact),
        later_exact_occurrences=len(later_exact),
        first_seen_run_id=all_exact[0].run_id,
        first_seen_at=all_exact[0].accepted_at,
        last_seen_run_id=all_exact[-1].run_id,
        last_seen_at=all_exact[-1].accepted_at,
        later_comparable_runs=len(analysis.later),
        first_later_pass=first_later_pass,
        exact_failure_recurred_after_pass=_recurred_after_pass(analysis.later, first_later_pass),
        prior_exact_contexts=_context_counts(prior_exact),
        later_exact_contexts=_context_counts(later_exact),
        later_pass_contexts=_context_counts(
            tuple(item for item in analysis.later if item.status is GameTapeStatus.PASSED)
        ),
    )


def _first_later_pass(
    entries: tuple[GameTapeComparableStepFact, ...],
) -> GameTapeLaterPass | None:
    return next(
        (_later_pass(item) for item in entries if item.status is GameTapeStatus.PASSED),
        None,
    )


def _recurred_after_pass(
    entries: tuple[GameTapeComparableStepFact, ...],
    first_later_pass: GameTapeLaterPass | None,
) -> bool:
    if first_later_pass is None:
        return False
    pass_seen = False
    for item in entries:
        if item.run_id == first_later_pass.fleet_run_id:
            pass_seen = True
            continue
        if pass_seen and item.signature_relation == "EXACT_SIGNATURE":
            return True
    return False


def _occurrence(fact: GameTapeComparableStepFact) -> GameTapeFailureOccurrence:
    failure = fact.failure
    assert failure is not None
    return GameTapeFailureOccurrence(
        fleet_run_id=fact.run_id,
        accepted_at=fact.accepted_at,
        repository_id=fact.repository_id,
        repository_name=fact.repository_name,
        step_id=fact.step_id,
        duration_ms=fact.duration_ms,
        attempt_count=fact.attempt_count,
        failure_attempt=failure.attempt,
        exit_code=failure.exit_code,
        reason_code=failure.reason_code,
        signature_id=failure.signature_id,
        evidence_stream=failure.evidence_stream,
        evidence_sha256=failure.evidence_sha256,
        context=fact.selected_context,
    )


def _later_pass(fact: GameTapeComparableStepFact) -> GameTapeLaterPass:
    return GameTapeLaterPass(
        fleet_run_id=fact.run_id,
        accepted_at=fact.accepted_at,
        duration_ms=fact.duration_ms,
        context=fact.selected_context,
    )


def _context_counts(
    entries: tuple[GameTapeComparableStepFact, ...],
) -> GameTapeFailureContextCounts:
    counts = {"SAME_CONTEXT": 0, "CHANGED_CONTEXT": 0, "INCOMPLETE_CONTEXT": 0}
    for item in entries:
        counts[item.selected_context.summary.classification] += 1
    return GameTapeFailureContextCounts(
        same_context=counts["SAME_CONTEXT"],
        changed_context=counts["CHANGED_CONTEXT"],
        incomplete_context=counts["INCOMPLETE_CONTEXT"],
    )
