from __future__ import annotations

import hashlib
from datetime import timedelta
from pathlib import Path

from oh_goon.core.game_tape_performance import project_game_tape_performance
from oh_goon.core.models.explanation import ReasonCode
from oh_goon.core.models.fleet_plan import ResolvedCommandStep, ResolvedFleetPlan
from oh_goon.core.models.fleet_plan_evidence import (
    CompletedPlanStepExecution,
    FailedPlanStepExecution,
    FleetPlanResult,
    PlanOutputEvidence,
)
from oh_goon.core.models.fleet_plan_execution_policy import MaintenanceExecutionPolicy
from oh_goon.core.models.goon_plan_source import PlanFileSource
from oh_goon.core.models.operation import FleetPlanOperationEvidence
from oh_goon.core.models.repository import RepositoryIdentity
from oh_goon.core.models.run import CompletedFleetRun, FleetRunDetail, FleetRunProgress
from oh_goon.core.models.run_admission import DirectFleetExecutionAdmission, fleet_repository_scope
from oh_goon.core.models.target import FleetPlanTarget
from oh_goon.core.time import utc_now
from oh_goon.core.vocabulary import FleetRunStatus, OperationAction, Verdict
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


def _plan(name: str = "performance-test") -> ResolvedFleetPlan:
    return ResolvedFleetPlan.create(
        name=name,
        description="",
        execution=MaintenanceExecutionPolicy(),
        steps=(ResolvedCommandStep(step_id="test", argv=("pytest",), timeout_seconds=30),),
    )


