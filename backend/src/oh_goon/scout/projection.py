from __future__ import annotations

from oh_goon.core.execution_observability import RepositoryRunObservation
from oh_goon.scout.models import (
    ScoutDetailSection,
    ScoutFindingCategory,
    ScoutFindingsPage,
    ScoutGoonDetail,
    ScoutGoonLeadersPage,
    ScoutMetricDefinition,
    ScoutMetricDictionary,
    ScoutOverview,
    ScoutPlanDetail,
    ScoutRepositoryDetail,
    ScoutRunSample,
    ScoutWindow,
)
from oh_goon.scout.aggregation import (
    build_step_peer_index,
    cpu_rank_key,
    group_steps,
    plan_summaries,
    repository_run_samples,
    resource_totals,
    runs_in_window,
    step_summary,
    steps_in_window,
    trend,
)
from oh_goon.scout.aggregation import (
    goon_rows as build_goon_rows,
)
from oh_goon.scout.aggregation import (
    goon_versions as build_goon_versions,
)
from oh_goon.scout.aggregation import (
    repository_rows as build_repository_rows,
)
from oh_goon.scout.evidence import (
    ScoutEvidenceSnapshot,
    ScoutStepObservation,
    project_scout_evidence as project_scout_evidence,
)
from oh_goon.scout.findings import all_findings, finding_counts
from oh_goon.scout.policy import (
    SCOUT_DEFAULT_LIMIT,
    SCOUT_DETAIL_ENTITY_LIMIT,
    SCOUT_MEASUREMENT_SCHEMA_VERSION,
    SCOUT_MEMORY_COMPARISON_MIN_COVERAGE_PERCENT,
    SCOUT_REPRESENTATIVE_RUN_LIMIT,
    SCOUT_RUN_SPIKE_HISTORY_PERIODS,
)
from oh_goon.core.vocabulary import GameTapeStatus

_FAILURE_STATUSES = frozenset({GameTapeStatus.FAILED})


def scout_metric_dictionary() -> ScoutMetricDictionary:
    return ScoutMetricDictionary(
        measurement_schema_version=SCOUT_MEASUREMENT_SCHEMA_VERSION,
        definitions=_resource_metric_definitions() + _execution_metric_definitions(),
    )


def _resource_metric_definitions() -> tuple[ScoutMetricDefinition, ...]:
    return (
        ScoutMetricDefinition(
            metric="total_cpu",
            display_name="Total CPU",
            definition="Accumulated observed processor execution time attributable to physical Step attempts.",
            unit="CPU-seconds",
            source="PlanResourceEvidence.cpu_usage_usec",
            aggregation="Sum observed non-overlapping physical attempt CPU time.",
            missing_data="Missing CPU observations remain missing and reduce visible coverage; they are never converted to zero.",
            comparison_rule="Rankings may compare additive CPU totals; efficiency comparisons require compatible Goon, source identity, Step, environment/resource envelope, outcome population, window, samples and coverage.",
        ),
        ScoutMetricDefinition(
            metric="peak_memory",
            display_name="Peak memory",
            definition="Highest reliably observed attributed memory usage in the selected execution boundary.",
            unit="bytes (displayed as MiB/GiB)",
            source="PlanResourceEvidence.memory_peak_bytes",
            aggregation="Maximum observed peak; peaks are never added across sequential Steps.",
            missing_data="Missing memory observations remain missing and reduce visible coverage.",
            comparison_rule=f"Peer comparison requires compatible work and at least {SCOUT_MEMORY_COMPARISON_MIN_COVERAGE_PERCENT:.0f}% memory telemetry coverage.",
        ),
        ScoutMetricDefinition(
            metric="average_cpu",
            display_name="Average CPU",
            definition="Average processor concurrency during a measured execution boundary.",
            unit="cores",
            source="Derived from complete CPU-seconds divided by canonical positive wall-duration seconds.",
            aggregation="Per execution: CPU-seconds / wall-seconds; typical display uses the median of complete successful executions.",
            missing_data="Unavailable unless both complete CPU time and positive wall duration are available; never inferred from resource limits.",
            comparison_rule="Descriptive in this measurement schema; no Scout finding is produced from average CPU alone.",
        ),
        ScoutMetricDefinition(
            metric="peak_cpu",
            display_name="Peak CPU",
            definition="Highest reliably observed instantaneous CPU consumption.",
            unit="cores",
            source="Not currently collected by canonical Oh Goon execution evidence.",
            aggregation="Unavailable.",
            missing_data="Always unavailable in this measurement schema; cumulative CPU time cannot be converted into peak CPU.",
            comparison_rule="No comparison is produced until canonical instantaneous CPU evidence exists.",
        ),
        ScoutMetricDefinition(
            metric="memory_time",
            display_name="Memory-time",
            definition="Memory use integrated over execution duration.",
            unit="GiB-seconds",
            source="Not currently collected by canonical Oh Goon execution evidence.",
            aggregation="Unavailable.",
            missing_data="Always unavailable in this measurement schema; Scout does not estimate it from peak memory.",
            comparison_rule="No comparison is produced until canonical memory-time evidence exists.",
        ),
    )


