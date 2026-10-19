from __future__ import annotations

import hashlib
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

from oh_goon.core.execution_observability import RepositoryRunObservation
from oh_goon.core.models.explanation import ReasonCode
from oh_goon.core.models.fleet_executor import FleetExecutorPlacement, PlanExecutorCapacityEvidence
from oh_goon.core.models.fleet_plan import (
    ResolvedCommandStep,
    ResolvedFleetPlan,
    ResolvedImport,
)
from oh_goon.core.models.fleet_plan_evidence import (
    CompletedPlanStepExecution,
    FleetPlanResult,
    PlanEgressEvidence,
    PlanIsolationBackendEvidence,
    PlanOutputEvidence,
    PlanResourceEvidence,
    PlanStepAttemptEvidence,
)
from oh_goon.core.models.fleet_plan_execution_policy import CicdExecutionPolicy
from oh_goon.core.models.goon_plan_source import PlanFileSource
from oh_goon.core.models.operation import FleetPlanOperationEvidence
from oh_goon.core.models.repository import RepositoryIdentity
from oh_goon.core.models.run import CompletedFleetRun, FleetRunDetail, FleetRunProgress
from oh_goon.core.models.run_admission import StrictFleetExecutionAdmission, fleet_repository_scope
from oh_goon.core.models.target import FleetPlanTarget
from oh_goon.core.time import utc_now
from oh_goon.core.vocabulary import FleetRunStatus, GameTapeStatus, OperationAction, Verdict
from oh_goon.scout.models import ScoutWindow
from oh_goon.scout.projection import (
    ScoutEvidenceSnapshot,
    ScoutStepObservation,
    project_scout_evidence,
    project_scout_findings,
    project_scout_overview,
    project_scout_plan,
    project_scout_repository,
)
from tests.factories.capabilities import capability_snapshot
from tests.factories.fleet_plan_targets import structural_execution_identity
from tests.factories.operation_reports import make_operation_report
from tests.factories.run_admission import repository_admissions_for


def _output(text: str) -> PlanOutputEvidence:
    encoded = text.encode()
    return PlanOutputEvidence(
        sha256=hashlib.sha256(encoded).hexdigest(),
        byte_count=len(encoded),
        line_count=len(text.splitlines()),
    )


