from __future__ import annotations

from typing import Literal

from pydantic import AwareDatetime, Field

from oh_goon.core.models.base import ImmutableModel
from oh_goon.core.vocabulary import GameTapeStatus

ScoutFindingCategory = Literal[
    "HEAVY_HITTER",
    "MOST_ACTIVE",
    "EXPENSIVE_RUN",
    "CPU_OUTLIER",
    "MEMORY_OUTLIER",
    "RUN_SPIKE",
    "GOON_REGRESSION",
    "FAILURE_WASTE",
]
ScoutEntityKind = Literal["REPOSITORY", "GOON", "STEP", "RUN"]
ScoutSearchEntityKind = Literal["REPOSITORY", "PLAN", "GOON"]
ScoutDetailSection = Literal["plans", "goons", "steps", "repositories", "versions"]
ScoutMetricName = Literal[
    "total_cpu",
    "run_count",
    "cpu_per_run",
    "peak_memory",
    "run_frequency",
    "failure_cpu",
]
ScoutMetricUnit = Literal["CPU-s", "runs", "bytes", "runs/window"]


class ScoutWindow(ImmutableModel):
    start_at: AwareDatetime
    end_at: AwareDatetime
    hours: int = Field(ge=1, le=24 * 90)
    run_assignment: Literal["start_timestamp"] = "start_timestamp"


class ScoutCoverage(ImmutableModel):
    observed: int = Field(ge=0)
    eligible: int = Field(ge=0)
    percent: float = Field(ge=0, le=100)


class ScoutResourceTotals(ImmutableModel):
    """Observed resource totals. Missing observations remain missing, never zero-filled."""

    total_cpu_seconds: float | None = Field(default=None, ge=0)
    cpu_coverage: ScoutCoverage
    peak_memory_bytes: int | None = Field(default=None, ge=0)
    memory_coverage: ScoutCoverage
    memory_time_gib_seconds: None = None
    memory_time_available: Literal[False] = False


class ScoutResourceEnvelope(ImmutableModel):
    cpu_millis: int = Field(ge=100)
    memory_bytes: int = Field(ge=64 * 1024 * 1024)


class ScoutComparisonEnvironment(ImmutableModel):
    isolation_backend_id: str | None = Field(default=None, min_length=1, max_length=80)
    isolation_configuration_id: str | None = Field(default=None, min_length=1)
    runtime_root_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    host_toolchain_id: str | None = Field(default=None, min_length=1)
    resource_envelopes: tuple[ScoutResourceEnvelope, ...] = Field(
        default_factory=tuple, max_length=16
    )


class ScoutRankingRow(ImmutableModel):
    entity_kind: Literal["REPOSITORY", "GOON"]
    key: str = Field(min_length=1)
    display_name: str = Field(min_length=1)
    repository_id: str | None = Field(default=None, min_length=1)
    goon_name: str | None = Field(default=None, min_length=1)
    goon_source_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    run_count: int = Field(ge=0)
    repository_count: int = Field(ge=0)
    attempt_count: int = Field(ge=0)
    total_cpu_seconds: float | None = Field(default=None, ge=0)
    median_cpu_seconds_per_run: float | None = Field(default=None, ge=0)
    median_average_cpu_cores: float | None = Field(default=None, ge=0)
    peak_memory_bytes: int | None = Field(default=None, ge=0)
    failed_cpu_seconds: float = Field(default=0, ge=0)
    cancelled_cpu_seconds: float = Field(default=0, ge=0)
    cpu_coverage: ScoutCoverage
    memory_coverage: ScoutCoverage
    median_duration_seconds_per_run: float | None = Field(default=None, ge=0)
    representative_run_id: str | None = Field(default=None, min_length=1)


class ScoutFindingCount(ImmutableModel):
    category: ScoutFindingCategory
    count: int = Field(ge=0)
    explanation: str = Field(min_length=1, max_length=240)