def _execution_metric_definitions() -> tuple[ScoutMetricDefinition, ...]:
    return (
        ScoutMetricDefinition(
            metric="cpu_per_run",
            display_name="Median CPU/run",
            definition="Median complete CPU-seconds for a repository execution in the selected population.",
            unit="CPU-seconds/run",
            source="Complete physical-attempt CPU observations aggregated to repository execution.",
            aggregation="Median successful complete executions; incomplete or unsuccessful runs are excluded rather than treated as zero.",
            missing_data="Unavailable when no complete successful run measurements exist.",
            comparison_rule="Successful completed comparable work only; peer median is the default baseline.",
        ),
        ScoutMetricDefinition(
            metric="wall_duration",
            display_name="Wall duration",
            definition="Actual elapsed time recorded for a repository execution or Step boundary.",
            unit="seconds",
            source="GameTapeRepositoryExecution.duration_ms / GameTapeStepExecution.duration_ms",
            aggregation="Median for typical successful completed work; individual samples retain their observed elapsed duration.",
            missing_data="Unavailable when canonical start/finish timing is incomplete; it is never inferred from CPU time.",
            comparison_rule="Successful completed comparable work only; Step peer comparisons require the same Goon source, Step and environment/resource envelope plus the canonical sample floor.",
        ),
        ScoutMetricDefinition(
            metric="run_count",
            display_name="Run count",
            definition="Number of repository executions whose run start timestamp falls inside the analytical window.",
            unit="runs",
            source="Canonical Fleet/Game Tape run start timestamp.",
            aggregation="Count distinct repository executions.",
            missing_data="Runs without a start timestamp are not assigned to an analytical period.",
            comparison_rule=f"Run-spike analysis compares a repository with its own {SCOUT_RUN_SPIKE_HISTORY_PERIODS} prior equal periods.",
        ),
        ScoutMetricDefinition(
            metric="run_frequency",
            display_name="Run frequency",
            definition="Repository execution count in one explicit analytical window.",
            unit="runs/window",
            source="Canonical repository-execution start timestamps.",
            aggregation="Count repository executions assigned by start timestamp to the selected or historical equal-sized window.",
            missing_data="Run-spike comparison is unavailable unless repository evidence predates the oldest required baseline period.",
            comparison_rule=f"Compare with the repository's own median across {SCOUT_RUN_SPIKE_HISTORY_PERIODS} prior equal windows; do not compare unrelated repositories.",
        ),
        ScoutMetricDefinition(
            metric="failure_cpu",
            display_name="Failure CPU",
            definition="Observed CPU consumed by repository executions that ended failed, attention-required, cancelled, or interrupted.",
            unit="CPU-seconds",
            source="Physical-attempt CPU observations plus canonical repository execution outcome.",
            aggregation="Sum observed CPU for unsuccessful repository executions; failed/attention and cancelled/interrupted consumption remain distinguishable in evidence.",
            missing_data="Missing CPU observations remain missing and reduce coverage; they are never converted to zero.",
            comparison_rule="Rank unsuccessful resource consumption directly; do not use unsuccessful executions in typical successful-run performance baselines.",
        ),
    )