def _detail(
    tmp_path: Path,
    *,
    run_id: str = "run-scout",
    cpu_usage_usec: int | None = 2_500_000,
    memory_peak_bytes: int | None = 256 * 1024 * 1024,
) -> FleetRunDetail:
    repository = RepositoryIdentity(path=tmp_path / "repo")
    plan = ResolvedFleetPlan.create(
        name="scout-plan",
        description="",
        execution=CicdExecutionPolicy(),
        requirements=(),
        imports=(
            ResolvedImport(
                import_id="python-tests",
                name="python/python-tests",
                source_sha256="a" * 64,
                step_ids=("pytest",),
            ),
        ),
        steps=(ResolvedCommandStep(step_id="pytest", argv=("pytest",), timeout_seconds=30),),
    )
    target = FleetPlanTarget(
        kind="fleet_plan",
        plan=plan,
        source=PlanFileSource(sha256="0" * 64),
        identity=structural_execution_identity(plan),
    )
    now = utc_now()
    attempt = PlanStepAttemptEvidence(
        attempt=1,
        argv=("pytest",),
        returncode=0,
        stdout_evidence=_output("ok\n"),
        stderr_evidence=_output(""),
        duration_ms=1000,
        repository_state_before="before",
        repository_state_after="after",
        egress=PlanEgressEvidence(mode="none"),
        resources=PlanResourceEvidence(
            cpu_millis=2000,
            memory_bytes=512 * 1024 * 1024,
            cpu_usage_usec=cpu_usage_usec,
            memory_peak_bytes=memory_peak_bytes,
        ),
    )
    execution = CompletedPlanStepExecution(
        step_id="pytest",
        kind="command",
        started_at=now,
        finished_at=now + timedelta(seconds=1),
        argv=("pytest",),
        returncode=0,
        stdout_evidence=_output("ok\n"),
        stderr_evidence=_output(""),
        duration_ms=1000,
        repository_state_before="before",
        repository_state_after="after",
        attempts=(attempt,),
    )
    report = make_operation_report(
        repository,
        OperationAction.EXECUTE_PLAN,
        Verdict.READY,
        ReasonCode.READY,
        fleet_run_id=run_id,
        target=target,
        evidence=FleetPlanOperationEvidence(
            kind="FLEET_PLAN",
            plan=FleetPlanResult(plan_id=plan.plan_id, steps=(execution,)),
        ),
    )
    report = report.model_copy(
        update={
            "provenance": report.provenance.model_copy(
                update={"started_at": now, "finished_at": now + timedelta(seconds=1)}
            )
        }
    )
    run = CompletedFleetRun(
        status=FleetRunStatus.COMPLETED,
        fleet_run_id=run_id,
        target=target,
        capability=capability_snapshot(OperationAction.EXECUTE_PLAN, target),
        repository_scope=fleet_repository_scope(None),
        targets=(repository,),
        accepted_at=now,
        started_at=now,
        finished_at=now + timedelta(seconds=1),
        progress=FleetRunProgress(total=1, succeeded=1, failed=0, needs_attention=0),
    )
    backend = PlanIsolationBackendEvidence(
        backend_id="test-isolation",
        configuration_id="scout-test-isolation",
        kernel_boundary="shared_host_kernel",
        remote_execution=False,
        experimental=False,
    )
    capacity = PlanExecutorCapacityEvidence(
        fleet_run_id=run_id,
        pool_id="pool-scout",
        placement_policy="priority_rendezvous_fifo_v2",
        placement_rank=0,
        executor_priority=0,
        executor_id="local",
        executor_generation=1,
        source="local_verified",
        backend_configuration_id=backend.configuration_id,
        capacity_cpu_millis=8000,
        capacity_memory_bytes=8 * 1024 * 1024 * 1024,
        capacity_max_processes=512,
        requested_parallelism=1,
        admitted_parallelism=1,
        per_attempt_cpu_millis=2000,
        per_attempt_memory_bytes=512 * 1024 * 1024,
        per_attempt_max_processes=64,
    )
    return FleetRunDetail(
        repository_admissions=repository_admissions_for(run.target, run.targets),
        run=run,
        execution=StrictFleetExecutionAdmission(
            placement=FleetExecutorPlacement(
                isolation_backend=backend,
                executor_capacity=capacity,
                executor_trust_epoch=0,
            ),
        ),
        operations=(report,),
    )


def _window(now, hours: int = 24) -> ScoutWindow:
    return ScoutWindow(
        start_at=now - timedelta(hours=hours), end_at=now + timedelta(seconds=1), hours=hours
    )


def test_scout_projects_canonical_attempt_resources_and_goon_provenance(tmp_path: Path) -> None:
    detail = _detail(tmp_path)
    snapshot = project_scout_evidence((detail,), journal_revision=9)
    overview = project_scout_overview(snapshot, window=_window(detail.run.started_at), limit=20)

    assert snapshot.journal_revision == 9
    assert len(snapshot.runs) == 1
    assert len(snapshot.steps) == 1
    assert snapshot.steps[0].goon_name == "python/python-tests"
    assert snapshot.steps[0].goon_source_sha256 == "a" * 64
    assert snapshot.runs[0].duration_seconds == 1.0
    assert snapshot.steps[0].duration_seconds == 1.0
    assert overview.executions == 1
    assert overview.active_repositories == 1
    assert overview.totals.total_cpu_seconds == 2.5
    assert overview.totals.cpu_coverage.percent == 100
    assert overview.totals.peak_memory_bytes == 256 * 1024 * 1024
    assert overview.totals.memory_coverage.percent == 100
    assert overview.totals.memory_time_available is False
    assert overview.totals.memory_time_gib_seconds is None
    assert overview.heavy_hitters[0].median_cpu_seconds_per_run == 2.5
    assert overview.heavy_hitters[0].median_average_cpu_cores == 2.5
    assert overview.heavy_hitters[0].median_duration_seconds_per_run == 1.0
    assert overview.goon_leaders[0].goon_name == "python/python-tests"


