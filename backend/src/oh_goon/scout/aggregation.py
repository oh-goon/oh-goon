from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import timedelta
from statistics import median

from oh_goon.core.execution_observability import RepositoryRunObservation
from oh_goon.core.vocabulary import GameTapeStatus
from oh_goon.scout.evidence import (
    ScoutEvidenceSnapshot,
    ScoutStepObservation,
    comparison_environment,
)
from oh_goon.scout.models import (
    ScoutCoverage,
    ScoutGoonVersionSummary,
    ScoutPlanSummary,
    ScoutRankingRow,
    ScoutResourceTotals,
    ScoutRunSample,
    ScoutStepSummary,
    ScoutTrendPoint,
    ScoutWindow,
)
from oh_goon.scout.policy import (
    SCOUT_MEMORY_COMPARISON_MIN_COVERAGE_PERCENT,
    SCOUT_PEER_MIN_EXECUTIONS,
    SCOUT_PEER_MIN_REPOSITORIES,
)

_UNSUCCESSFUL_STATUSES = frozenset(
    {
        GameTapeStatus.ATTENTION_REQUIRED,
        GameTapeStatus.FAILED,
        GameTapeStatus.CANCELLED,
        GameTapeStatus.INTERRUPTED,
    }
)
_CANCELLED_STATUSES = frozenset({GameTapeStatus.CANCELLED, GameTapeStatus.INTERRUPTED})


@dataclass(frozen=True, slots=True)
class RepositoryRunResource:
    run: RepositoryRunObservation
    attempt_count: int
    eligible_attempts: int
    cpu_observed_attempts: int
    memory_observed_attempts: int
    observed_cpu_seconds: float
    complete_cpu_seconds: float | None
    observed_peak_memory_bytes: int | None
    complete_peak_memory_bytes: int | None


@dataclass(frozen=True, slots=True)
class StepPeerComparison:
    cpu_median: float | None
    memory_median: float | None
    execution_count: int
    repository_count: int
    cpu_coverage: ScoutCoverage
    memory_coverage: ScoutCoverage
    duration_median: float | None


StepPeerKey = tuple[str, str, str, tuple[object, ...]]
StepPeerIndex = dict[StepPeerKey, tuple[ScoutStepObservation, ...]]


def build_step_peer_index(steps: tuple[ScoutStepObservation, ...]) -> StepPeerIndex:
    successful = (item for item in steps if item.repository_status is GameTapeStatus.PASSED)
    return group_steps(successful, key=_step_peer_key)


def runs_in_window(
    runs: tuple[RepositoryRunObservation, ...], window: ScoutWindow
) -> tuple[RepositoryRunObservation, ...]:
    return tuple(run for run in runs if window.start_at <= run.started_at < window.end_at)


def steps_in_window(
    steps: tuple[ScoutStepObservation, ...], window: ScoutWindow
) -> tuple[ScoutStepObservation, ...]:
    return tuple(step for step in steps if window.start_at <= step.started_at < window.end_at)


def coverage(observed: int, eligible: int) -> ScoutCoverage:
    return ScoutCoverage(
        observed=observed,
        eligible=eligible,
        percent=(observed / eligible * 100) if eligible else 100.0,
    )


def resource_totals(steps: tuple[ScoutStepObservation, ...]) -> ScoutResourceTotals:
    eligible = sum(step.eligible_attempts for step in steps)
    cpu_observed = sum(step.cpu_observed_attempts for step in steps)
    memory_observed = sum(step.memory_observed_attempts for step in steps)
    peaks = [
        step.observed_peak_memory_bytes
        for step in steps
        if step.observed_peak_memory_bytes is not None
    ]
    return ScoutResourceTotals(
        total_cpu_seconds=_observed_cpu_total(steps),
        cpu_coverage=coverage(cpu_observed, eligible),
        peak_memory_bytes=max(peaks) if peaks else None,
        memory_coverage=coverage(memory_observed, eligible),
    )


