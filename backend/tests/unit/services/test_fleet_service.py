from __future__ import annotations

from pathlib import Path

from oh_goon.core.errors import TargetSelectionError
from oh_goon.core.models.explanation import ReasonCode
from oh_goon.core.models.operation import OperationExecution
from oh_goon.core.models.target import OperationTarget
from oh_goon.core.vocabulary import (
    FleetRunOutcome,
    FleetScope,
    OperationAction,
    ReadinessState,
    Verdict,
)
from oh_goon.runtime.database import LocalDatabase
from oh_goon.services.fleet_run_control_plane import FleetRunControlPlane
from tests.factories.local_state import local_state
from tests.factories.operation_reports import make_operation_report
from tests.factories.runtime_stack import direct_operation_stack, fleet_stack
from tests.fakes.capability_runners import CommandRunnerStub


class ReadyRunner:
    def run(
        self,
        repository: Path,
        action: OperationAction,
        *,
        fleet_run_id: str | None = None,
        cancel_requested: object = None,
    ) -> OperationExecution:
        report = make_operation_report(repository, action, fleet_run_id=fleet_run_id)
        return OperationExecution(stdout="", stderr="", report=report)


class TargetAwareReadyRunner:
    def run(
        self,
        repository: Path,
        action: OperationAction,
        *,
        fleet_run_id: str | None = None,
        target: OperationTarget | None = None,
        cancel_requested: object = None,
    ) -> OperationExecution:
        del cancel_requested
        report = make_operation_report(repository, action, target=target, fleet_run_id=fleet_run_id)
        return OperationExecution(stdout="", stderr="", report=report)


class PlanFailureRunner(ReadyRunner):
    def run(
        self,
        repository: Path,
        action: OperationAction,
        *,
        fleet_run_id: str | None = None,
        cancel_requested: object = None,
    ) -> OperationExecution:
        if action is not OperationAction.PLAN:
            return super().run(repository, action, fleet_run_id=fleet_run_id)
        report = make_operation_report(
            repository,
            action,
            verdict=Verdict.FAILED,
            reason_code=ReasonCode.TESTS_FAILED,
            fleet_run_id=fleet_run_id,
            returncode=1,
        )
        return OperationExecution(stdout="", stderr="tests failed", report=report)


class SequenceRunner:
    def __init__(self) -> None:
        self.update_calls = 0

    def run(
        self,
        repository: Path,
        action: OperationAction,
        *,
        fleet_run_id: str | None = None,
        cancel_requested: object = None,
    ) -> OperationExecution:
        if action is OperationAction.INIT:
            verdict = Verdict.READY
        elif action is OperationAction.UPDATE:
            self.update_calls += 1
            verdict = Verdict.UPDATED if self.update_calls != 2 else Verdict.UNCHANGED
        else:
            verdict = Verdict.READY
        report = make_operation_report(
            repository,
            action,
            verdict=verdict,
            fleet_run_id=fleet_run_id,
            reason_code=(
                ReasonCode.DEPENDENCIES_UPDATED
                if verdict is Verdict.UPDATED
                else ReasonCode.NO_UPDATES
                if verdict is Verdict.UNCHANGED
                else ReasonCode.READY
            ),
        )
        return OperationExecution(stdout="", stderr="", report=report)


def make_repository(root: Path, name: str) -> Path:
    repository = root / name
    repository.mkdir(parents=True)
    (repository / "pyproject.toml").write_text("[project]\nname='x'\nversion='0.1.0'\n")
    (repository / ".git").mkdir()
    return repository


def make_fleet(tmp_path: Path, runner: object):
    root = tmp_path / "repos"
    root.mkdir(exist_ok=True)
    database = LocalDatabase(tmp_path / "state.db")
    state = local_state(database)
    state.add_location(root)
    fleet, manager, _, _ = fleet_stack(state, runner)
    operations, _, _, _ = direct_operation_stack(database, state.journal, CommandRunnerStub(runner))
    return fleet, manager, operations


def finish(manager: FleetRunControlPlane, action: OperationAction):
    started = manager.start(action)
    return manager.wait_for_terminal(started.fleet_run_id, timeout=3)