def _detail(
    run_id: str,
    repository: RepositoryIdentity,
    plan: ResolvedFleetPlan,
    duration_ms: int,
    *,
    passed: bool = True,
) -> FleetRunDetail:
    target = FleetPlanTarget(
        kind="fleet_plan",
        plan=plan,
        source=PlanFileSource(sha256="0" * 64),
        identity=structural_execution_identity(plan),
    )
    now = utc_now()
    step_type = CompletedPlanStepExecution if passed else FailedPlanStepExecution
    step = step_type(
        step_id="test",
        kind="command",
        started_at=now,
        finished_at=now + timedelta(milliseconds=duration_ms),
        argv=("pytest",),
        returncode=0 if passed else 1,
        stdout_evidence=_output("ok\n" if passed else "failed\n"),
        stderr_evidence=_output("" if passed else "trace\n"),
        duration_ms=duration_ms,
        repository_state_before="before",
        repository_state_after="after" if passed else "before",
    )
    report = make_operation_report(
        repository,
        OperationAction.EXECUTE_PLAN,
        Verdict.READY if passed else Verdict.FAILED,
        ReasonCode.READY if passed else ReasonCode.TESTS_FAILED,
        fleet_run_id=run_id,
        target=target,
        returncode=0 if passed else 1,
        evidence=FleetPlanOperationEvidence(
            kind="FLEET_PLAN",
            plan=FleetPlanResult(plan_id=plan.plan_id, steps=(step,)),
        ),
    )
    report = report.model_copy(
        update={
            "provenance": report.provenance.model_copy(
                update={
                    "started_at": now,
                    "finished_at": now + timedelta(milliseconds=duration_ms),
                }
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
        finished_at=now + timedelta(milliseconds=duration_ms),
        progress=FleetRunProgress(
            total=1,
            succeeded=1 if passed else 0,
            failed=0 if passed else 1,
            needs_attention=0 if passed else 1,
        ),
    )
    return FleetRunDetail(
        repository_admissions=repository_admissions_for(run.target, run.targets),
        run=run,
        execution=DirectFleetExecutionAdmission(),
        operations=(report,),
    )


def test_performance_uses_newest_successful_same_plan_repository_samples(tmp_path: Path) -> None:
    repository = RepositoryIdentity(path=tmp_path / "repo")
    other_repository = RepositoryIdentity(path=tmp_path / "other")
    plan = _plan()
    unrelated_plan = _plan("other-plan")
    current = _detail("run-current", repository, plan, 2000)
    prior = (
        _detail("run-failed", repository, plan, 5000, passed=False),
        _detail("run-other-repo", other_repository, plan, 50),
        _detail("run-3", repository, plan, 1400),
        _detail("run-2", repository, plan, 1200),
        _detail("run-1", repository, plan, 1000),
        _detail("run-unrelated", repository, unrelated_plan, 100),
    )

    report = project_game_tape_performance(
        current.game_tape, tuple(item.game_tape for item in prior), sample_limit=3
    )

    assert report.history_candidates_considered == 6
    assert report.comparable_plan_runs == 5
    assert report.previous_comparable_run_id == "run-failed"
    assert report.run_timing.baseline.sample_run_ids == ("run-3", "run-2", "run-1")
    assert report.run_timing.baseline.median_duration_ms == 1200
    assert report.run_timing.delta_ms == 800
    assert report.run_timing.delta_percent == 67
    assert report.run_timing.direction == "SLOWER"
    repository_report = report.repositories[0]
    assert repository_report.timing.baseline.sample_run_ids == ("run-3", "run-2", "run-1")
    assert repository_report.largest_slowdown_step_id == "test"
    assert repository_report.largest_slowdown_percent == 67
    step = repository_report.steps[0]
    assert step.timing.baseline.minimum_duration_ms == 1000
    assert step.timing.baseline.maximum_duration_ms == 1400
    assert step.timing.baseline.sample_count == 3


def test_performance_reports_no_baseline_without_prior_successful_timing(tmp_path: Path) -> None:
    repository = RepositoryIdentity(path=tmp_path / "repo")
    current = _detail("run-current", repository, _plan(), 900)

    report = project_game_tape_performance(current.game_tape, (), sample_limit=10)

    assert report.previous_comparable_run_id is None
    assert report.run_timing.direction == "NO_BASELINE"
    assert report.run_timing.baseline.sample_count == 0
    assert report.run_timing.delta_ms is None
    assert report.repositories[0].steps[0].timing.direction == "NO_BASELINE"


def test_game_tape_comparison_cut_reads_one_journal_view_and_projects_each_run_once(
    tmp_path: Path, monkeypatch
) -> None:
    import oh_goon.core.game_tape_projection as projection
    from oh_goon.services.fleet_run_control_plane import _game_tape_comparison_cut

    repository = RepositoryIdentity(path=tmp_path / "repo")
    plan = _plan()
    baseline = _detail("run-baseline", repository, plan, 1000)
    compared = _detail("run-compared", repository, plan, 1200)
    details = {
        baseline.run.fleet_run_id: baseline,
        compared.run.fleet_run_id: compared,
    }
    projections: list[str] = []
    read_views = 0
    original = projection.project_game_tape

    def counted(detail):
        projections.append(detail.run.fleet_run_id)
        return original(detail)

    monkeypatch.setattr(projection, "project_game_tape", counted)

    class View:
        def required_run_detail(self, fleet_run_id: str):
            return details[fleet_run_id]

    class JournalStub:
        def read_view(self):
            nonlocal read_views
            read_views += 1
            return View()

    base_tape, compared_tape = _game_tape_comparison_cut(
        JournalStub(), "run-baseline", "run-compared"
    )

    assert base_tape.fleet_run_id == "run-baseline"
    assert compared_tape.fleet_run_id == "run-compared"
    assert read_views == 1
    assert projections == ["run-baseline", "run-compared"]


def test_game_tape_comparison_cut_reuses_one_projection_for_same_run(
    tmp_path: Path, monkeypatch
) -> None:
    import oh_goon.core.game_tape_projection as projection
    from oh_goon.services.fleet_run_control_plane import _game_tape_comparison_cut

    repository = RepositoryIdentity(path=tmp_path / "repo")
    detail = _detail("run-current", repository, _plan(), 1000)
    projections: list[str] = []
    original = projection.project_game_tape

    def counted(item):
        projections.append(item.run.fleet_run_id)
        return original(item)

    monkeypatch.setattr(projection, "project_game_tape", counted)

    class View:
        def required_run_detail(self, fleet_run_id: str):
            assert fleet_run_id == "run-current"
            return detail

    class JournalStub:
        def read_view(self):
            return View()

    baseline, compared = _game_tape_comparison_cut(JournalStub(), "run-current", "run-current")

    assert baseline is compared
    assert projections == ["run-current"]


def test_game_tape_history_cut_projects_only_needed_run_details_once(
    tmp_path: Path, monkeypatch
) -> None:
    import oh_goon.core.game_tape_projection as projection
    from oh_goon.services.fleet_run_control_plane import _game_tape_history_cut

    repository = RepositoryIdentity(path=tmp_path / "repo")
    plan = _plan()
    prior_far = _detail("run-prior-far", repository, plan, 1000)
    prior_near = _detail("run-prior-near", repository, plan, 1200)
    current = _detail("run-current", repository, plan, 1400)
    calls: list[str] = []
    original = projection.project_game_tape

    def counted(detail):
        calls.append(detail.run.fleet_run_id)
        return original(detail)

    monkeypatch.setattr(projection, "project_game_tape", counted)

    class View:
        def run_detail_window(self, fleet_run_id: str, *, before_limit: int, after_limit: int):
            assert fleet_run_id == "run-current"
            assert before_limit == 10
            assert after_limit == 0
            return current, (prior_near, prior_far), ()

    class JournalStub:
        def read_view(self):
            return View()

    selected, prior = _game_tape_history_cut(JournalStub(), "run-current")

    assert selected.fleet_run_id == "run-current"
    assert [item.fleet_run_id for item in prior] == ["run-prior-near", "run-prior-far"]
    assert calls == ["run-current", "run-prior-near", "run-prior-far"]


def test_game_tape_history_window_projects_each_selected_run_once(
    tmp_path: Path, monkeypatch
) -> None:
    import oh_goon.core.game_tape_projection as projection
    from oh_goon.services.fleet_run_control_plane import _game_tape_history_window

    repository = RepositoryIdentity(path=tmp_path / "repo")
    plan = _plan()
    prior = _detail("run-prior", repository, plan, 1000)
    current = _detail("run-current", repository, plan, 1200)
    later = _detail("run-later", repository, plan, 1400)
    calls: list[str] = []
    original = projection.project_game_tape

    def counted(detail):
        calls.append(detail.run.fleet_run_id)
        return original(detail)

    monkeypatch.setattr(projection, "project_game_tape", counted)

    class View:
        def run_detail_window(self, fleet_run_id: str, *, before_limit: int, after_limit: int):
            assert fleet_run_id == "run-current"
            assert before_limit == 10
            assert after_limit == 10
            return current, (prior,), (later,)

    class JournalStub:
        def read_view(self):
            return View()

    selected, before, after = _game_tape_history_window(JournalStub(), "run-current")

    assert selected.fleet_run_id == "run-current"
    assert [item.fleet_run_id for item in before] == ["run-prior"]
    assert [item.fleet_run_id for item in after] == ["run-later"]
    assert calls == ["run-current", "run-prior", "run-later"]