def repository_run_resources(
    runs: tuple[RepositoryRunObservation, ...], steps: tuple[ScoutStepObservation, ...]
) -> tuple[RepositoryRunResource, ...]:
    steps_by_run_repo = group_steps(steps, key=lambda item: (item.run_id, item.repository_id))
    result: list[RepositoryRunResource] = []
    for run in runs:
        run_steps = steps_by_run_repo.get((run.run_id, run.repository_id), ())
        eligible = sum(step.eligible_attempts for step in run_steps)
        cpu_observed = sum(step.cpu_observed_attempts for step in run_steps)
        memory_observed = sum(step.memory_observed_attempts for step in run_steps)
        observed_cpu = sum(step.observed_cpu_seconds for step in run_steps)
        peaks = [
            step.observed_peak_memory_bytes
            for step in run_steps
            if step.observed_peak_memory_bytes is not None
        ]
        complete_cpu = observed_cpu if eligible > 0 and cpu_observed == eligible else None
        complete_peak = (
            max(peaks) if eligible > 0 and memory_observed == eligible and peaks else None
        )
        result.append(
            RepositoryRunResource(
                run=run,
                attempt_count=sum(step.attempt_count for step in run_steps),
                eligible_attempts=eligible,
                cpu_observed_attempts=cpu_observed,
                memory_observed_attempts=memory_observed,
                observed_cpu_seconds=observed_cpu,
                complete_cpu_seconds=complete_cpu,
                observed_peak_memory_bytes=max(peaks) if peaks else None,
                complete_peak_memory_bytes=complete_peak,
            )
        )
    return tuple(result)


def repository_rows(
    runs: tuple[RepositoryRunObservation, ...], steps: tuple[ScoutStepObservation, ...]
) -> tuple[ScoutRankingRow, ...]:
    run_resources = repository_run_resources(runs, steps)
    grouped: dict[str, list[RepositoryRunResource]] = defaultdict(list)
    for item in run_resources:
        grouped[item.run.repository_id].append(item)
    result: list[ScoutRankingRow] = []
    for repository_id, items in grouped.items():
        successful = [item for item in items if item.run.repository_status is GameTapeStatus.PASSED]
        complete_cpu = [
            item.complete_cpu_seconds
            for item in successful
            if item.complete_cpu_seconds is not None
        ]
        durations = [
            item.run.duration_seconds
            for item in successful
            if item.run.duration_seconds is not None
        ]
        average_cpu = [
            value
            for item in successful
            if (value := _average_cpu_cores(item.complete_cpu_seconds, item.run.duration_seconds))
            is not None
        ]
        peaks = [
            item.observed_peak_memory_bytes
            for item in items
            if item.observed_peak_memory_bytes is not None
        ]
        representative = max(
            items, key=lambda item: (item.observed_cpu_seconds, item.run.started_at)
        )
        latest = max(items, key=lambda item: (item.run.started_at, item.run.run_id))
        result.append(
            ScoutRankingRow(
                entity_kind="REPOSITORY",
                key=repository_id,
                display_name=latest.run.repository_name,
                repository_id=repository_id,
                run_count=len(items),
                repository_count=1,
                attempt_count=sum(item.attempt_count for item in items),
                total_cpu_seconds=_observed_cpu_total_from_run_resources(items),
                median_cpu_seconds_per_run=median(complete_cpu) if complete_cpu else None,
                median_average_cpu_cores=median(average_cpu) if average_cpu else None,
                peak_memory_bytes=max(peaks) if peaks else None,
                failed_cpu_seconds=sum(
                    item.observed_cpu_seconds
                    for item in items
                    if item.run.repository_status in _UNSUCCESSFUL_STATUSES
                    and item.run.repository_status not in _CANCELLED_STATUSES
                ),
                cancelled_cpu_seconds=sum(
                    item.observed_cpu_seconds
                    for item in items
                    if item.run.repository_status in _CANCELLED_STATUSES
                ),
                cpu_coverage=coverage(
                    sum(item.cpu_observed_attempts for item in items),
                    sum(item.eligible_attempts for item in items),
                ),
                memory_coverage=coverage(
                    sum(item.memory_observed_attempts for item in items),
                    sum(item.eligible_attempts for item in items),
                ),
                median_duration_seconds_per_run=median(durations) if durations else None,
                representative_run_id=representative.run.run_id,
            )
        )
    return tuple(result)