def test_scout_plan_drill_down_reuses_canonical_plan_goon_step_evidence(tmp_path: Path) -> None:
    detail = _detail(tmp_path)
    snapshot = project_scout_evidence((detail,), journal_revision=10)
    plan_id = detail.run.target.plan.plan_id

    result = project_scout_plan(snapshot, window=_window(detail.run.started_at), plan_id=plan_id)

    assert result is not None
    assert result.plan_id == plan_id
    assert result.plan_name == "scout-plan"
    assert [row.repository_id for row in result.repositories] == [snapshot.runs[0].repository_id]
    assert [row.goon_name for row in result.goons] == ["python/python-tests"]
    assert [step.step_id for step in result.steps] == ["pytest"]
    assert result.steps[0].median_duration_seconds_per_run == 1.0
    assert result.steps[0].median_average_cpu_cores == 2.5
    assert result.representative_runs[0].run_id == "run-scout"
    assert result.representative_runs[0].duration_seconds == 1.0
    assert result.representative_runs[0].average_cpu_cores == 2.5


def test_scout_preserves_missing_cpu_instead_of_turning_it_into_zero(tmp_path: Path) -> None:
    detail = _detail(tmp_path, cpu_usage_usec=None)
    snapshot = project_scout_evidence((detail,), journal_revision=1)
    overview = project_scout_overview(snapshot, window=_window(detail.run.started_at))

    assert overview.totals.total_cpu_seconds is None
    assert overview.totals.cpu_coverage.observed == 0
    assert overview.totals.cpu_coverage.eligible == 1
    assert overview.heavy_hitters == ()
    assert overview.goon_leaders == ()


def _observation(
    *,
    repo: int,
    sample: int,
    now,
    cpu: float,
    memory: int = 128 * 1024 * 1024,
    source: str = "b" * 64,
    plan_id: str = "plan",
) -> ScoutStepObservation:
    return ScoutStepObservation(
        run_id=f"run-{repo}-{sample}",
        started_at=now - timedelta(minutes=sample),
        repository_id=f"repo-{repo}",
        repository_name=f"repo-{repo}",
        repository_status=GameTapeStatus.PASSED,
        plan_id=plan_id,
        plan_name=plan_id,
        goon_name="python/python-tests",
        goon_source_sha256=source,
        step_id="pytest",
        step_status=GameTapeStatus.PASSED,
        attempt_count=1,
        eligible_attempts=1,
        cpu_observed_attempts=1,
        memory_observed_attempts=1,
        observed_cpu_seconds=cpu,
        complete_cpu_seconds=cpu,
        observed_peak_memory_bytes=memory,
        complete_peak_memory_bytes=memory,
        environment_key=("local", "isolation", "c" * 64, "toolchain", ((2000, 536870912),)),
    )


def test_scout_overview_is_bounded_with_5000_repositories() -> None:
    now = utc_now()
    steps = tuple(
        _observation(repo=repo, sample=0, now=now, cpu=float((repo % 17) + 1))
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
            plan_id="plan",
            plan_name="plan",
        )
        for step in steps
    )
    overview = project_scout_overview(
        ScoutEvidenceSnapshot(journal_revision=5, runs=runs, steps=steps),
        window=_window(now),
        limit=20,
    )

    assert overview.active_repositories == 5000
    assert overview.heavy_hitter_total == 5000
    assert len(overview.heavy_hitters) == 20
    assert overview.goon_leader_total == 1
    assert len(overview.goon_leaders) == 1
    assert len(overview.trend) <= 25


