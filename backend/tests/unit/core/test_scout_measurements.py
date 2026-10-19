from __future__ import annotations

from dataclasses import replace
from typing import get_args

from oh_goon.core.execution_observability import RepositoryRunObservation
from oh_goon.core.time import utc_now
from oh_goon.core.vocabulary import GameTapeStatus
from oh_goon.scout.evidence import ScoutEvidenceSnapshot
from oh_goon.scout.models import ScoutMetricName
from oh_goon.scout.projection import (
    project_scout_findings,
    project_scout_goon,
    project_scout_overview,
    project_scout_repository,
    scout_metric_dictionary,
)
from tests.unit.core.test_scout_projection import _observation, _window


def test_typical_cpu_metrics_exclude_unsuccessful_executions_but_totals_keep_their_cpu() -> None:
    now = utc_now()
    passed = replace(_observation(repo=0, sample=0, now=now, cpu=1.0), duration_seconds=1.0)
    failed = replace(
        _observation(repo=0, sample=1, now=now, cpu=101.0),
        repository_status=GameTapeStatus.FAILED,
        step_status=GameTapeStatus.FAILED,
        duration_seconds=1.0,
    )
    steps = (passed, failed)
    runs = (
        RepositoryRunObservation(
            run_id=passed.run_id,
            started_at=passed.started_at,
            run_status=GameTapeStatus.PASSED,
            repository_id=passed.repository_id,
            repository_name=passed.repository_name,
            repository_status=GameTapeStatus.PASSED,
            plan_id=passed.plan_id,
            plan_name=passed.plan_name,
            duration_seconds=1.0,
        ),
        RepositoryRunObservation(
            run_id=failed.run_id,
            started_at=failed.started_at,
            run_status=GameTapeStatus.FAILED,
            repository_id=failed.repository_id,
            repository_name=failed.repository_name,
            repository_status=GameTapeStatus.FAILED,
            plan_id=failed.plan_id,
            plan_name=failed.plan_name,
            duration_seconds=1.0,
        ),
    )

    overview = project_scout_overview(
        ScoutEvidenceSnapshot(journal_revision=15, runs=runs, steps=steps),
        window=_window(now),
    )

    repository = overview.heavy_hitters[0]
    assert repository.total_cpu_seconds == 102.0
    assert repository.failed_cpu_seconds == 101.0
    assert repository.median_cpu_seconds_per_run == 1.0
    assert repository.median_average_cpu_cores == 1.0
    assert repository.median_duration_seconds_per_run == 1.0
    assert overview.goon_leaders[0].median_cpu_seconds_per_run == 1.0


def test_cancelled_cpu_is_failure_waste_but_not_typical_run_intensity() -> None:
    now = utc_now()
    passed = replace(_observation(repo=0, sample=0, now=now, cpu=2.0), duration_seconds=1.0)
    cancelled = replace(
        _observation(repo=0, sample=1, now=now, cpu=50.0),
        repository_status=GameTapeStatus.CANCELLED,
        step_status=GameTapeStatus.CANCELLED,
        duration_seconds=1.0,
    )
    runs = (
        RepositoryRunObservation(
            run_id=passed.run_id,
            started_at=passed.started_at,
            run_status=GameTapeStatus.PASSED,
            repository_id=passed.repository_id,
            repository_name=passed.repository_name,
            repository_status=GameTapeStatus.PASSED,
            plan_id=passed.plan_id,
            plan_name=passed.plan_name,
            duration_seconds=1.0,
        ),
        RepositoryRunObservation(
            run_id=cancelled.run_id,
            started_at=cancelled.started_at,
            run_status=GameTapeStatus.CANCELLED,
            repository_id=cancelled.repository_id,
            repository_name=cancelled.repository_name,
            repository_status=GameTapeStatus.CANCELLED,
            plan_id=cancelled.plan_id,
            plan_name=cancelled.plan_name,
            duration_seconds=1.0,
        ),
    )
    snapshot = ScoutEvidenceSnapshot(journal_revision=16, runs=runs, steps=(passed, cancelled))

    overview = project_scout_overview(snapshot, window=_window(now))
    waste = project_scout_findings(
        snapshot, window=_window(now), category="FAILURE_WASTE", offset=0, limit=20
    )

    row = overview.heavy_hitters[0]
    assert row.total_cpu_seconds == 52.0
    assert row.cancelled_cpu_seconds == 50.0
    assert row.median_cpu_seconds_per_run == 2.0
    assert waste.total == 1
    assert waste.items[0].observed == 50.0
    assert waste.items[0].failed_cpu_seconds == 0.0
    assert waste.items[0].cancelled_cpu_seconds == 50.0
    assert waste.items[0].sample_count == 1
    assert waste.items[0].coverage is not None and waste.items[0].coverage.percent == 100.0
    assert waste.items[0].representative_run_id == cancelled.run_id
    assert "cancelled/interrupted" in waste.items[0].explanation