def plan_summaries(
    runs: tuple[RepositoryRunObservation, ...], steps: tuple[ScoutStepObservation, ...]
) -> tuple[ScoutPlanSummary, ...]:
    resources = repository_run_resources(runs, steps)
    grouped: dict[str, list[RepositoryRunResource]] = defaultdict(list)
    for item in resources:
        if item.run.plan_id is not None:
            grouped[item.run.plan_id].append(item)
    result: list[ScoutPlanSummary] = []
    for plan_id, items in grouped.items():
        latest = max(items, key=lambda item: (item.run.started_at, item.run.run_id))
        plan_name = latest.run.plan_name or plan_id
        successful = [item for item in items if item.run.repository_status is GameTapeStatus.PASSED]
        complete_cpu = [
            item.complete_cpu_seconds
            for item in successful
            if item.complete_cpu_seconds is not None
        ]
        durations = [
            item.run.duration_seconds
            for item in successful
            if item.run.duration_seconds is not None
        ]
        average_cpu = [
            value
            for item in successful
            if (value := _average_cpu_cores(item.complete_cpu_seconds, item.run.duration_seconds))
            is not None
        ]
        peaks = [
            item.observed_peak_memory_bytes
            for item in items
            if item.observed_peak_memory_bytes is not None
        ]
        representative = max(
            items, key=lambda item: (item.observed_cpu_seconds, item.run.started_at)
        )
        result.append(
            ScoutPlanSummary(
                plan_id=plan_id,
                plan_name=plan_name,
                run_count=len(items),
                attempt_count=sum(item.attempt_count for item in items),
                total_cpu_seconds=_observed_cpu_total_from_run_resources(items),
                median_cpu_seconds_per_run=median(complete_cpu) if complete_cpu else None,
                median_average_cpu_cores=median(average_cpu) if average_cpu else None,
                peak_memory_bytes=max(peaks) if peaks else None,
                cpu_coverage=coverage(
                    sum(item.cpu_observed_attempts for item in items),
                    sum(item.eligible_attempts for item in items),
                ),
                memory_coverage=coverage(
                    sum(item.memory_observed_attempts for item in items),
                    sum(item.eligible_attempts for item in items),
                ),
                median_duration_seconds_per_run=median(durations) if durations else None,
                representative_run_id=representative.run.run_id,
            )
        )
    return tuple(
        sorted(
            result,
            key=lambda item: (
                item.total_cpu_seconds if item.total_cpu_seconds is not None else -1.0,
                item.run_count,
                item.plan_name,
            ),
            reverse=True,
        )
    )


def goon_rows(steps: tuple[ScoutStepObservation, ...]) -> tuple[ScoutRankingRow, ...]:
    grouped = group_steps(steps, key=lambda item: (item.goon_name, item.goon_source_sha256))
    result: list[ScoutRankingRow] = []
    for (name, source_sha), items in grouped.items():
        run_groups = group_steps(items, key=lambda item: (item.run_id, item.repository_id))
        complete_run_cpu: list[float] = []
        for run_items in run_groups.values():
            eligible = sum(item.eligible_attempts for item in run_items)
            observed = sum(item.cpu_observed_attempts for item in run_items)
            if (
                all(item.repository_status is GameTapeStatus.PASSED for item in run_items)
                and eligible > 0
                and observed == eligible
            ):
                complete_run_cpu.append(sum(item.observed_cpu_seconds for item in run_items))
        peaks = [
            item.observed_peak_memory_bytes
            for item in items
            if item.observed_peak_memory_bytes is not None
        ]
        representative = max(items, key=lambda item: (item.observed_cpu_seconds, item.started_at))
        result.append(
            ScoutRankingRow(
                entity_kind="GOON",
                key=f"{name}@{source_sha}",
                display_name=name,
                goon_name=name,
                goon_source_sha256=source_sha,
                run_count=len(run_groups),
                repository_count=len({item.repository_id for item in items}),
                attempt_count=sum(item.attempt_count for item in items),
                total_cpu_seconds=_observed_cpu_total(items),
                median_cpu_seconds_per_run=median(complete_run_cpu) if complete_run_cpu else None,
                peak_memory_bytes=max(peaks) if peaks else None,
                failed_cpu_seconds=sum(
                    item.observed_cpu_seconds
                    for item in items
                    if item.repository_status in _UNSUCCESSFUL_STATUSES
                    and item.repository_status not in _CANCELLED_STATUSES
                ),
                cancelled_cpu_seconds=sum(
                    item.observed_cpu_seconds
                    for item in items
                    if item.repository_status in _CANCELLED_STATUSES
                ),
                cpu_coverage=coverage(
                    sum(item.cpu_observed_attempts for item in items),
                    sum(item.eligible_attempts for item in items),
                ),
                memory_coverage=coverage(
                    sum(item.memory_observed_attempts for item in items),
                    sum(item.eligible_attempts for item in items),
                ),
                representative_run_id=representative.run_id,
            )
        )
    return tuple(result)