def project_scout_overview(
    snapshot: ScoutEvidenceSnapshot,
    *,
    window: ScoutWindow,
    limit: int = SCOUT_DEFAULT_LIMIT,
    enabled: bool = True,
) -> ScoutOverview:
    current_runs = runs_in_window(snapshot.runs, window)
    current_steps = steps_in_window(snapshot.steps, window)
    repository_rows = build_repository_rows(current_runs, current_steps)
    goon_rows = build_goon_rows(current_steps)
    findings = all_findings(snapshot, window, current_runs, current_steps, repository_rows)
    heavy_hitters = tuple(row for row in repository_rows if (row.total_cpu_seconds or 0) > 0)
    goon_leaders = tuple(row for row in goon_rows if (row.total_cpu_seconds or 0) > 0)
    failed = sum(1 for run in current_runs if run.repository_status in _FAILURE_STATUSES)
    return ScoutOverview(
        enabled=enabled,
        journal_revision=snapshot.journal_revision,
        window=window,
        executions=len({(run.run_id, run.repository_id) for run in current_runs}),
        active_repositories=len({run.repository_id for run in current_runs}),
        failure_rate_percent=(failed / len(current_runs) * 100) if current_runs else 0.0,
        totals=resource_totals(current_steps),
        needs_scouting=finding_counts(findings, repository_rows),
        heavy_hitter_total=len(heavy_hitters),
        goon_leader_total=len(goon_leaders),
        heavy_hitters=tuple(sorted(heavy_hitters, key=cpu_rank_key, reverse=True)[:limit]),
        goon_leaders=tuple(sorted(goon_leaders, key=cpu_rank_key, reverse=True)[:limit]),
        trend=trend(current_runs, current_steps, window),
    )


def project_scout_findings(
    snapshot: ScoutEvidenceSnapshot,
    *,
    window: ScoutWindow,
    category: ScoutFindingCategory | None,
    offset: int,
    limit: int,
    enabled: bool = True,
) -> ScoutFindingsPage:
    current_runs = runs_in_window(snapshot.runs, window)
    current_steps = steps_in_window(snapshot.steps, window)
    repository_rows = build_repository_rows(current_runs, current_steps)
    selected = all_findings(
        snapshot,
        window,
        current_runs,
        current_steps,
        repository_rows,
        category=category,
        ranked_limit=None,
    )
    return ScoutFindingsPage(
        enabled=enabled,
        journal_revision=snapshot.journal_revision,
        window=window,
        category=category,
        total=len(selected),
        offset=offset,
        limit=limit,
        items=selected[offset : offset + limit],
    )


def project_scout_goon_leaders(
    snapshot: ScoutEvidenceSnapshot,
    *,
    window: ScoutWindow,
    offset: int,
    limit: int,
    enabled: bool = True,
) -> ScoutGoonLeadersPage:
    current_steps = steps_in_window(snapshot.steps, window)
    rows = tuple(row for row in build_goon_rows(current_steps) if (row.total_cpu_seconds or 0) > 0)
    ranked = tuple(sorted(rows, key=cpu_rank_key, reverse=True))
    return ScoutGoonLeadersPage(
        enabled=enabled,
        journal_revision=snapshot.journal_revision,
        window=window,
        total=len(ranked),
        offset=offset,
        limit=limit,
        items=ranked[offset : offset + limit],
    )


def _detail_slice(
    items: tuple,
    *,
    active_section: ScoutDetailSection,
    page_section: ScoutDetailSection | None,
    page_offset: int,
    page_limit: int,
    default_limit: int,
) -> tuple:
    offset = page_offset if page_section == active_section else 0
    limit = page_limit if page_section == active_section else default_limit
    return items[offset : offset + limit]