class ScoutFinding(ImmutableModel):
    category: ScoutFindingCategory
    entity_kind: ScoutEntityKind
    repository_id: str | None = Field(default=None, min_length=1)
    repository_name: str | None = Field(default=None, min_length=1)
    goon_name: str | None = Field(default=None, min_length=1)
    goon_source_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    baseline_goon_source_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    step_id: str | None = Field(default=None, min_length=1, max_length=80)
    run_id: str | None = Field(default=None, min_length=1)
    metric: ScoutMetricName
    unit: ScoutMetricUnit
    observed: float = Field(ge=0)
    baseline: float | None = Field(default=None, ge=0)
    ratio: float | None = Field(default=None, ge=0)
    sample_count: int = Field(default=0, ge=0)
    peer_repository_count: int = Field(default=0, ge=0)
    current_repository_count: int = Field(default=0, ge=0)
    baseline_repository_count: int = Field(default=0, ge=0)
    overlap_repository_count: int = Field(default=0, ge=0)
    failed_cpu_seconds: float | None = Field(default=None, ge=0)
    cancelled_cpu_seconds: float | None = Field(default=None, ge=0)
    current_period_start_at: AwareDatetime | None = None
    current_period_end_at: AwareDatetime | None = None
    baseline_period_start_at: AwareDatetime | None = None
    baseline_period_end_at: AwareDatetime | None = None
    coverage: ScoutCoverage | None = None
    comparison_environment: ScoutComparisonEnvironment | None = None
    explanation: str = Field(min_length=1, max_length=500)
    representative_run_id: str | None = Field(default=None, min_length=1)


class ScoutTrendPoint(ImmutableModel):
    start_at: AwareDatetime
    end_at: AwareDatetime
    run_count: int = Field(ge=0)
    total_cpu_seconds: float | None = Field(default=None, ge=0)
    peak_memory_bytes: int | None = Field(default=None, ge=0)
    cpu_coverage: ScoutCoverage
    memory_coverage: ScoutCoverage


class ScoutOverview(ImmutableModel):
    enabled: bool
    journal_revision: int = Field(ge=0)
    window: ScoutWindow
    executions: int = Field(ge=0)
    active_repositories: int = Field(ge=0)
    failure_rate_percent: float = Field(ge=0, le=100)
    totals: ScoutResourceTotals
    needs_scouting: tuple[ScoutFindingCount, ...] = Field(default_factory=tuple)
    heavy_hitter_total: int = Field(ge=0)
    goon_leader_total: int = Field(ge=0)
    heavy_hitters: tuple[ScoutRankingRow, ...] = Field(default_factory=tuple, max_length=100)
    goon_leaders: tuple[ScoutRankingRow, ...] = Field(default_factory=tuple, max_length=100)
    trend: tuple[ScoutTrendPoint, ...] = Field(default_factory=tuple, max_length=256)


class ScoutRunSample(ImmutableModel):
    run_id: str = Field(min_length=1)
    started_at: AwareDatetime
    status: GameTapeStatus
    cpu_seconds: float | None = Field(default=None, ge=0)
    peak_memory_bytes: int | None = Field(default=None, ge=0)
    duration_seconds: float | None = Field(default=None, ge=0)
    average_cpu_cores: float | None = Field(default=None, ge=0)
    goon_name: str | None = Field(default=None, min_length=1)
    step_id: str | None = Field(default=None, min_length=1, max_length=80)


class ScoutStepSummary(ImmutableModel):
    step_id: str = Field(min_length=1, max_length=80)
    goon_name: str = Field(min_length=1)
    goon_source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    run_count: int = Field(ge=0)
    attempt_count: int = Field(ge=0)
    total_cpu_seconds: float | None = Field(default=None, ge=0)
    median_cpu_seconds_per_run: float | None = Field(default=None, ge=0)
    median_average_cpu_cores: float | None = Field(default=None, ge=0)
    peer_median_cpu_seconds_per_run: float | None = Field(default=None, ge=0)
    cpu_ratio_to_peer_median: float | None = Field(default=None, ge=0)
    peak_memory_bytes: int | None = Field(default=None, ge=0)
    peer_median_peak_memory_bytes: float | None = Field(default=None, ge=0)
    memory_ratio_to_peer_median: float | None = Field(default=None, ge=0)
    peer_execution_count: int = Field(default=0, ge=0)
    peer_repository_count: int = Field(default=0, ge=0)
    peer_cpu_coverage: ScoutCoverage | None = None
    peer_memory_coverage: ScoutCoverage | None = None
    comparison_environment: ScoutComparisonEnvironment | None = None
    cpu_coverage: ScoutCoverage
    memory_coverage: ScoutCoverage
    median_duration_seconds_per_run: float | None = Field(default=None, ge=0)
    peer_median_duration_seconds_per_run: float | None = Field(default=None, ge=0)
    duration_ratio_to_peer_median: float | None = Field(default=None, ge=0)
    representative_run_id: str | None = Field(default=None, min_length=1)