def goon_versions(steps: tuple[ScoutStepObservation, ...]) -> tuple[ScoutGoonVersionSummary, ...]:
    grouped = group_steps(steps, key=lambda item: item.goon_source_sha256)
    result: list[ScoutGoonVersionSummary] = []
    for source_sha, items in grouped.items():
        run_groups = group_steps(items, key=lambda item: (item.run_id, item.repository_id))
        run_cpu: list[float] = []
        for group in run_groups.values():
            eligible = sum(item.eligible_attempts for item in group)
            observed = sum(item.cpu_observed_attempts for item in group)
            if (
                all(item.repository_status is GameTapeStatus.PASSED for item in group)
                and eligible > 0
                and eligible == observed
            ):
                run_cpu.append(sum(item.observed_cpu_seconds for item in group))
        result.append(
            ScoutGoonVersionSummary(
                goon_name=items[0].goon_name,
                source_sha256=source_sha,
                first_seen_at=min(item.started_at for item in items),
                last_seen_at=max(item.started_at for item in items),
                repository_count=len({item.repository_id for item in items}),
                run_count=len(run_groups),
                total_cpu_seconds=_observed_cpu_total(items),
                median_cpu_seconds_per_run=median(run_cpu) if run_cpu else None,
                cpu_coverage=coverage(
                    sum(item.cpu_observed_attempts for item in items),
                    sum(item.eligible_attempts for item in items),
                ),
            )
        )
    return tuple(sorted(result, key=lambda item: item.last_seen_at, reverse=True))


def step_summary(
    snapshot: ScoutEvidenceSnapshot,
    window: ScoutWindow,
    items: tuple[ScoutStepObservation, ...],
    *,
    repository_id: str | None,
    peer_index: StepPeerIndex | None = None,
) -> ScoutStepSummary:
    first = items[0]
    successful = tuple(item for item in items if item.repository_status is GameTapeStatus.PASSED)
    complete_cpu = [
        item.complete_cpu_seconds for item in successful if item.complete_cpu_seconds is not None
    ]
    complete_memory = [
        item.complete_peak_memory_bytes
        for item in successful
        if item.complete_peak_memory_bytes is not None
    ]
    observed_cpu = median(complete_cpu) if complete_cpu else None
    observed_memory = median(complete_memory) if complete_memory else None
    average_cpu = [
        value
        for item in successful
        if (value := _average_cpu_cores(item.complete_cpu_seconds, item.duration_seconds))
        is not None
    ]
    durations = [item.duration_seconds for item in successful if item.duration_seconds is not None]
    observed_duration = median(durations) if durations else None
    peers = _step_peer_comparison(snapshot, window, first, repository_id, peer_index)
    representative = max(items, key=lambda item: (item.observed_cpu_seconds, item.started_at))
    return ScoutStepSummary(
        step_id=first.step_id,
        goon_name=first.goon_name,
        goon_source_sha256=first.goon_source_sha256,
        run_count=len({(item.run_id, item.repository_id) for item in items}),
        attempt_count=sum(item.attempt_count for item in items),
        total_cpu_seconds=_observed_cpu_total(items),
        median_cpu_seconds_per_run=observed_cpu,
        median_average_cpu_cores=median(average_cpu) if average_cpu else None,
        peer_median_cpu_seconds_per_run=peers.cpu_median,
        cpu_ratio_to_peer_median=ratio(observed_cpu, peers.cpu_median),
        peak_memory_bytes=max(
            (
                item.observed_peak_memory_bytes
                for item in items
                if item.observed_peak_memory_bytes is not None
            ),
            default=None,
        ),
        peer_median_peak_memory_bytes=peers.memory_median,
        memory_ratio_to_peer_median=ratio(observed_memory, peers.memory_median),
        peer_execution_count=peers.execution_count,
        peer_repository_count=peers.repository_count,
        peer_cpu_coverage=peers.cpu_coverage if repository_id is not None else None,
        peer_memory_coverage=peers.memory_coverage if repository_id is not None else None,
        comparison_environment=(
            comparison_environment(first) if repository_id is not None else None
        ),
        cpu_coverage=coverage(
            sum(item.cpu_observed_attempts for item in items),
            sum(item.eligible_attempts for item in items),
        ),
        memory_coverage=coverage(
            sum(item.memory_observed_attempts for item in items),
            sum(item.eligible_attempts for item in items),
        ),
        median_duration_seconds_per_run=observed_duration,
        peer_median_duration_seconds_per_run=peers.duration_median,
        duration_ratio_to_peer_median=ratio(observed_duration, peers.duration_median),
        representative_run_id=representative.run_id,
    )