def test_fleet_snapshot_uses_journal_as_operation_ssot(tmp_path: Path) -> None:
    root = tmp_path / "repos"
    included = make_repository(root, "api")
    excluded = make_repository(root, "legacy")
    database = LocalDatabase(tmp_path / "state.db")
    state = local_state(database)
    state.add_location(root)
    state.exclude(excluded, "Legacy", expected_durable_revision=state.read_view().durable_revision)
    fleet, manager, _, _ = fleet_stack(state, ReadyRunner())

    before = fleet.snapshot()
    repeated_before = fleet.snapshot()
    assert before.provenance.snapshot_id == repeated_before.provenance.snapshot_id
    assert before.summary.discovered == 2
    assert before.summary.not_checked == 1
    assert before.repositories[0].guidance.reason_code is ReasonCode.NOT_CHECKED

    detail = finish(manager, OperationAction.INIT)
    snapshot = fleet.snapshot()

    assert len(detail.operations) == 1
    assert detail.operations[0].repository.path == included.resolve()
    assert snapshot.summary.ready_for_update == 1
    assert snapshot.provenance.snapshot_id != before.provenance.snapshot_id
    snapshot_operation_ids = {
        state.report.provenance.operation_id
        for repository in snapshot.repositories
        for _, state in repository.operation_states()
        if state is not None
    }
    assert detail.operations[0].provenance.operation_id in snapshot_operation_ids
    legacy = next(repo for repo in snapshot.repositories if repo.scope is FleetScope.EXCLUDED)
    assert legacy.guidance.reason_code is ReasonCode.EXCLUDED


def test_plan_requires_current_ready_init(tmp_path: Path) -> None:
    root = tmp_path / "repos"
    make_repository(root, "api")
    fleet, manager, _ = make_fleet(tmp_path, ReadyRunner())

    try:
        manager.start(OperationAction.PLAN)
    except TargetSelectionError as exc:
        assert "initialize prerequisite" in str(exc)
    else:
        raise AssertionError("plan must be gated until init is READY")

    finish(manager, OperationAction.INIT)
    detail = finish(manager, OperationAction.PLAN)
    assert len(detail.operations) == 1


def test_latest_fleet_update_summary_survives_newer_direct_update(tmp_path: Path) -> None:
    root = tmp_path / "repos"
    repository = make_repository(root, "api")
    runner = SequenceRunner()
    fleet, manager, operations = make_fleet(tmp_path, runner)

    finish(manager, OperationAction.INIT)
    first_update = finish(manager, OperationAction.UPDATE)
    first_snapshot = fleet.snapshot()
    assert first_snapshot.summary.updated == 1
    assert first_snapshot.repositories[0].readiness_state is ReadinessState.STALE

    finish(manager, OperationAction.INIT)
    second_update = finish(manager, OperationAction.UPDATE)
    second_snapshot = fleet.snapshot()
    assert second_snapshot.summary.unchanged == 1
    latest_update = second_snapshot.latest_run_family(OperationAction.UPDATE)
    assert latest_update is not None
    assert latest_update.run.fleet_run_id == second_update.run.fleet_run_id

    direct = operations.run(repository, OperationAction.UPDATE)
    assert direct.report.provenance.fleet_run_id is None
    after_direct = fleet.snapshot()
    assert after_direct.repositories[0].update is not None
    assert (
        after_direct.repositories[0].update.report.provenance.operation_id
        == direct.report.provenance.operation_id
    )
    assert after_direct.summary.updated == 0
    assert after_direct.summary.unchanged == 1
    retained_update = after_direct.latest_run_family(OperationAction.UPDATE)
    assert retained_update is not None
    assert retained_update.run.fleet_run_id == second_update.run.fleet_run_id
    assert (
        first_update.operations[0].provenance.operation_id
        != second_update.operations[0].provenance.operation_id
    )


def test_failed_plan_remains_visible_in_snapshot_outcome_and_guidance(tmp_path: Path) -> None:
    root = tmp_path / "repos"
    make_repository(root, "api")
    fleet, manager, _ = make_fleet(tmp_path, PlanFailureRunner())

    finish(manager, OperationAction.INIT)
    plan = finish(manager, OperationAction.PLAN)
    snapshot = fleet.snapshot()

    assert plan.outcome is FleetRunOutcome.ATTENTION_REQUIRED
    assert snapshot.summary.needs_attention == 1
    repository = snapshot.repositories[0]
    assert repository.plan is not None
    assert repository.plan.report.outcome.verdict is Verdict.FAILED
    assert repository.guidance.reason_code is ReasonCode.TESTS_FAILED
    latest_plan = snapshot.latest_run_family(OperationAction.PLAN)
    assert latest_plan is not None
    assert latest_plan.outcome is FleetRunOutcome.ATTENTION_REQUIRED
