from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

from oh_goon.core.execution_observability import RepositoryRunObservation
from oh_goon.scout.models import ScoutWindow
from oh_goon.scout.evidence import (
    ScoutEvidenceSnapshot,
    ScoutStepObservation,
)
from oh_goon.scout.projection import project_scout_findings
from oh_goon.core.time import utc_now
from oh_goon.core.vocabulary import GameTapeStatus


def _window(now, hours: int = 24) -> ScoutWindow:
    return ScoutWindow(
        start_at=now - timedelta(hours=hours),
        end_at=now + timedelta(seconds=1),
        hours=hours,
    )


def _observation(*, repo: int, sample: int, now, cpu: float) -> ScoutStepObservation:
    return ScoutStepObservation(
        run_id=f"run-{repo}-{sample}",
        started_at=now - timedelta(minutes=sample),
        repository_id=f"repo-{repo}",
        repository_name=f"repo-{repo}",
        repository_status=GameTapeStatus.PASSED,
        plan_id="plan",
        plan_name="plan",
        goon_name="python/python-tests",
        goon_source_sha256="b" * 64,
        step_id="pytest",
        step_status=GameTapeStatus.PASSED,
        attempt_count=1,
        eligible_attempts=1,
        cpu_observed_attempts=1,
        memory_observed_attempts=1,
        observed_cpu_seconds=cpu,
        complete_cpu_seconds=cpu,
        observed_peak_memory_bytes=128 * 1024 * 1024,
        complete_peak_memory_bytes=128 * 1024 * 1024,
        environment_key=("local", "isolation", "c" * 64, "toolchain", ((2000, 536870912),)),
    )


def _runs(steps: tuple[ScoutStepObservation, ...]) -> tuple[RepositoryRunObservation, ...]:
    return tuple(
        RepositoryRunObservation(
            run_id=step.run_id,
            started_at=step.started_at,
            run_status=GameTapeStatus.PASSED,
            repository_id=step.repository_id,
            repository_name=step.repository_name,
            repository_status=GameTapeStatus.PASSED,
            plan_id=step.plan_id,
            plan_name=step.plan_name,
        )
        for step in steps
    )


def test_cpu_outlier_requires_complete_peer_sample_floor_and_reports_peer_median() -> None:
    now = utc_now()
    steps = tuple(
        _observation(repo=repo, sample=sample, now=now, cpu=10.0 if repo == 0 else 1.0)
        for repo in range(6)
        for sample in range(4)
    )
    page = project_scout_findings(
        ScoutEvidenceSnapshot(journal_revision=2, runs=_runs(steps), steps=steps),
        window=_window(now),
        category="CPU_OUTLIER",
        offset=0,
        limit=20,
    )

    assert page.total == 1
    finding = page.items[0]
    assert finding.repository_id == "repo-0"
    assert finding.observed == 10
    assert finding.baseline == 1
    assert finding.ratio == 10
    assert finding.peer_repository_count == 5
    assert finding.sample_count == 20
    assert finding.comparison_environment is not None
    assert finding.comparison_environment.isolation_backend_id == "local"
    assert finding.comparison_environment.isolation_configuration_id == "isolation"
    assert finding.comparison_environment.runtime_root_sha256 == "c" * 64
    assert finding.comparison_environment.host_toolchain_id == "toolchain"
    assert finding.comparison_environment.resource_envelopes[0].cpu_millis == 2000
    assert finding.comparison_environment.resource_envelopes[0].memory_bytes == 536870912
    assert "not its cause" in finding.explanation


def test_cpu_outlier_is_withheld_when_target_pushes_cohort_over_sample_floor() -> None:
    now = utc_now()
    steps = tuple(
        _observation(repo=repo, sample=sample, now=now, cpu=10.0 if repo == 0 else 1.0)
        for repo in range(5)
        for sample in range(4)
    )

    page = project_scout_findings(
        ScoutEvidenceSnapshot(journal_revision=3, runs=_runs(steps), steps=steps),
        window=_window(now),
        category="CPU_OUTLIER",
        offset=0,
        limit=20,
    )

    assert page.total == 0


def test_run_spike_is_withheld_when_required_history_exceeds_retention() -> None:
    now = utc_now()
    current = tuple(
        RepositoryRunObservation(
            run_id=f"current-{sample}",
            started_at=now - timedelta(hours=1),
            run_status=GameTapeStatus.PASSED,
            repository_id="repo-0",
            repository_name="repo-0",
            repository_status=GameTapeStatus.PASSED,
            plan_id="plan",
            plan_name="plan",
        )
        for sample in range(10)
    )
    historical = tuple(
        RepositoryRunObservation(
            run_id=f"history-{period}",
            started_at=now - timedelta(days=90 * period + 1),
            run_status=GameTapeStatus.PASSED,
            repository_id="repo-0",
            repository_name="repo-0",
            repository_status=GameTapeStatus.PASSED,
            plan_id="plan",
            plan_name="plan",
        )
        for period in range(1, 5)
    )
    window = ScoutWindow(
        start_at=now - timedelta(days=90),
        end_at=now,
        hours=24 * 90,
    )

    page = project_scout_findings(
        ScoutEvidenceSnapshot(journal_revision=4, runs=current + historical, steps=()),
        window=window,
        category="RUN_SPIKE",
        offset=0,
        limit=20,
    )

    assert page.total == 0