def _step_peer_comparison(
    snapshot: ScoutEvidenceSnapshot,
    window: ScoutWindow,
    first: ScoutStepObservation,
    repository_id: str | None,
    peer_index: StepPeerIndex | None,
) -> StepPeerComparison:
    if repository_id is None:
        empty = coverage(0, 0)
        return StepPeerComparison(None, None, 0, 0, empty, empty, None)
    candidates = (
        peer_index.get(_step_peer_key(first), ())
        if peer_index is not None
        else steps_in_window(snapshot.steps, window)
    )
    cohort = tuple(item for item in candidates if _is_step_peer(item, first, repository_id))
    eligible = sum(item.eligible_attempts for item in cohort)
    cpu_coverage = coverage(sum(item.cpu_observed_attempts for item in cohort), eligible)
    memory_coverage = coverage(sum(item.memory_observed_attempts for item in cohort), eligible)
    by_repo = group_steps(cohort, key=lambda item: item.repository_id)
    cpu_values = _peer_repository_medians(by_repo, lambda item: item.complete_cpu_seconds)
    memory_values = _peer_repository_medians(by_repo, lambda item: item.complete_peak_memory_bytes)
    duration_values = _peer_repository_medians(by_repo, lambda item: item.duration_seconds)
    cpu_execution_count = sum(item.complete_cpu_seconds is not None for item in cohort)
    cpu_repository_count = sum(
        any(item.complete_cpu_seconds is not None for item in group) for group in by_repo.values()
    )
    cpu_ready = (
        cpu_execution_count >= SCOUT_PEER_MIN_EXECUTIONS
        and cpu_repository_count >= SCOUT_PEER_MIN_REPOSITORIES
    )
    cpu_median = median(cpu_values) if cpu_ready else None
    memory_ready = (
        _peer_sample_floor(cohort, by_repo, "memory")
        and memory_coverage.percent >= SCOUT_MEMORY_COMPARISON_MIN_COVERAGE_PERCENT
    )
    return StepPeerComparison(
        cpu_median=cpu_median,
        memory_median=median(memory_values) if memory_ready else None,
        execution_count=cpu_execution_count,
        repository_count=cpu_repository_count,
        cpu_coverage=cpu_coverage,
        memory_coverage=memory_coverage,
        duration_median=(
            median(duration_values) if _peer_sample_floor(cohort, by_repo, "duration") else None
        ),
    )


def _step_peer_key(item: ScoutStepObservation) -> StepPeerKey:
    return (item.goon_name, item.goon_source_sha256, item.step_id, item.environment_key)


def _is_step_peer(
    item: ScoutStepObservation, first: ScoutStepObservation, repository_id: str
) -> bool:
    return (
        item.goon_name == first.goon_name
        and item.goon_source_sha256 == first.goon_source_sha256
        and item.step_id == first.step_id
        and item.environment_key == first.environment_key
        and item.repository_status is GameTapeStatus.PASSED
        and item.repository_id != repository_id
    )


def _peer_repository_medians(
    by_repo: dict[object, tuple[ScoutStepObservation, ...]],
    value: Callable[[ScoutStepObservation], float | int | None],
) -> list[float]:
    result: list[float] = []
    for group in by_repo.values():
        values = [float(measurement) for item in group if (measurement := value(item)) is not None]
        if values:
            result.append(median(values))
    return result


def _peer_sample_floor(
    cohort: tuple[ScoutStepObservation, ...],
    by_repo: dict[object, tuple[ScoutStepObservation, ...]],
    metric: str,
) -> bool:
    if metric == "cpu":

        def value(item: ScoutStepObservation) -> float | int | None:
            return item.complete_cpu_seconds
    elif metric == "memory":

        def value(item: ScoutStepObservation) -> float | int | None:
            return item.complete_peak_memory_bytes
    else:

        def value(item: ScoutStepObservation) -> float | int | None:
            return item.duration_seconds

    sample_count = sum(value(item) is not None for item in cohort)
    repository_count = sum(
        any(value(item) is not None for item in group) for group in by_repo.values()
    )
    return (
        sample_count >= SCOUT_PEER_MIN_EXECUTIONS
        and repository_count >= SCOUT_PEER_MIN_REPOSITORIES
    )


