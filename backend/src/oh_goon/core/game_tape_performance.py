from __future__ import annotations

from statistics import median

from oh_goon.core.errors import InvalidRequest
from oh_goon.core.models.game_tape import (
    GameTape,
    GameTapeRepositoryExecution,
    GameTapeStepExecution,
)
from oh_goon.core.models.game_tape_performance import (
    GameTapeDurationBaseline,
    GameTapeDurationComparison,
    GameTapePerformanceReport,
    GameTapeRepositoryPerformance,
    GameTapeStepPerformance,
)
from oh_goon.core.vocabulary import GameTapeStatus


def project_game_tape_performance(
    tape: GameTape,
    prior: tuple[GameTape, ...],
    *,
    sample_limit: int,
) -> GameTapePerformanceReport:
    """Compare one canonical Game Tape with prior tapes from the same admitted plan."""
    if sample_limit < 1 or sample_limit > 20:
        raise InvalidRequest("Game Tape performance sample_limit must be between 1 and 20")
    if tape.plan_id is None:
        raise InvalidRequest("Game Tape performance requires an admitted Fleet Plan")
    comparable = tuple(item for item in prior if item.plan_id == tape.plan_id)
    return GameTapePerformanceReport(
        fleet_run_id=tape.fleet_run_id,
        plan_id=tape.plan_id,
        sample_limit=sample_limit,
        history_candidates_considered=len(prior),
        comparable_plan_runs=len(comparable),
        previous_comparable_run_id=_previous_comparable_run_id(tape, comparable),
        run_timing=_run_comparison(tape, comparable, sample_limit),
        repositories=tuple(
            _repository_performance(repository, comparable, sample_limit)
            for repository in tape.repositories
        ),
    )


def _previous_comparable_run_id(current: GameTape, prior: tuple[GameTape, ...]) -> str | None:
    repository_ids = frozenset(item.repository.repository_id for item in current.repositories)
    for tape in prior:
        candidate_ids = frozenset(item.repository.repository_id for item in tape.repositories)
        if candidate_ids == repository_ids:
            return tape.fleet_run_id
    return None


def _run_comparison(
    current: GameTape,
    prior: tuple[GameTape, ...],
    sample_limit: int,
) -> GameTapeDurationComparison:
    repository_ids = frozenset(item.repository.repository_id for item in current.repositories)
    samples: list[tuple[str, int]] = []
    for tape in prior:
        candidate_ids = frozenset(item.repository.repository_id for item in tape.repositories)
        if candidate_ids != repository_ids or tape.status is not GameTapeStatus.PASSED:
            continue
        if tape.receipt.duration_ms is not None:
            samples.append((tape.fleet_run_id, tape.receipt.duration_ms))
        if len(samples) == sample_limit:
            break
    return _comparison(current.receipt.duration_ms, samples)


def _repository_performance(
    current: GameTapeRepositoryExecution,
    prior: tuple[GameTape, ...],
    sample_limit: int,
) -> GameTapeRepositoryPerformance:
    historical = tuple(_repository(tape, current.repository.repository_id) for tape in prior)
    repository_samples = _successful_repository_samples(historical, sample_limit)
    steps = tuple(_step_performance(step, historical, sample_limit) for step in current.steps)
    slowdown = max(
        (item for item in steps if item.timing.direction == "SLOWER"),
        key=lambda item: item.timing.delta_percent if item.timing.delta_percent is not None else -1,
        default=None,
    )
    return GameTapeRepositoryPerformance(
        repository_execution_id=current.repository_execution_id,
        repository_id=current.repository.repository_id,
        repository_name=current.repository.name,
        timing=_comparison(current.duration_ms, repository_samples),
        largest_slowdown_step_id=slowdown.step_id if slowdown is not None else None,
        largest_slowdown_percent=slowdown.timing.delta_percent if slowdown is not None else None,
        steps=steps,
    )


def _step_performance(
    current: GameTapeStepExecution,
    historical: tuple[tuple[str, GameTapeRepositoryExecution] | None, ...],
    sample_limit: int,
) -> GameTapeStepPerformance:
    samples: list[tuple[str, int]] = []
    for entry in historical:
        if entry is None:
            continue
        run_id, repository = entry
        step = next((item for item in repository.steps if item.step_id == current.step_id), None)
        if step is None or step.status is not GameTapeStatus.PASSED or step.duration_ms is None:
            continue
        samples.append((run_id, step.duration_ms))
        if len(samples) == sample_limit:
            break
    return GameTapeStepPerformance(
        step_execution_id=current.step_execution_id,
        step_id=current.step_id,
        status=current.status,
        timing=_comparison(current.duration_ms, samples),
    )


def _successful_repository_samples(
    historical: tuple[tuple[str, GameTapeRepositoryExecution] | None, ...],
    sample_limit: int,
) -> list[tuple[str, int]]:
    samples: list[tuple[str, int]] = []
    for entry in historical:
        if entry is None:
            continue
        run_id, repository = entry
        if repository.status is GameTapeStatus.PASSED and repository.duration_ms is not None:
            samples.append((run_id, repository.duration_ms))
        if len(samples) == sample_limit:
            break
    return samples


def _repository(
    tape: GameTape,
    repository_id: str,
) -> tuple[str, GameTapeRepositoryExecution] | None:
    repository = next(
        (item for item in tape.repositories if item.repository.repository_id == repository_id),
        None,
    )
    return (tape.fleet_run_id, repository) if repository is not None else None


def _comparison(
    current_duration_ms: int | None,
    samples: list[tuple[str, int]],
) -> GameTapeDurationComparison:
    durations = [duration_ms for _, duration_ms in samples]
    baseline = GameTapeDurationBaseline(
        sample_count=len(samples),
        sample_run_ids=tuple(run_id for run_id, _ in samples),
        median_duration_ms=median(durations) if durations else None,
        minimum_duration_ms=min(durations) if durations else None,
        maximum_duration_ms=max(durations) if durations else None,
    )
    if current_duration_ms is None or baseline.median_duration_ms is None:
        return GameTapeDurationComparison(
            current_duration_ms=current_duration_ms,
            baseline=baseline,
            direction="NO_BASELINE",
        )
    delta = current_duration_ms - baseline.median_duration_ms
    percent = (
        round((delta / baseline.median_duration_ms) * 100)
        if baseline.median_duration_ms > 0
        else None
    )
    direction = "SAME" if delta == 0 else "SLOWER" if delta > 0 else "FASTER"
    return GameTapeDurationComparison(
        current_duration_ms=current_duration_ms,
        baseline=baseline,
        delta_ms=delta,
        delta_percent=percent,
        direction=direction,
    )
