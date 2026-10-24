from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from statistics import median
from typing import Literal

from oh_goon.core.execution_observability import RepositoryRunObservation
from oh_goon.core.vocabulary import GameTapeStatus
from oh_goon.scout.aggregation import (
    coverage,
    cpu_rank_key,
    group_steps,
    repository_run_resources,
    runs_in_window,
)
from oh_goon.scout.aggregation import (
    ratio as comparison_ratio,
)
from oh_goon.scout.evidence import (
    ScoutEvidenceSnapshot,
    ScoutStepObservation,
    comparison_environment,
)
from oh_goon.scout.models import (
    ScoutFinding,
    ScoutFindingCategory,
    ScoutFindingCount,
    ScoutRankingRow,
    ScoutWindow,
)
from oh_goon.scout.policy import (
    SCOUT_GOON_REGRESSION_RATIO,
    SCOUT_MEMORY_COMPARISON_MIN_COVERAGE_PERCENT,
    SCOUT_OUTLIER_RATIO,
    SCOUT_PEER_MIN_EXECUTIONS,
    SCOUT_PEER_MIN_REPOSITORIES,
    SCOUT_RUN_RETENTION_DAYS,
    SCOUT_RUN_SPIKE_HISTORY_PERIODS,
    SCOUT_RUN_SPIKE_RATIO,
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


def all_findings(
    snapshot: ScoutEvidenceSnapshot,
    window: ScoutWindow,
    current_runs: tuple[RepositoryRunObservation, ...],
    current_steps: tuple[ScoutStepObservation, ...],
    repository_rows: tuple[ScoutRankingRow, ...],
    *,
    category: ScoutFindingCategory | None = None,
    ranked_limit: int | None = 20,
) -> tuple[ScoutFinding, ...]:
    findings = list(_ranking_findings(repository_rows, category=category, limit=ranked_limit))

    def include(value: ScoutFindingCategory) -> bool:
        return category is None or category == value

    if include("EXPENSIVE_RUN"):
        findings.extend(_expensive_run_findings(current_runs, current_steps, limit=ranked_limit))
    if include("FAILURE_WASTE"):
        findings.extend(
            _failure_waste_findings(
                repository_rows, current_runs, current_steps, limit=ranked_limit
            )
        )
    if include("CPU_OUTLIER"):
        findings.extend(_peer_outlier_findings(current_steps, metric="cpu"))
    if include("MEMORY_OUTLIER"):
        findings.extend(_peer_outlier_findings(current_steps, metric="memory"))
    if include("RUN_SPIKE"):
        findings.extend(_run_spike_findings(snapshot.runs, window))
    if include("GOON_REGRESSION"):
        findings.extend(_goon_regression_findings(current_steps))
    return _sort_findings(findings)


def _ranking_findings(
    repository_rows: tuple[ScoutRankingRow, ...],
    *,
    category: ScoutFindingCategory | None,
    limit: int | None,
) -> tuple[ScoutFinding, ...]:
    result: list[ScoutFinding] = []
    if category is None or category == "HEAVY_HITTER":
        for row in sorted(repository_rows, key=cpu_rank_key, reverse=True)[:limit]:
            if row.total_cpu_seconds is None or row.total_cpu_seconds <= 0:
                continue
            result.append(
                ScoutFinding(
                    category="HEAVY_HITTER",
                    entity_kind="REPOSITORY",
                    repository_id=row.repository_id,
                    repository_name=row.display_name,
                    metric="total_cpu",
                    unit="CPU-s",
                    observed=row.total_cpu_seconds,
                    sample_count=row.run_count,
                    coverage=row.cpu_coverage,
                    explanation="Highest observed total CPU consumption in the selected window; this is a resource ranking, not an efficiency judgment.",
                    representative_run_id=row.representative_run_id,
                )
            )
    if category is None or category == "MOST_ACTIVE":
        result.extend(_most_active_findings(repository_rows, limit=limit))
    return tuple(result)


def _most_active_findings(
    repository_rows: tuple[ScoutRankingRow, ...], *, limit: int | None
) -> tuple[ScoutFinding, ...]:
    result: list[ScoutFinding] = []
    rows = sorted(
        repository_rows, key=lambda item: (item.run_count, item.display_name), reverse=True
    )[:limit]
    for row in rows:
        if row.run_count <= 0:
            continue
        result.append(
            ScoutFinding(
                category="MOST_ACTIVE",
                entity_kind="REPOSITORY",
                repository_id=row.repository_id,
                repository_name=row.display_name,
                metric="run_count",
                unit="runs",
                observed=float(row.run_count),
                sample_count=row.run_count,
                explanation="Highest repository execution frequency in the selected window; high frequency does not imply inefficiency.",
                representative_run_id=row.representative_run_id,
            )
        )
    return tuple(result)


def _sort_findings(findings: list[ScoutFinding]) -> tuple[ScoutFinding, ...]:
    order = {
        "GOON_REGRESSION": 0,
        "RUN_SPIKE": 1,
        "CPU_OUTLIER": 2,
        "MEMORY_OUTLIER": 3,
        "FAILURE_WASTE": 4,
        "HEAVY_HITTER": 5,
        "EXPENSIVE_RUN": 6,
        "MOST_ACTIVE": 7,
    }
    return tuple(
        sorted(
            findings,
            key=lambda item: (
                order[item.category],
                -(item.ratio if item.ratio is not None else item.observed),
                item.repository_name or item.goon_name or item.run_id or "",
            ),
        )
    )


def _expensive_run_findings(
    runs: tuple[RepositoryRunObservation, ...],
    steps: tuple[ScoutStepObservation, ...],
    *,
    limit: int | None,
) -> tuple[ScoutFinding, ...]:
    resources = repository_run_resources(runs, steps)
    complete = [item for item in resources if item.complete_cpu_seconds is not None]
    result: list[ScoutFinding] = []
    for item in sorted(complete, key=lambda value: value.complete_cpu_seconds or 0.0, reverse=True)[
        :limit
    ]:
        result.append(
            ScoutFinding(
                category="EXPENSIVE_RUN",
                entity_kind="RUN",
                repository_id=item.run.repository_id,
                repository_name=item.run.repository_name,
                run_id=item.run.run_id,
                metric="cpu_per_run",
                unit="CPU-s",
                observed=item.complete_cpu_seconds or 0.0,
                sample_count=1,
                coverage=coverage(item.cpu_observed_attempts, item.eligible_attempts),
                explanation="One repository execution with high complete CPU consumption in the selected window.",
                representative_run_id=item.run.run_id,
            )
        )
    return tuple(result)


def _failure_waste_findings(
    rows: tuple[ScoutRankingRow, ...],
    runs: tuple[RepositoryRunObservation, ...],
    steps: tuple[ScoutStepObservation, ...],
    *,
    limit: int | None,
) -> tuple[ScoutFinding, ...]:
    result: list[ScoutFinding] = []
    unsuccessful_by_repo = defaultdict(list)
    for item in repository_run_resources(runs, steps):
        if item.run.repository_status in _UNSUCCESSFUL_STATUSES:
            unsuccessful_by_repo[item.run.repository_id].append(item)
    candidates = sorted(
        rows,
        key=lambda item: item.failed_cpu_seconds + item.cancelled_cpu_seconds,
        reverse=True,
    )
    for row in candidates[:limit]:
        waste = row.failed_cpu_seconds + row.cancelled_cpu_seconds
        if waste <= 0:
            continue
        evidence = unsuccessful_by_repo.get(row.repository_id or "", [])
        representative = max(evidence, key=lambda item: item.observed_cpu_seconds, default=None)
        result.append(
            ScoutFinding(
                category="FAILURE_WASTE",
                entity_kind="REPOSITORY",
                repository_id=row.repository_id,
                repository_name=row.display_name,
                metric="failure_cpu",
                unit="CPU-s",
                observed=waste,
                sample_count=len(evidence),
                failed_cpu_seconds=row.failed_cpu_seconds,
                cancelled_cpu_seconds=row.cancelled_cpu_seconds,
                coverage=coverage(
                    sum(item.cpu_observed_attempts for item in evidence),
                    sum(item.eligible_attempts for item in evidence),
                ),
                explanation=(
                    f"Observed CPU attributed to unsuccessful repository executions: "
                    f"{row.failed_cpu_seconds:.1f} CPU-s failed/attention and "
                    f"{row.cancelled_cpu_seconds:.1f} CPU-s cancelled/interrupted."
                ),
                representative_run_id=representative.run.run_id if representative else None,
            )
        )
    return tuple(result)


def _peer_outlier_findings(
    steps: tuple[ScoutStepObservation, ...], *, metric: Literal["cpu", "memory"]
) -> tuple[ScoutFinding, ...]:
    successful = tuple(item for item in steps if item.repository_status is GameTapeStatus.PASSED)
    cohorts = group_steps(
        successful,
        key=lambda item: (
            item.goon_name,
            item.goon_source_sha256,
            item.step_id,
            item.environment_key,
        ),
    )
    result: list[ScoutFinding] = []
    for cohort in cohorts.values():
        result.extend(_cohort_outliers(cohort, metric=metric))
    return tuple(sorted(result, key=lambda item: item.ratio or 0.0, reverse=True))


@dataclass(frozen=True)
class _RepositoryMetricStats:
    median_value: float
    complete_count: int
    eligible_attempts: int
    observed_attempts: int
    representative: ScoutStepObservation


def _cohort_outliers(
    cohort: tuple[ScoutStepObservation, ...], *, metric: Literal["cpu", "memory"]
) -> tuple[ScoutFinding, ...]:
    by_repo = group_steps(cohort, key=lambda item: item.repository_id)
    stats = _repository_metric_stats(by_repo, metric=metric)
    if len(stats) <= SCOUT_PEER_MIN_REPOSITORIES:
        return ()
    sorted_medians = sorted(
        (item.median_value, repository_id) for repository_id, item in stats.items()
    )
    median_index = {repository_id: index for index, (_, repository_id) in enumerate(sorted_medians)}
    total_complete = sum(item.complete_count for item in stats.values())
    total_eligible = sum(item.eligible_attempts for item in cohort)
    total_observed = sum(_metric_observed_attempts(item, metric=metric) for item in cohort)
    result: list[ScoutFinding] = []
    for repository_id, item in stats.items():
        finding = _repository_outlier_finding(
            repository_id=repository_id,
            stats=item,
            baseline=_leave_one_out_median(sorted_medians, median_index[repository_id]),
            peer_repository_count=len(stats) - 1,
            peer_complete_count=total_complete - item.complete_count,
            peer_eligible_attempts=total_eligible - item.eligible_attempts,
            peer_observed_attempts=total_observed - item.observed_attempts,
            metric=metric,
        )
        if finding is not None:
            result.append(finding)
    return tuple(result)


def _repository_metric_stats(
    by_repo: Mapping[str, tuple[ScoutStepObservation, ...]], *, metric: Literal["cpu", "memory"]
) -> dict[str, _RepositoryMetricStats]:
    result: dict[str, _RepositoryMetricStats] = {}
    for repository_id, items in by_repo.items():
        values = _complete_metric_values(items, metric=metric)
        if not values:
            continue
        result[str(repository_id)] = _RepositoryMetricStats(
            median_value=float(median(values)),
            complete_count=len(values),
            eligible_attempts=sum(item.eligible_attempts for item in items),
            observed_attempts=sum(_metric_observed_attempts(item, metric=metric) for item in items),
            representative=max(items, key=lambda item: _step_metric_value(item, metric=metric)),
        )
    return result


def _metric_observed_attempts(
    item: ScoutStepObservation, *, metric: Literal["cpu", "memory"]
) -> int:
    return item.cpu_observed_attempts if metric == "cpu" else item.memory_observed_attempts


def _leave_one_out_median(sorted_values: list[tuple[float, str]], excluded_index: int) -> float:
    count = len(sorted_values)
    peer_count = count - 1
    middle = peer_count // 2
    if peer_count % 2 == 1:
        source_index = middle + (1 if excluded_index <= middle else 0)
        return sorted_values[source_index][0]
    lower = middle - 1
    upper = middle
    lower_source = lower + (1 if excluded_index <= lower else 0)
    upper_source = upper + (1 if excluded_index <= upper else 0)
    return (sorted_values[lower_source][0] + sorted_values[upper_source][0]) / 2.0


def _complete_metric_values(
    items: tuple[ScoutStepObservation, ...], *, metric: Literal["cpu", "memory"]
) -> list[float]:
    if metric == "cpu":
        return [
            item.complete_cpu_seconds for item in items if item.complete_cpu_seconds is not None
        ]
    return [
        float(item.complete_peak_memory_bytes)
        for item in items
        if item.complete_peak_memory_bytes is not None
    ]


def _repository_outlier_finding(
    *,
    repository_id: str,
    stats: _RepositoryMetricStats,
    baseline: float,
    peer_repository_count: int,
    peer_complete_count: int,
    peer_eligible_attempts: int,
    peer_observed_attempts: int,
    metric: Literal["cpu", "memory"],
) -> ScoutFinding | None:
    if (
        peer_complete_count < SCOUT_PEER_MIN_EXECUTIONS
        or peer_repository_count < SCOUT_PEER_MIN_REPOSITORIES
    ):
        return None
    peer_coverage = coverage(peer_observed_attempts, peer_eligible_attempts)
    if metric == "memory" and peer_coverage.percent < SCOUT_MEMORY_COMPARISON_MIN_COVERAGE_PERCENT:
        return None
    observed_ratio = comparison_ratio(stats.median_value, baseline)
    if observed_ratio is None or observed_ratio < SCOUT_OUTLIER_RATIO:
        return None
    representative = stats.representative
    return ScoutFinding(
        category="CPU_OUTLIER" if metric == "cpu" else "MEMORY_OUTLIER",
        entity_kind="STEP",
        repository_id=repository_id,
        repository_name=representative.repository_name,
        goon_name=representative.goon_name,
        goon_source_sha256=representative.goon_source_sha256,
        step_id=representative.step_id,
        metric="cpu_per_run" if metric == "cpu" else "peak_memory",
        unit="CPU-s" if metric == "cpu" else "bytes",
        observed=stats.median_value,
        baseline=baseline,
        ratio=observed_ratio,
        sample_count=peer_complete_count,
        peer_repository_count=peer_repository_count,
        coverage=peer_coverage,
        comparison_environment=comparison_environment(representative),
        explanation=(
            f"Repository median {'CPU/run' if metric == 'cpu' else 'peak memory'} is "
            f"{observed_ratio:.1f}× the peer median for the same Goon source, Step and "
            "compatible execution environment. Scout reports the measured difference, not its cause."
        ),
        representative_run_id=representative.run_id,
    )


def _step_metric_value(item: ScoutStepObservation, *, metric: Literal["cpu", "memory"]) -> float:
    if metric == "cpu":
        return item.complete_cpu_seconds or 0.0
    return float(item.complete_peak_memory_bytes or 0)


def _run_spike_findings(
    all_runs: tuple[RepositoryRunObservation, ...], window: ScoutWindow
) -> tuple[ScoutFinding, ...]:
    required_history_hours = window.hours * (SCOUT_RUN_SPIKE_HISTORY_PERIODS + 1)
    if required_history_hours > SCOUT_RUN_RETENTION_DAYS * 24:
        return ()
    current = runs_in_window(all_runs, window)
    by_repo_all: dict[str, list[RepositoryRunObservation]] = defaultdict(list)
    by_repo_current: dict[str, list[RepositoryRunObservation]] = defaultdict(list)
    for run in all_runs:
        by_repo_all[run.repository_id].append(run)
    for run in current:
        by_repo_current[run.repository_id].append(run)
    duration = window.end_at - window.start_at
    oldest_baseline_start = window.start_at - duration * SCOUT_RUN_SPIKE_HISTORY_PERIODS
    result: list[ScoutFinding] = []
    for repository_id, current_runs in by_repo_current.items():
        repository_runs = by_repo_all[repository_id]
        if not any(run.started_at < oldest_baseline_start for run in repository_runs):
            continue
        historical_counts: list[int] = []
        for period in range(1, SCOUT_RUN_SPIKE_HISTORY_PERIODS + 1):
            end = window.start_at - duration * (period - 1)
            start = end - duration
            historical_counts.append(
                sum(1 for run in repository_runs if start <= run.started_at < end)
            )
        if len(historical_counts) < SCOUT_RUN_SPIKE_HISTORY_PERIODS:
            continue
        baseline = float(median(historical_counts))
        if baseline <= 0:
            continue
        observed = float(len(current_runs))
        ratio = observed / baseline
        if ratio < SCOUT_RUN_SPIKE_RATIO:
            continue
        latest = max(current_runs, key=lambda item: item.started_at)
        result.append(
            ScoutFinding(
                category="RUN_SPIKE",
                entity_kind="REPOSITORY",
                repository_id=repository_id,
                repository_name=latest.repository_name,
                metric="run_frequency",
                unit="runs/window",
                observed=observed,
                baseline=baseline,
                ratio=ratio,
                sample_count=SCOUT_RUN_SPIKE_HISTORY_PERIODS,
                explanation=(
                    f"{len(current_runs)} runs in the selected window versus the repository's own "
                    f"{SCOUT_RUN_SPIKE_HISTORY_PERIODS}-period median of {baseline:.1f}; "
                    f"{ratio:.1f}× historical median."
                ),
                representative_run_id=latest.run_id,
            )
        )
    return tuple(sorted(result, key=lambda item: item.ratio or 0.0, reverse=True))


def _goon_regression_findings(steps: tuple[ScoutStepObservation, ...]) -> tuple[ScoutFinding, ...]:
    successful = tuple(
        item
        for item in steps
        if item.repository_status is GameTapeStatus.PASSED and item.complete_cpu_seconds is not None
    )
    grouped = group_steps(
        successful, key=lambda item: (item.goon_name, item.step_id, item.environment_key)
    )
    result: list[ScoutFinding] = []
    for (goon_name, step_id, _environment_key), items in grouped.items():
        by_version = group_steps(items, key=lambda item: item.goon_source_sha256)
        if len(by_version) < 2:
            continue
        version_order = sorted(
            by_version.items(), key=lambda pair: max(item.started_at for item in pair[1])
        )
        baseline_sha, baseline_items = version_order[-2]
        current_sha, current_items = version_order[-1]
        comparison = _paired_version_cpu(baseline_items, current_items)
        if comparison is None:
            continue
        baseline, observed, ratio, overlap, baseline_overlap, current_overlap = comparison
        if ratio < SCOUT_GOON_REGRESSION_RATIO:
            continue
        representative = max(current_overlap, key=lambda item: item.complete_cpu_seconds or 0.0)
        result.append(
            ScoutFinding(
                category="GOON_REGRESSION",
                entity_kind="STEP",
                goon_name=goon_name,
                goon_source_sha256=current_sha,
                baseline_goon_source_sha256=baseline_sha,
                step_id=step_id,
                metric="cpu_per_run",
                unit="CPU-s",
                observed=observed,
                baseline=baseline,
                ratio=ratio,
                sample_count=len(current_overlap),
                peer_repository_count=len(overlap),
                current_repository_count=len({item.repository_id for item in current_items}),
                baseline_repository_count=len({item.repository_id for item in baseline_items}),
                overlap_repository_count=len(overlap),
                current_period_start_at=min(item.started_at for item in current_overlap),
                current_period_end_at=max(item.started_at for item in current_overlap),
                baseline_period_start_at=min(item.started_at for item in baseline_overlap),
                baseline_period_end_at=max(item.started_at for item in baseline_overlap),
                coverage=coverage(
                    sum(item.cpu_observed_attempts for item in current_overlap),
                    sum(item.eligible_attempts for item in current_overlap),
                ),
                comparison_environment=comparison_environment(representative),
                explanation=(
                    f"Median per-repository CPU/run for {goon_name} / {step_id} increased to {ratio:.1f}× "
                    f"the previous Goon source among {len(overlap)} repositories observed on both sources. "
                    "The source change is associated with the measured difference; Scout does not assert causation."
                ),
                representative_run_id=representative.run_id,
            )
        )
    return tuple(sorted(result, key=lambda item: item.ratio or 0.0, reverse=True))


def _paired_version_cpu(
    baseline_items: tuple[ScoutStepObservation, ...],
    current_items: tuple[ScoutStepObservation, ...],
) -> (
    tuple[
        float,
        float,
        float,
        set[str],
        tuple[ScoutStepObservation, ...],
        tuple[ScoutStepObservation, ...],
    ]
    | None
):
    baseline_repos = {item.repository_id for item in baseline_items}
    current_repos = {item.repository_id for item in current_items}
    overlap = baseline_repos & current_repos
    if len(overlap) < SCOUT_PEER_MIN_REPOSITORIES:
        return None
    baseline_overlap = tuple(item for item in baseline_items if item.repository_id in overlap)
    current_overlap = tuple(item for item in current_items if item.repository_id in overlap)
    if (
        len(baseline_overlap) < SCOUT_PEER_MIN_EXECUTIONS
        or len(current_overlap) < SCOUT_PEER_MIN_EXECUTIONS
    ):
        return None
    baseline = _median_repository_cpu(baseline_overlap)
    observed = _median_repository_cpu(current_overlap)
    ratio = comparison_ratio(observed, baseline)
    if ratio is None:
        return None
    return baseline, observed, ratio, overlap, baseline_overlap, current_overlap


def _median_repository_cpu(items: tuple[ScoutStepObservation, ...]) -> float:
    by_repository = group_steps(items, key=lambda item: item.repository_id)
    medians = [
        median(item.complete_cpu_seconds for item in group if item.complete_cpu_seconds is not None)
        for group in by_repository.values()
    ]
    return float(median(medians))


def finding_counts(
    findings: tuple[ScoutFinding, ...], repository_rows: tuple[ScoutRankingRow, ...]
) -> tuple[ScoutFindingCount, ...]:
    counts: defaultdict[ScoutFindingCategory, int] = defaultdict(int)
    for finding in findings:
        counts[finding.category] += 1
    heavy_count = sum(1 for row in repository_rows if (row.total_cpu_seconds or 0) > 0)
    failure_waste_count = sum(
        1 for row in repository_rows if row.failed_cpu_seconds + row.cancelled_cpu_seconds > 0
    )
    explanations: tuple[tuple[ScoutFindingCategory, str], ...] = (
        ("HEAVY_HITTER", "Highest total observed CPU consumption"),
        ("RUN_SPIKE", "Repositories running much more often than their own historical baseline"),
        ("GOON_REGRESSION", "Goon source changes associated with increased CPU/run"),
        ("CPU_OUTLIER", "CPU-intensive Steps relative to genuinely comparable peers"),
        ("MEMORY_OUTLIER", "High peak-memory Steps relative to genuinely comparable peers"),
        (
            "FAILURE_WASTE",
            "Observed CPU consumed by failed, attention, cancelled or interrupted work",
        ),
    )
    return tuple(
        ScoutFindingCount(
            category=category,
            count=(
                heavy_count
                if category == "HEAVY_HITTER"
                else failure_waste_count
                if category == "FAILURE_WASTE"
                else counts[category]
            ),
            explanation=explanation,
        )
        for category, explanation in explanations
    )