class ScoutPlanSummary(ImmutableModel):
    plan_id: str = Field(min_length=1)
    plan_name: str = Field(min_length=1)
    run_count: int = Field(ge=0)
    attempt_count: int = Field(ge=0)
    total_cpu_seconds: float | None = Field(default=None, ge=0)
    median_cpu_seconds_per_run: float | None = Field(default=None, ge=0)
    median_average_cpu_cores: float | None = Field(default=None, ge=0)
    peak_memory_bytes: int | None = Field(default=None, ge=0)
    cpu_coverage: ScoutCoverage
    memory_coverage: ScoutCoverage
    median_duration_seconds_per_run: float | None = Field(default=None, ge=0)
    representative_run_id: str | None = Field(default=None, min_length=1)


class ScoutRepositoryDetail(ImmutableModel):
    enabled: bool
    journal_revision: int = Field(ge=0)
    window: ScoutWindow
    repository: ScoutRankingRow
    selected_plan_id: str | None = Field(default=None, min_length=1)
    page_section: ScoutDetailSection | None = None
    page_offset: int = Field(ge=0)
    page_limit: int = Field(ge=1, le=100)
    plan_total: int = Field(ge=0)
    goon_total: int = Field(ge=0)
    step_total: int = Field(ge=0)
    plans: tuple[ScoutPlanSummary, ...] = Field(default_factory=tuple, max_length=100)
    goons: tuple[ScoutRankingRow, ...] = Field(default_factory=tuple, max_length=100)
    steps: tuple[ScoutStepSummary, ...] = Field(default_factory=tuple, max_length=200)
    representative_runs: tuple[ScoutRunSample, ...] = Field(default_factory=tuple, max_length=20)


class ScoutPlanDetail(ImmutableModel):
    enabled: bool
    journal_revision: int = Field(ge=0)
    window: ScoutWindow
    plan_id: str = Field(min_length=1)
    plan_name: str = Field(min_length=1)
    page_section: ScoutDetailSection | None = None
    page_offset: int = Field(ge=0)
    page_limit: int = Field(ge=1, le=100)
    repository_total: int = Field(ge=0)
    goon_total: int = Field(ge=0)
    step_total: int = Field(ge=0)
    repositories: tuple[ScoutRankingRow, ...] = Field(default_factory=tuple, max_length=100)
    goons: tuple[ScoutRankingRow, ...] = Field(default_factory=tuple, max_length=100)
    steps: tuple[ScoutStepSummary, ...] = Field(default_factory=tuple, max_length=200)
    representative_runs: tuple[ScoutRunSample, ...] = Field(default_factory=tuple, max_length=20)


class ScoutGoonVersionSummary(ImmutableModel):
    goon_name: str = Field(min_length=1)
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    first_seen_at: AwareDatetime
    last_seen_at: AwareDatetime
    repository_count: int = Field(ge=0)
    run_count: int = Field(ge=0)
    total_cpu_seconds: float | None = Field(default=None, ge=0)
    median_cpu_seconds_per_run: float | None = Field(default=None, ge=0)
    cpu_coverage: ScoutCoverage


class ScoutGoonDetail(ImmutableModel):
    enabled: bool
    journal_revision: int = Field(ge=0)
    window: ScoutWindow
    goon_name: str = Field(min_length=1)
    selected_source_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    page_section: ScoutDetailSection | None = None
    page_offset: int = Field(ge=0)
    page_limit: int = Field(ge=1, le=100)
    version_total: int = Field(ge=0)
    repository_total: int = Field(ge=0)
    step_total: int = Field(ge=0)
    versions: tuple[ScoutGoonVersionSummary, ...] = Field(default_factory=tuple, max_length=100)
    repositories: tuple[ScoutRankingRow, ...] = Field(default_factory=tuple, max_length=100)
    steps: tuple[ScoutStepSummary, ...] = Field(default_factory=tuple, max_length=200)
    representative_runs: tuple[ScoutRunSample, ...] = Field(default_factory=tuple, max_length=20)
    trend: tuple[ScoutTrendPoint, ...] = Field(default_factory=tuple, max_length=256)


class ScoutGoonLeadersPage(ImmutableModel):
    enabled: bool
    journal_revision: int = Field(ge=0)
    window: ScoutWindow
    total: int = Field(ge=0)
    offset: int = Field(ge=0)
    limit: int = Field(ge=1, le=100)
    items: tuple[ScoutRankingRow, ...] = Field(default_factory=tuple, max_length=100)


