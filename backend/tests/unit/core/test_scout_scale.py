from dataclasses import replace
from datetime import timedelta

from oh_goon.core.execution_observability import RepositoryRunObservation
from oh_goon.scout.models import ScoutWindow
from oh_goon.scout.aggregation import goon_rows, goon_versions
from oh_goon.scout.evidence import (
    ScoutEvidenceSnapshot,
    ScoutStepObservation,
)
from oh_goon.scout.projection import (
    project_scout_findings,
    project_scout_goon,
    project_scout_goon_leaders,
    project_scout_plan,
    project_scout_repository,
)
from oh_goon.core.time import utc_now
from oh_goon.core.vocabulary import GameTapeStatus


def _window(now) -> ScoutWindow:
    return ScoutWindow(
        start_at=now - timedelta(hours=24), end_at=now + timedelta(seconds=1), hours=24
    )


def _observation(*, repo: int, now, cpu: float) -> ScoutStepObservation:
    return ScoutStepObservation(
        run_id=f"run-{repo}-0",
        started_at=now,
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


def test_goon_cpu_medians_require_consistent_passed_repository_status() -> None:
    now = utc_now()
    passed = _observation(repo=0, now=now, cpu=4.0)
    mixed = replace(
        passed,
        step_id="pytest-second",
        repository_status=GameTapeStatus.FAILED,
        observed_cpu_seconds=6.0,
        complete_cpu_seconds=6.0,
    )

    row = goon_rows((passed, mixed))[0]
    version = goon_versions((passed, mixed))[0]

    assert row.median_cpu_seconds_per_run is None
    assert version.median_cpu_seconds_per_run is None


def test_plan_and_goon_drill_down_bound_large_league_repository_lists() -> None:
    now = utc_now()
    steps = tuple(
        _observation(repo=repo, now=now, cpu=float((repo % 11) + 1)) for repo in range(5000)
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
        )
        for step in steps
    )
    snapshot = ScoutEvidenceSnapshot(journal_revision=13, runs=runs, steps=steps)

    plan = project_scout_plan(snapshot, window=_window(now), plan_id="plan")
    goon = project_scout_goon(
        snapshot,
        window=_window(now),
        goon_name="python/python-tests",
        source_sha256="b" * 64,
    )

    assert plan is not None
    assert plan.repository_total == 5000
    assert len(plan.repositories) == 100
    assert plan.step_total == 1
    assert len(plan.steps) == 1
    assert goon is not None
    assert goon.repository_total == 5000
    assert len(goon.repositories) == 100
    assert goon.step_total == 1
    assert len(goon.steps) == 1


def test_findings_remain_bounded_for_five_thousand_repository_peer_cohort() -> None:
    now = utc_now()
    steps = tuple(
        _observation(repo=repo, now=now, cpu=20.0 if repo == 0 else float((repo % 7) + 1))
        for repo in range(5000)
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
        )
        for step in steps
    )

    page = project_scout_findings(
        ScoutEvidenceSnapshot(journal_revision=14, runs=runs, steps=steps),
        window=_window(now),
        category="CPU_OUTLIER",
        offset=0,
        limit=20,
    )

    assert page.total >= 1
    assert len(page.items) <= 20
    assert page.items[0].repository_id == "repo-0"
    assert page.items[0].peer_repository_count == 4999


def test_repository_peer_drill_down_indexes_large_league_once_per_window() -> None:
    now = utc_now()
    base_steps = tuple(
        _observation(repo=repo, now=now, cpu=20.0 if repo == 0 else 1.0) for repo in range(5000)
    )
    steps = tuple(
        replace(item, step_id=f"step-{step_index}")
        for item in base_steps
        for step_index in range(20)
    )
    runs = tuple(
        RepositoryRunObservation(
            run_id=item.run_id,
            started_at=item.started_at,
            run_status=GameTapeStatus.PASSED,
            repository_id=item.repository_id,
            repository_name=item.repository_name,
            repository_status=GameTapeStatus.PASSED,
            plan_id=item.plan_id,
            plan_name=item.plan_name,
            duration_seconds=10.0,
        )
        for item in base_steps
    )

    detail = project_scout_repository(
        ScoutEvidenceSnapshot(journal_revision=15, runs=runs, steps=steps),
        window=_window(now),
        repository_id="repo-0",
    )

    assert detail is not None
    assert detail.step_total == 20
    assert len(detail.steps) == 20
    assert all(step.peer_repository_count == 4999 for step in detail.steps)
    assert all(step.peer_median_cpu_seconds_per_run == 1.0 for step in detail.steps)


def test_goon_leaders_paginate_beyond_overview_top_twenty() -> None:
    now = utc_now()
    steps = tuple(
        replace(
            _observation(repo=goon, now=now, cpu=float(100 - goon)),
            goon_name=f"goons/shared-{goon:02d}",
        )
        for goon in range(25)
    )
    snapshot = ScoutEvidenceSnapshot(journal_revision=16, runs=(), steps=steps)

    first = project_scout_goon_leaders(snapshot, window=_window(now), offset=0, limit=20)
    second = project_scout_goon_leaders(snapshot, window=_window(now), offset=20, limit=20)

    assert first.total == 25
    assert len(first.items) == 20
    assert second.total == 25
    assert len(second.items) == 5
    assert first.items[0].goon_name == "goons/shared-00"
    assert second.items[0].goon_name == "goons/shared-20"


def test_plan_and_goon_repository_pages_reach_rows_beyond_first_bound() -> None:
    now = utc_now()
    steps = tuple(
        _observation(repo=repo, now=now, cpu=float((repo % 11) + 1)) for repo in range(250)
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
        )
        for step in steps
    )
    snapshot = ScoutEvidenceSnapshot(journal_revision=17, runs=runs, steps=steps)

    first = project_scout_plan(
        snapshot, window=_window(now), plan_id="plan", page_section="repositories", page_offset=0, page_limit=100
    )
    third = project_scout_plan(
        snapshot, window=_window(now), plan_id="plan", page_section="repositories", page_offset=200, page_limit=100
    )
    goon_third = project_scout_goon(
        snapshot,
        window=_window(now),
        goon_name="python/python-tests",
        source_sha256="b" * 64,
        page_section="repositories",
        page_offset=200,
        page_limit=100,
    )

    assert first is not None and third is not None and goon_third is not None
    assert first.repository_total == third.repository_total == 250
    assert first.page_offset == 0
    assert third.page_offset == 200
    assert len(third.repositories) == 50
    assert {row.repository_id for row in first.repositories}.isdisjoint(
        {row.repository_id for row in third.repositories}
    )
    assert goon_third.repository_total == 250
    assert goon_third.page_offset == 200
    assert len(goon_third.repositories) == 50


def test_heavy_hitters_are_reachable_through_paginated_findings() -> None:
    now = utc_now()
    steps = tuple(
        _observation(repo=repo, now=now, cpu=float(100 - repo)) for repo in range(25)
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
        )
        for step in steps
    )
    snapshot = ScoutEvidenceSnapshot(journal_revision=18, runs=runs, steps=steps)

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
    assert first.items[0].repository_id == "repo-0"