def repository_run_samples(
    runs: tuple[RepositoryRunObservation, ...], steps: tuple[ScoutStepObservation, ...]
) -> list[ScoutRunSample]:
    resources = repository_run_resources(runs, steps)
    samples = [
        ScoutRunSample(
            run_id=item.run.run_id,
            started_at=item.run.started_at,
            status=item.run.repository_status,
            cpu_seconds=item.complete_cpu_seconds,
            peak_memory_bytes=item.complete_peak_memory_bytes,
            duration_seconds=item.run.duration_seconds,
            average_cpu_cores=_average_cpu_cores(
                item.complete_cpu_seconds, item.run.duration_seconds
            ),
        )
        for item in resources
    ]
    return sorted(
        samples,
        key=lambda item: (item.cpu_seconds is not None, item.cpu_seconds or 0.0, item.started_at),
        reverse=True,
    )


def _average_cpu_cores(cpu_seconds: float | None, duration_seconds: float | None) -> float | None:
    if cpu_seconds is None or duration_seconds is None or duration_seconds <= 0:
        return None
    return cpu_seconds / duration_seconds


def trend(
    runs: tuple[RepositoryRunObservation, ...],
    steps: tuple[ScoutStepObservation, ...],
    window: ScoutWindow,
) -> tuple[ScoutTrendPoint, ...]:
    if window.hours <= 24:
        bucket = timedelta(hours=1)
    elif window.hours <= 24 * 14:
        bucket = timedelta(days=1)
    else:
        bucket = timedelta(days=7)
    points: list[ScoutTrendPoint] = []
    start = window.start_at
    while start < window.end_at:
        end = min(start + bucket, window.end_at)
        bucket_runs = tuple(run for run in runs if start <= run.started_at < end)
        bucket_steps = tuple(step for step in steps if start <= step.started_at < end)
        eligible = sum(step.eligible_attempts for step in bucket_steps)
        cpu_observed = sum(step.cpu_observed_attempts for step in bucket_steps)
        memory_observed = sum(step.memory_observed_attempts for step in bucket_steps)
        peaks = [
            step.observed_peak_memory_bytes
            for step in bucket_steps
            if step.observed_peak_memory_bytes is not None
        ]
        points.append(
            ScoutTrendPoint(
                start_at=start,
                end_at=end,
                run_count=len({(run.run_id, run.repository_id) for run in bucket_runs}),
                total_cpu_seconds=_observed_cpu_total(bucket_steps),
                peak_memory_bytes=max(peaks) if peaks else None,
                cpu_coverage=coverage(cpu_observed, eligible),
                memory_coverage=coverage(memory_observed, eligible),
            )
        )
        start = end
    return tuple(points)


def group_steps(items: Iterable[ScoutStepObservation], *, key):
    grouped: dict[object, list[ScoutStepObservation]] = defaultdict(list)
    for item in items:
        grouped[key(item)].append(item)
    return {group_key: tuple(group) for group_key, group in grouped.items()}


def cpu_rank_key(item: ScoutRankingRow) -> tuple[float, int, str]:
    return (
        item.total_cpu_seconds if item.total_cpu_seconds is not None else -1.0,
        item.run_count,
        item.display_name,
    )


def _observed_cpu_total(steps: tuple[ScoutStepObservation, ...]) -> float | None:
    eligible = sum(step.eligible_attempts for step in steps)
    observed = sum(step.cpu_observed_attempts for step in steps)
    if eligible > 0 and observed == 0:
        return None
    return sum(step.observed_cpu_seconds for step in steps)


def _observed_cpu_total_from_run_resources(items: list[RepositoryRunResource]) -> float | None:
    eligible = sum(item.eligible_attempts for item in items)
    observed = sum(item.cpu_observed_attempts for item in items)
    if eligible > 0 and observed == 0:
        return None
    return sum(item.observed_cpu_seconds for item in items)


def ratio(observed: float | None, baseline: float | None) -> float | None:
    if observed is None or baseline is None or baseline <= 0:
        return None
    return observed / baseline