def test_memory_outlier_requires_ninety_percent_peer_coverage() -> None:
    now = utc_now()
    complete = tuple(
        _observation(repo=repo, sample=sample, now=now, cpu=1.0)
        for repo in range(7)
        for sample in range(4)
    )
    high_target = tuple(
        replace(
            item,
            observed_peak_memory_bytes=2 * 1024**3,
            complete_peak_memory_bytes=2 * 1024**3,
        )
        if item.repository_id == "repo-0"
        else item
        for item in complete
    )
    peer_keys = {("repo-1", "run-1-0"), ("repo-2", "run-2-0"), ("repo-3", "run-3-0")}
    low_coverage = tuple(
        replace(
            item,
            memory_observed_attempts=0,
            observed_peak_memory_bytes=None,
            complete_peak_memory_bytes=None,
        )
        if (item.repository_id, item.run_id) in peer_keys
        else item
        for item in high_target
    )

    hidden = project_scout_findings(
        ScoutEvidenceSnapshot(journal_revision=5, runs=_runs(low_coverage), steps=low_coverage),
        window=_window(now),
        category="MEMORY_OUTLIER",
        offset=0,
        limit=20,
    )
    assert hidden.total == 0

    sufficient_coverage = tuple(
        replace(
            item,
            memory_observed_attempts=1,
            observed_peak_memory_bytes=128 * 1024**2,
            complete_peak_memory_bytes=128 * 1024**2,
        )
        if item.repository_id == "repo-3" and item.run_id == "run-3-0"
        else item
        for item in low_coverage
    )
    visible = project_scout_findings(
        ScoutEvidenceSnapshot(
            journal_revision=6,
            runs=_runs(sufficient_coverage),
            steps=sufficient_coverage,
        ),
        window=_window(now),
        category="MEMORY_OUTLIER",
        offset=0,
        limit=20,
    )

    assert visible.total == 1
    assert visible.items[0].repository_id == "repo-0"
    assert visible.items[0].coverage is not None
    assert visible.items[0].coverage.percent > 90


def test_run_spike_uses_seven_own_history_periods_for_supported_window() -> None:
    now = utc_now()
    window = ScoutWindow(
        start_at=now - timedelta(days=30),
        end_at=now,
        hours=24 * 30,
    )
    current = tuple(
        RepositoryRunObservation(
            run_id=f"current-{sample}",
            started_at=now - timedelta(days=1),
            run_status=GameTapeStatus.PASSED,
            repository_id="repo-0",
            repository_name="repo-0",
            repository_status=GameTapeStatus.PASSED,
            plan_id="plan",
            plan_name="plan",
        )
        for sample in range(10)
    )
    history = tuple(
        RepositoryRunObservation(
            run_id=f"history-{period}-{sample}",
            started_at=window.start_at - timedelta(days=30 * (period - 1) + 1),
            run_status=GameTapeStatus.PASSED,
            repository_id="repo-0",
            repository_name="repo-0",
            repository_status=GameTapeStatus.PASSED,
            plan_id="plan",
            plan_name="plan",
        )
        for period in range(1, 8)
        for sample in range(2)
    )

    history_proof = RepositoryRunObservation(
        run_id="history-proof",
        started_at=window.start_at - timedelta(days=30 * 7 + 1),
        run_status=GameTapeStatus.PASSED,
        repository_id="repo-0",
        repository_name="repo-0",
        repository_status=GameTapeStatus.PASSED,
        plan_id="plan",
        plan_name="plan",
    )

    page = project_scout_findings(
        ScoutEvidenceSnapshot(
            journal_revision=7, runs=current + history + (history_proof,), steps=()
        ),
        window=window,
        category="RUN_SPIKE",
        offset=0,
        limit=20,
    )

    assert page.total == 1
    finding = page.items[0]
    assert finding.observed == 10
    assert finding.baseline == 2
    assert finding.ratio == 5
    assert finding.sample_count == 7


def test_run_spike_requires_repository_history_before_oldest_baseline_period() -> None:
    now = utc_now()
    window = ScoutWindow(start_at=now - timedelta(days=30), end_at=now, hours=24 * 30)
    current = tuple(
        RepositoryRunObservation(
            run_id=f"current-short-history-{sample}",
            started_at=now - timedelta(days=1),
            run_status=GameTapeStatus.PASSED,
            repository_id="repo-new",
            repository_name="repo-new",
            repository_status=GameTapeStatus.PASSED,
            plan_id="plan",
            plan_name="plan",
        )
        for sample in range(10)
    )
    history = tuple(
        RepositoryRunObservation(
            run_id=f"short-history-{period}-{sample}",
            started_at=window.start_at - timedelta(days=30 * (period - 1) + 1),
            run_status=GameTapeStatus.PASSED,
            repository_id="repo-new",
            repository_name="repo-new",
            repository_status=GameTapeStatus.PASSED,
            plan_id="plan",
            plan_name="plan",
        )
        for period in range(1, 8)
        for sample in range(2)
    )

    page = project_scout_findings(
        ScoutEvidenceSnapshot(journal_revision=71, runs=current + history, steps=()),
        window=window,
        category="RUN_SPIKE",
        offset=0,
        limit=20,
    )

    assert page.total == 0