def test_shared_run_counts_repository_executions_without_collapsing_fleet_run_id() -> None:
    now = utc_now()
    steps = tuple(
        ScoutStepObservation(
            run_id="shared-run",
            started_at=now,
            repository_id=f"repo-{repo}",
            repository_name=f"repo-{repo}",
            repository_status=GameTapeStatus.PASSED,
            plan_id="shared-plan",
            plan_name="shared-plan",
            goon_name="python/python-tests",
            goon_source_sha256="c" * 64,
            step_id="pytest",
            step_status=GameTapeStatus.PASSED,
            attempt_count=1,
            eligible_attempts=1,
            cpu_observed_attempts=1,
            memory_observed_attempts=1,
            observed_cpu_seconds=1.0,
            complete_cpu_seconds=1.0,
            observed_peak_memory_bytes=64 * 1024 * 1024,
            complete_peak_memory_bytes=64 * 1024 * 1024,
            environment_key=("local", "isolation", "c" * 64, "toolchain", ((1000, 268435456),)),
        )
        for repo in range(2)
    )
    runs = tuple(
        RepositoryRunObservation(
            run_id="shared-run",
            started_at=now,
            run_status=GameTapeStatus.PASSED,
            repository_id=step.repository_id,
            repository_name=step.repository_name,
            repository_status=GameTapeStatus.PASSED,
            plan_id="shared-plan",
            plan_name="shared-plan",
        )
        for step in steps
    )
    overview = project_scout_overview(
        ScoutEvidenceSnapshot(journal_revision=7, runs=runs, steps=steps),
        window=_window(now),
    )

    assert overview.executions == 2
    assert overview.trend[-1].run_count == 2
    assert overview.goon_leaders[0].run_count == 2


def test_repository_plan_drill_down_keeps_all_plans_visible_and_filters_execution_slice() -> None:
    now = utc_now()
    steps = tuple(
        _observation(
            repo=0,
            sample=sample,
            now=now,
            cpu=1.0 if sample < 2 else 5.0,
            plan_id="plan-a" if sample < 2 else "plan-b",
        )
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
        )
        for step in steps
    )
    snapshot = ScoutEvidenceSnapshot(journal_revision=8, runs=runs, steps=steps)

    all_plans = project_scout_repository(snapshot, window=_window(now), repository_id="repo-0")
    selected = project_scout_repository(
        snapshot, window=_window(now), repository_id="repo-0", plan_id="plan-b"
    )

    assert all_plans is not None
    assert [plan.plan_id for plan in all_plans.plans] == ["plan-b", "plan-a"]
    assert selected is not None
    assert selected.selected_plan_id == "plan-b"
    assert selected.repository.run_count == 2
    assert selected.repository.total_cpu_seconds == 10.0
    assert {plan.plan_id for plan in selected.plans} == {"plan-a", "plan-b"}


def test_step_peer_ratio_waits_for_explicit_peer_sample_floor() -> None:
    now = utc_now()
    insufficient_steps = tuple(
        _observation(repo=repo, sample=sample, now=now, cpu=10.0 if repo == 0 else 1.0)
        for repo in range(5)
        for sample in range(4)
    )
    insufficient_runs = tuple(
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
        for step in insufficient_steps
    )
    detail = project_scout_repository(
        ScoutEvidenceSnapshot(journal_revision=9, runs=insufficient_runs, steps=insufficient_steps),
        window=_window(now),
        repository_id="repo-0",
    )

    assert detail is not None
    step = detail.steps[0]
    assert step.peer_execution_count == 16
    assert step.peer_repository_count == 4
    assert step.peer_median_cpu_seconds_per_run is None
    assert step.cpu_ratio_to_peer_median is None

    sufficient_steps = tuple(
        _observation(repo=repo, sample=sample, now=now, cpu=10.0 if repo == 0 else 1.0)
        for repo in range(6)
        for sample in range(4)
    )
    sufficient_runs = tuple(
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
        for step in sufficient_steps
    )
    detail = project_scout_repository(
        ScoutEvidenceSnapshot(journal_revision=10, runs=sufficient_runs, steps=sufficient_steps),
        window=_window(now),
        repository_id="repo-0",
    )

    assert detail is not None
    step = detail.steps[0]
    assert step.peer_execution_count == 20
    assert step.peer_repository_count == 5
    assert step.peer_median_cpu_seconds_per_run == 1.0
    assert step.cpu_ratio_to_peer_median == 10.0