def project_scout_repository(
    snapshot: ScoutEvidenceSnapshot,
    *,
    window: ScoutWindow,
    repository_id: str,
    plan_id: str | None = None,
    page_section: ScoutDetailSection | None = None,
    page_offset: int = 0,
    page_limit: int = SCOUT_DETAIL_ENTITY_LIMIT,
    enabled: bool = True,
) -> ScoutRepositoryDetail | None:
    current_runs = tuple(
        run for run in runs_in_window(snapshot.runs, window) if run.repository_id == repository_id
    )
    if not current_runs:
        return None
    window_steps = steps_in_window(snapshot.steps, window)
    all_steps = tuple(step for step in window_steps if step.repository_id == repository_id)
    peer_index = build_step_peer_index(window_steps)
    plans = plan_summaries(current_runs, all_steps)
    if plan_id is not None and plan_id not in {plan.plan_id for plan in plans}:
        return None
    selected_runs = tuple(run for run in current_runs if plan_id is None or run.plan_id == plan_id)
    selected_steps = tuple(step for step in all_steps if plan_id is None or step.plan_id == plan_id)
    row = build_repository_rows(selected_runs, selected_steps)[0]
    goons = build_goon_rows(selected_steps)
    step_groups = group_steps(
        selected_steps, key=lambda item: (item.goon_name, item.goon_source_sha256, item.step_id)
    )
    step_summaries = tuple(
        step_summary(snapshot, window, group, repository_id=repository_id, peer_index=peer_index)
        for _, group in sorted(step_groups.items(), key=lambda item: item[0])
    )
    samples = repository_run_samples(selected_runs, selected_steps)
    return ScoutRepositoryDetail(
        enabled=enabled,
        journal_revision=snapshot.journal_revision,
        window=window,
        repository=row,
        selected_plan_id=plan_id,
        page_section=page_section,
        page_offset=page_offset,
        page_limit=page_limit,
        plan_total=len(plans),
        goon_total=len(goons),
        step_total=len(step_summaries),
        plans=_detail_slice(plans, active_section="plans", page_section=page_section, page_offset=page_offset, page_limit=page_limit, default_limit=SCOUT_DETAIL_ENTITY_LIMIT),
        goons=_detail_slice(tuple(sorted(goons, key=cpu_rank_key, reverse=True)), active_section="goons", page_section=page_section, page_offset=page_offset, page_limit=page_limit, default_limit=SCOUT_DETAIL_ENTITY_LIMIT),
        steps=_detail_slice(step_summaries, active_section="steps", page_section=page_section, page_offset=page_offset, page_limit=page_limit, default_limit=SCOUT_DETAIL_ENTITY_LIMIT),
        representative_runs=tuple(samples[:SCOUT_REPRESENTATIVE_RUN_LIMIT]),
    )


def project_scout_plan(
    snapshot: ScoutEvidenceSnapshot,
    *,
    window: ScoutWindow,
    plan_id: str,
    page_section: ScoutDetailSection | None = None,
    page_offset: int = 0,
    page_limit: int = SCOUT_DETAIL_ENTITY_LIMIT,
    enabled: bool = True,
) -> ScoutPlanDetail | None:
    runs = tuple(run for run in runs_in_window(snapshot.runs, window) if run.plan_id == plan_id)
    if not runs:
        return None
    steps = tuple(
        step for step in steps_in_window(snapshot.steps, window) if step.plan_id == plan_id
    )
    repository_rows = build_repository_rows(runs, steps)
    goon_rows = build_goon_rows(steps)
    step_groups = group_steps(
        steps, key=lambda item: (item.goon_name, item.goon_source_sha256, item.step_id)
    )
    step_summaries = tuple(
        step_summary(snapshot, window, group, repository_id=None)
        for _, group in sorted(step_groups.items(), key=lambda item: item[0])
    )
    latest = max(runs, key=lambda item: item.started_at)
    return ScoutPlanDetail(
        enabled=enabled,
        journal_revision=snapshot.journal_revision,
        window=window,
        plan_id=plan_id,
        plan_name=latest.plan_name or plan_id,
        page_section=page_section,
        page_offset=page_offset,
        page_limit=page_limit,
        repository_total=len(repository_rows),
        goon_total=len(goon_rows),
        step_total=len(step_summaries),
        repositories=_detail_slice(tuple(sorted(repository_rows, key=cpu_rank_key, reverse=True)), active_section="repositories", page_section=page_section, page_offset=page_offset, page_limit=page_limit, default_limit=SCOUT_DETAIL_ENTITY_LIMIT),
        goons=_detail_slice(tuple(sorted(goon_rows, key=cpu_rank_key, reverse=True)), active_section="goons", page_section=page_section, page_offset=page_offset, page_limit=page_limit, default_limit=SCOUT_DETAIL_ENTITY_LIMIT),
        steps=_detail_slice(step_summaries, active_section="steps", page_section=page_section, page_offset=page_offset, page_limit=page_limit, default_limit=SCOUT_DETAIL_ENTITY_LIMIT),
        representative_runs=tuple(
            repository_run_samples(runs, steps)[:SCOUT_REPRESENTATIVE_RUN_LIMIT]
        ),
    )