def test_cpu_outlier_uses_exact_leave_one_repository_out_peer_median() -> None:
    now = utc_now()
    cpu_by_repo = {0: 20.0, 1: 1.0, 2: 2.0, 3: 3.0, 4: 4.0, 5: 5.0, 6: 6.0}
    steps = tuple(
        _observation(repo=repo, sample=sample, now=now, cpu=cpu_by_repo[repo])
        for repo in range(7)
        for sample in range(4)
    )

    page = project_scout_findings(
        ScoutEvidenceSnapshot(journal_revision=8, runs=_runs(steps), steps=steps),
        window=_window(now),
        category="CPU_OUTLIER",
        offset=0,
        limit=20,
    )

    target = next(item for item in page.items if item.repository_id == "repo-0")
    assert target.observed == 20
    assert target.baseline == 3.5
    assert target.ratio == 20 / 3.5
    assert target.peer_repository_count == 6
    assert target.sample_count == 24


def test_cpu_outlier_does_not_merge_materially_different_resource_envelopes() -> None:
    now = utc_now()
    steps = []
    for repo in range(6):
        envelope = ((2000, 536870912),) if repo < 3 else ((4000, 1073741824),)
        for sample in range(4):
            steps.append(
                replace(
                    _observation(repo=repo, sample=sample, now=now, cpu=10.0 if repo == 0 else 1.0),
                    environment_key=("local", "isolation", "c" * 64, "toolchain", envelope),
                )
            )
    observations = tuple(steps)

    page = project_scout_findings(
        ScoutEvidenceSnapshot(journal_revision=9, runs=_runs(observations), steps=observations),
        window=_window(now),
        category="CPU_OUTLIER",
        offset=0,
        limit=20,
    )

    assert page.total == 0


def test_cpu_outlier_pagination_counts_all_qualifying_findings_beyond_first_hundred() -> None:
    now = utc_now()
    steps = tuple(
        replace(
            _observation(
                repo=cohort * 6 + repo, sample=sample, now=now, cpu=10.0 if repo == 0 else 1.0
            ),
            step_id=f"pytest-{cohort}",
        )
        for cohort in range(101)
        for repo in range(6)
        for sample in range(4)
    )
    snapshot = ScoutEvidenceSnapshot(journal_revision=10, runs=_runs(steps), steps=steps)

    first = project_scout_findings(
        snapshot, window=_window(now), category="CPU_OUTLIER", offset=0, limit=20
    )
    last = project_scout_findings(
        snapshot, window=_window(now), category="CPU_OUTLIER", offset=100, limit=20
    )

    assert first.total == 101
    assert len(first.items) == 20
    assert last.total == 101
    assert len(last.items) == 1


def test_heavy_hitter_ranking_pages_beyond_initial_twenty() -> None:
    now = utc_now()
    steps = tuple(
        _observation(repo=repo, sample=0, now=now, cpu=float(100 - repo)) for repo in range(25)
    )
    snapshot = ScoutEvidenceSnapshot(journal_revision=11, runs=_runs(steps), steps=steps)

    first = project_scout_findings(
        snapshot, window=_window(now), category="HEAVY_HITTER", offset=0, limit=20
    )
    second = project_scout_findings(
        snapshot, window=_window(now), category="HEAVY_HITTER", offset=20, limit=20
    )

    assert first.total == 25
    assert len(first.items) == 20
    assert second.total == 25
    assert len(second.items) == 5
    assert {item.repository_id for item in first.items}.isdisjoint(
        item.repository_id for item in second.items
    )


def test_overview_style_finding_counts_are_not_capped_by_ranked_preview() -> None:
    from oh_goon.scout.aggregation import repository_rows
    from oh_goon.scout.findings import all_findings, finding_counts

    now = utc_now()
    steps = tuple(
        _observation(repo=repo, sample=0, now=now, cpu=float(100 - repo)) for repo in range(25)
    )
    snapshot = ScoutEvidenceSnapshot(journal_revision=12, runs=_runs(steps), steps=steps)
    window = _window(now)
    rows = repository_rows(snapshot.runs, snapshot.steps)
    preview = all_findings(snapshot, window, snapshot.runs, snapshot.steps, rows)
    counts = {item.category: item.count for item in finding_counts(preview, rows)}

    assert sum(1 for item in preview if item.category == "HEAVY_HITTER") == 20
    assert counts["HEAVY_HITTER"] == 25