class ScoutFindingsPage(ImmutableModel):
    enabled: bool
    journal_revision: int = Field(ge=0)
    window: ScoutWindow
    category: ScoutFindingCategory | None = None
    total: int = Field(ge=0)
    offset: int = Field(ge=0)
    limit: int = Field(ge=1, le=100)
    items: tuple[ScoutFinding, ...] = Field(default_factory=tuple, max_length=100)


class ScoutSearchResult(ImmutableModel):
    entity_kind: ScoutSearchEntityKind
    key: str = Field(min_length=1)
    display_name: str = Field(min_length=1)
    repository_id: str | None = Field(default=None, min_length=1)
    plan_id: str | None = Field(default=None, min_length=1)
    goon_name: str | None = Field(default=None, min_length=1)
    goon_source_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


class ScoutSearchPage(ImmutableModel):
    enabled: bool
    journal_revision: int = Field(ge=0)
    window: ScoutWindow
    query: str = Field(min_length=1, max_length=120)
    total: int = Field(ge=0)
    offset: int = Field(ge=0)
    limit: int = Field(ge=1, le=100)
    items: tuple[ScoutSearchResult, ...] = Field(default_factory=tuple, max_length=100)


class ScoutHealth(ImmutableModel):
    enabled: bool
    measurement_schema_version: int = Field(ge=1)
    journal_revision: int = Field(ge=0)
    processed_journal_revision: int = Field(ge=0)
    lag_revisions: int = Field(ge=0)
    retained_run_observations: int = Field(ge=0)
    retained_step_observations: int = Field(ge=0)
    snapshot_cache_entries: int = Field(ge=0)
    snapshot_cache_hits: int = Field(ge=0)
    snapshot_cache_misses: int = Field(ge=0)
    last_snapshot_load_ms: float | None = Field(default=None, ge=0)
    analytical_storage_bytes: int = Field(ge=0)
    detail_retention_days: int = Field(ge=1)
    run_retention_days: int = Field(ge=1)


class ScoutMetricDefinition(ImmutableModel):
    metric: str = Field(min_length=1)
    display_name: str = Field(min_length=1)
    definition: str = Field(min_length=1)
    unit: str = Field(min_length=1)
    source: str = Field(min_length=1)
    aggregation: str = Field(min_length=1)
    missing_data: str = Field(min_length=1)
    comparison_rule: str = Field(min_length=1)


class ScoutMetricDictionary(ImmutableModel):
    measurement_schema_version: int = Field(ge=1)
    definitions: tuple[ScoutMetricDefinition, ...] = Field(min_length=1)


class ScoutRunStepEvidence(ImmutableModel):
    """Measured Step evidence for one exact canonical Run; no execution interpretation."""

    step_id: str = Field(min_length=1, max_length=80)
    goon_name: str = Field(min_length=1)
    goon_source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    status: GameTapeStatus
    cpu_seconds: float | None = Field(default=None, ge=0)
    peak_memory_bytes: int | None = Field(default=None, ge=0)
    duration_seconds: float | None = Field(default=None, ge=0)
    comparable_runs: int = Field(ge=0, le=20)
    typical_cpu_seconds: float | None = Field(default=None, ge=0)
    typical_peak_memory_bytes: float | None = Field(default=None, ge=0)
    typical_duration_seconds: float | None = Field(default=None, ge=0)
    cpu_difference_percent: int | None = None
    memory_difference_percent: int | None = None
    duration_difference_percent: int | None = None
    comparison_environment: list[object] = Field(default_factory=list)


class ScoutRunRepositoryEvidence(ImmutableModel):
    repository_id: str = Field(min_length=1)
    repository_name: str = Field(min_length=1)
    status: GameTapeStatus
    duration_seconds: float | None = Field(default=None, ge=0)
    cpu_seconds: float | None = Field(default=None, ge=0)
    peak_memory_bytes: int | None = Field(default=None, ge=0)
    steps: tuple[ScoutRunStepEvidence, ...] = Field(default_factory=tuple)


class ScoutRunEvidence(ImmutableModel):
    enabled: bool
    journal_revision: int = Field(ge=0)
    run_id: str = Field(min_length=1)
    repositories: tuple[ScoutRunRepositoryEvidence, ...] = Field(default_factory=tuple)