def test_trend_preserves_missing_resource_samples_instead_of_plotting_zero() -> None:
    now = utc_now()
    missing = replace(
        _observation(repo=0, sample=0, now=now, cpu=1.0),
        cpu_observed_attempts=0,
        memory_observed_attempts=0,
        observed_cpu_seconds=0.0,
        complete_cpu_seconds=None,
        observed_peak_memory_bytes=None,
        complete_peak_memory_bytes=None,
    )
    run = RepositoryRunObservation(
        run_id=missing.run_id,
        started_at=missing.started_at,
        run_status=GameTapeStatus.PASSED,
        repository_id=missing.repository_id,
        repository_name=missing.repository_name,
        repository_status=GameTapeStatus.PASSED,
        plan_id=missing.plan_id,
        plan_name=missing.plan_name,
    )
    overview = project_scout_overview(
        ScoutEvidenceSnapshot(journal_revision=11, runs=(run,), steps=(missing,)),
        window=_window(now),
    )
    observed_bucket = next(point for point in overview.trend if point.run_count == 1)

    assert observed_bucket.total_cpu_seconds is None
    assert observed_bucket.peak_memory_bytes is None
    assert observed_bucket.cpu_coverage.percent == 0
    assert observed_bucket.memory_coverage.percent == 0


def test_goon_regression_uses_equal_weight_per_repository_for_overlapping_population() -> None:
    now = utc_now()
    baseline_steps = []
    current_steps = []
    for repo in range(5):
        count = 100 if repo == 0 else 5
        baseline_cpu = 1.0 if repo == 0 else 10.0
        current_cpu = 1.0 if repo == 0 else 20.0
        baseline_steps.extend(
            _observation(
                repo=repo,
                sample=100 + sample + repo * 2,
                now=now - timedelta(hours=4),
                cpu=baseline_cpu,
                source="d" * 64,
            )
            for sample in range(count)
        )
        current_steps.extend(
            _observation(
                repo=repo,
                sample=sample,
                now=now,
                cpu=current_cpu,
                source="e" * 64,
            )
            for sample in range(count)
        )
    steps = tuple(baseline_steps + current_steps)
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
        ScoutEvidenceSnapshot(journal_revision=12, runs=runs, steps=steps),
        window=_window(now),
        category="GOON_REGRESSION",
        offset=0,
        limit=20,
    )

    assert page.total == 1
    finding = page.items[0]
    assert finding.baseline == 10.0
    assert finding.observed == 20.0
    assert finding.ratio == 2.0
    assert finding.peer_repository_count == 5
    assert finding.current_repository_count == 5
    assert finding.baseline_repository_count == 5
    assert finding.overlap_repository_count == 5
    assert finding.baseline_period_end_at < finding.current_period_start_at
    assert "Median per-repository CPU/run" in finding.explanation


def test_scout_keeps_stable_repository_and_plan_identity_across_renames() -> None:
    now = utc_now()
    old_step = replace(
        _observation(repo=0, sample=1, now=now, cpu=1.0),
        repository_id="repo-stable",
        repository_name="old-repository-name",
        plan_id="plan-stable",
        plan_name="Old Plan Name",
        started_at=now - timedelta(hours=1),
    )
    new_step = replace(
        _observation(repo=0, sample=0, now=now, cpu=2.0),
        repository_id="repo-stable",
        repository_name="new-repository-name",
        plan_id="plan-stable",
        plan_name="New Plan Name",
        started_at=now,
    )
    steps = (new_step, old_step)  # Deliberately not chronological.
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
    snapshot = ScoutEvidenceSnapshot(journal_revision=14, runs=runs, steps=steps)

    repository = project_scout_repository(
        snapshot, window=_window(now), repository_id="repo-stable"
    )
    plan = project_scout_plan(snapshot, window=_window(now), plan_id="plan-stable")

    assert repository is not None and repository.repository.display_name == "new-repository-name"
    assert repository.plan_total == 1 and repository.plans[0].plan_name == "New Plan Name"
    assert plan is not None and plan.plan_name == "New Plan Name"
