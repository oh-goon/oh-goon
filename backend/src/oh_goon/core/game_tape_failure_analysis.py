from __future__ import annotations

from dataclasses import dataclass

from oh_goon.core.errors import InvalidRequest
from oh_goon.core.game_tape_failure_fact import (
    GameTapeComparableStepFact,
    GameTapeComparableTransitionFact,
    compare_adjacent_step_facts,
)
from oh_goon.core.game_tape_reproduction_context import compare_reproduction_context
from oh_goon.core.models.game_tape import (
    GameTape,
    GameTapeRepositoryExecution,
    GameTapeStepExecution,
)
from oh_goon.core.models.game_tape_failure_relation import GameTapeFailureSignatureRelation
from oh_goon.core.vocabulary import GameTapeStatus


@dataclass(frozen=True, slots=True)
class GameTapeFailureSample:
    """One bounded chronological slice and its already-derived adjacent transitions."""

    prior_count: int
    later_count: int
    facts: tuple[GameTapeComparableStepFact, ...]
    transitions: tuple[GameTapeComparableTransitionFact, ...]


@dataclass(frozen=True, slots=True)
class GameTapeFailureAnalysis:
    """Canonical one-pass analysis of the Journal window around one selected failed step."""

    plan_id: str
    repository_id: str
    step_id: str
    signature_id: str
    current: GameTapeComparableStepFact
    prior: tuple[GameTapeComparableStepFact, ...]
    later: tuple[GameTapeComparableStepFact, ...]
    chronological: tuple[GameTapeComparableStepFact, ...]
    sample: GameTapeFailureSample


def analyze_failure_history(
    tape: GameTape,
    prior: tuple[GameTape, ...],
    later: tuple[GameTape, ...],
    *,
    repository_id: str,
    step_id: str,
    sample_limit: int,
) -> GameTapeFailureAnalysis:
    """Select, normalize and interpret one already-projected Journal window exactly once."""
    if tape.plan_id is None:
        raise InvalidRequest("Game Tape failure history requires an admitted Fleet Plan")
    repository = _require_repository(tape, repository_id)
    step = _require_step(repository, step_id)
    if step.failure is None:
        raise InvalidRequest("Game Tape failure history requires a failed step with a signature")
    signature_id = step.failure.signature_id
    current_fact = _fact(tape, repository, step, signature_id, repository, step)
    prior_facts = _analyze_entries(
        prior, tape.plan_id, repository_id, step_id, signature_id, repository, step
    )
    later_facts = _analyze_entries(
        later, tape.plan_id, repository_id, step_id, signature_id, repository, step
    )
    chronological = (*tuple(reversed(prior_facts)), current_fact, *later_facts)
    sample = _sample(prior_facts, current_fact, later_facts, sample_limit)
    return GameTapeFailureAnalysis(
        plan_id=tape.plan_id,
        repository_id=repository_id,
        step_id=step_id,
        signature_id=signature_id,
        current=current_fact,
        prior=prior_facts,
        later=later_facts,
        chronological=chronological,
        sample=sample,
    )


def _sample(
    prior: tuple[GameTapeComparableStepFact, ...],
    current: GameTapeComparableStepFact,
    later: tuple[GameTapeComparableStepFact, ...],
    sample_limit: int,
) -> GameTapeFailureSample:
    prior_sample = tuple(reversed(prior[:sample_limit]))
    later_sample = later[:sample_limit]
    facts = (*prior_sample, current, *later_sample)
    return GameTapeFailureSample(
        prior_count=len(prior_sample),
        later_count=len(later_sample),
        facts=facts,
        transitions=tuple(
            compare_adjacent_step_facts(previous, current_item)
            for previous, current_item in zip(facts, facts[1:], strict=False)
        ),
    )


def exact_entries(
    entries: tuple[GameTapeComparableStepFact, ...],
) -> tuple[GameTapeComparableStepFact, ...]:
    return tuple(item for item in entries if item.signature_relation == "EXACT_SIGNATURE")


def passed_entries(
    entries: tuple[GameTapeComparableStepFact, ...],
) -> tuple[GameTapeComparableStepFact, ...]:
    return tuple(item for item in entries if item.status is GameTapeStatus.PASSED)


def _analyze_entries(
    details: tuple[GameTape, ...],
    plan_id: str,
    repository_id: str,
    step_id: str,
    signature_id: str,
    current_repository: GameTapeRepositoryExecution,
    current_step: GameTapeStepExecution,
) -> tuple[GameTapeComparableStepFact, ...]:
    facts: list[GameTapeComparableStepFact] = []
    for tape in details:
        if tape.plan_id != plan_id:
            continue
        repository = _optional_repository(tape, repository_id)
        step = _optional_step(repository, step_id)
        if repository is None or step is None:
            continue
        facts.append(
            _fact(
                tape,
                repository,
                step,
                signature_id,
                current_repository,
                current_step,
            )
        )
    return tuple(facts)


def _fact(
    tape: GameTape,
    repository: GameTapeRepositoryExecution,
    step: GameTapeStepExecution,
    signature_id: str,
    selected_repository: GameTapeRepositoryExecution,
    selected_step: GameTapeStepExecution,
) -> GameTapeComparableStepFact:
    return GameTapeComparableStepFact(
        run_id=tape.fleet_run_id,
        accepted_at=tape.receipt.accepted_at,
        repository_id=repository.repository.repository_id,
        repository_name=repository.repository.name,
        step_id=step.step_id,
        status=step.status,
        duration_ms=step.duration_ms,
        attempt_count=step.attempt_count,
        failure=step.failure,
        signature_relation=_signature_relation(step, signature_id),
        selected_context=compare_reproduction_context(
            selected_repository, selected_step, repository, step
        ),
        source_repository=repository,
        source_step=step,
    )


def _signature_relation(
    step: GameTapeStepExecution, signature_id: str
) -> GameTapeFailureSignatureRelation:
    if step.failure is None:
        return "NO_SIGNATURE"
    return "EXACT_SIGNATURE" if step.failure.signature_id == signature_id else "DIFFERENT_SIGNATURE"


def _require_repository(tape: GameTape, repository_id: str) -> GameTapeRepositoryExecution:
    repository = _optional_repository(tape, repository_id)
    if repository is None:
        raise InvalidRequest(f"Game Tape repository is not part of this run: {repository_id}")
    return repository


def _require_step(repository: GameTapeRepositoryExecution, step_id: str) -> GameTapeStepExecution:
    step = _optional_step(repository, step_id)
    if step is None:
        raise InvalidRequest(f"Game Tape step is not part of this repository execution: {step_id}")
    return step


def _optional_repository(tape: GameTape, repository_id: str) -> GameTapeRepositoryExecution | None:
    return next(
        (item for item in tape.repositories if item.repository.repository_id == repository_id),
        None,
    )


def _optional_step(
    repository: GameTapeRepositoryExecution | None, step_id: str
) -> GameTapeStepExecution | None:
    if repository is None:
        return None
    return next((item for item in repository.steps if item.step_id == step_id), None)