def _goon_run_samples(selected: tuple[ScoutStepObservation, ...]) -> tuple[ScoutRunSample, ...]:
    ranked = sorted(
        (
            ScoutRunSample(
                run_id=step.run_id,
                started_at=step.started_at,
                status=step.repository_status,
                cpu_seconds=step.complete_cpu_seconds,
                peak_memory_bytes=step.complete_peak_memory_bytes,
                duration_seconds=step.duration_seconds,
                average_cpu_cores=(step.complete_cpu_seconds / step.duration_seconds)
                if step.complete_cpu_seconds is not None
                and step.duration_seconds is not None
                and step.duration_seconds > 0
                else None,
                goon_name=step.goon_name,
                step_id=step.step_id,
            )
            for step in selected
        ),
        key=lambda item: (item.cpu_seconds is not None, item.cpu_seconds or 0.0, item.started_at),
        reverse=True,
    )
    unique: list[ScoutRunSample] = []
    seen: set[str] = set()
    for sample in ranked:
        if sample.run_id not in seen:
            seen.add(sample.run_id)
            unique.append(sample)
        if len(unique) == SCOUT_REPRESENTATIVE_RUN_LIMIT:
            break
    return tuple(unique)


def project_scout_goon(
    snapshot: ScoutEvidenceSnapshot,
    *,
    window: ScoutWindow,
    goon_name: str,
    source_sha256: str | None,
    page_section: ScoutDetailSection | None = None,
    page_offset: int = 0,
    page_limit: int = SCOUT_DETAIL_ENTITY_LIMIT,
    enabled: bool = True,
) -> ScoutGoonDetail | None:
    all_current = tuple(
        step for step in steps_in_window(snapshot.steps, window) if step.goon_name == goon_name
    )
    if not all_current:
        return None
    versions = build_goon_versions(all_current)
    selected = tuple(
        step
        for step in all_current
        if source_sha256 is None or step.goon_source_sha256 == source_sha256
    )
    if source_sha256 is not None and not selected:
        return None
    selected_run_repositories = {(step.run_id, step.repository_id) for step in selected}
    runs = tuple(
        run
        for run in runs_in_window(snapshot.runs, window)
        if (run.run_id, run.repository_id) in selected_run_repositories
    )
    repository_rows = build_repository_rows(runs, selected)
    step_groups = group_steps(
        selected, key=lambda item: (item.goon_name, item.goon_source_sha256, item.step_id)
    )
    steps = tuple(
        step_summary(snapshot, window, group, repository_id=None)
        for _, group in sorted(step_groups.items(), key=lambda item: item[0])
    )
    unique_samples = _goon_run_samples(selected)
    return ScoutGoonDetail(
        enabled=enabled,
        journal_revision=snapshot.journal_revision,
        window=window,
        goon_name=goon_name,
        selected_source_sha256=source_sha256,
        page_section=page_section,
        page_offset=page_offset,
        page_limit=page_limit,
        version_total=len(versions),
        repository_total=len(repository_rows),
        step_total=len(steps),
        versions=_detail_slice(versions, active_section="versions", page_section=page_section, page_offset=page_offset, page_limit=page_limit, default_limit=SCOUT_DETAIL_ENTITY_LIMIT),
        repositories=_detail_slice(tuple(sorted(repository_rows, key=cpu_rank_key, reverse=True)), active_section="repositories", page_section=page_section, page_offset=page_offset, page_limit=page_limit, default_limit=SCOUT_DETAIL_ENTITY_LIMIT),
        steps=_detail_slice(steps, active_section="steps", page_section=page_section, page_offset=page_offset, page_limit=page_limit, default_limit=SCOUT_DETAIL_ENTITY_LIMIT),
        representative_runs=tuple(unique_samples),
        trend=trend(runs, selected, window),
    )