def test_step_peer_output_exposes_the_exact_compatible_environment() -> None:
    now = utc_now()
    steps = tuple(
        replace(
            _observation(repo=repo, sample=sample, now=now, cpu=10.0 if repo == 0 else 1.0),
            duration_seconds=1.0,
        )
        for repo in range(6)
        for sample in range(4)
    )
    runs = tuple(
        RepositoryRunObservation(
            run_id=step.run_id,
            started_at=step.started_at,
            run_status=GameTapeStatus.PASSED,
            repository_id=step.repository_id,
            repository_name=step.repository_name,
            repository_status=GameTapeStatus.PASSED,
            plan_id=step.plan_id,
            plan_name=step.plan_name,
            duration_seconds=1.0,
        )
        for step in steps
    )
    detail = project_scout_repository(
        ScoutEvidenceSnapshot(journal_revision=17, runs=runs, steps=steps),
        window=_window(now),
        repository_id="repo-0",
    )

    assert detail is not None
    environment = detail.steps[0].comparison_environment
    assert environment is not None
    assert environment.isolation_backend_id == "local"
    assert environment.isolation_configuration_id == "isolation"
    assert environment.runtime_root_sha256 == "c" * 64
    assert environment.host_toolchain_id == "toolchain"
    assert environment.resource_envelopes[0].cpu_millis == 2000
    assert environment.resource_envelopes[0].memory_bytes == 536870912


def test_goon_drill_down_exposes_bounded_resource_trend() -> None:
    now = utc_now()
    steps = tuple(
        replace(
            _observation(repo=repo, sample=0, now=now, cpu=float(repo + 1)), duration_seconds=1.0
        )
        for repo in range(2)
    )
    runs = tuple(
        RepositoryRunObservation(
            run_id=step.run_id,
            started_at=step.started_at,
            run_status=GameTapeStatus.PASSED,
            repository_id=step.repository_id,
            repository_name=step.repository_name,
            repository_status=GameTapeStatus.PASSED,
            plan_id=step.plan_id,
            plan_name=step.plan_name,
            duration_seconds=1.0,
        )
        for step in steps
    )
    detail = project_scout_goon(
        ScoutEvidenceSnapshot(journal_revision=18, runs=runs, steps=steps),
        window=_window(now),
        goon_name="python/python-tests",
        source_sha256="b" * 64,
    )

    assert detail is not None
    assert detail.trend
    assert sum(point.run_count for point in detail.trend) == 2
    assert sum(point.total_cpu_seconds or 0 for point in detail.trend) == 3.0


def test_metric_dictionary_defines_every_finding_metric() -> None:
    dictionary = scout_metric_dictionary()
    defined = {item.metric for item in dictionary.definitions}

    assert set(get_args(ScoutMetricName)) <= defined
    assert {"run_frequency", "failure_cpu"} <= defined


def test_overview_failure_rate_does_not_count_cancelled_or_interrupted_as_failures() -> None:
    now = utc_now()
    statuses = (
        GameTapeStatus.PASSED,
        GameTapeStatus.FAILED,
        GameTapeStatus.ATTENTION_REQUIRED,
        GameTapeStatus.CANCELLED,
        GameTapeStatus.INTERRUPTED,
    )
    runs = tuple(
        RepositoryRunObservation(
            run_id=f"status-{status.value}",
            started_at=now,
            run_status=status,
            repository_id=f"repo-{index}",
            repository_name=f"repo-{index}",
            repository_status=status,
            plan_id="plan",
            plan_name="plan",
        )
        for index, status in enumerate(statuses)
    )

    overview = project_scout_overview(
        ScoutEvidenceSnapshot(journal_revision=99, runs=runs, steps=()),
        window=_window(now),
    )

    assert overview.failure_rate_percent == 20.0
