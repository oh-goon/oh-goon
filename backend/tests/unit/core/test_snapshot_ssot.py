from pathlib import Path

import pytest
from pydantic import ValidationError

from oh_goon.core.models.explanation import ReasonCode
from oh_goon.core.models.fleet import FleetSnapshot, IncludedFleetRepository, OperationState
from oh_goon.core.models.repository import (
    IncludedRepositoryCatalogEntry,
    RepositoryCatalogEntry,
    RepositoryIdentity,
)
from oh_goon.core.models.run import (
    CompletedFleetRun,
    FleetRunDetail,
    FleetRunProgress,
)
from oh_goon.core.models.run_admission import (
    DirectFleetExecutionAdmission,
    fleet_repository_scope,
)
from oh_goon.core.projections import (
    FleetProjectionContext,
    build_fleet_snapshot,
    build_fleet_summary,
    explain_operation,
    explain_repository_guidance,
)
from oh_goon.core.projections.fleet_snapshot_provenance import snapshot_payload
from oh_goon.core.time import utc_now
from oh_goon.core.vocabulary import (
    FleetRunStatus,
    FleetScope,
    OperationAction,
    ReadinessState,
    ResultFreshness,
    Verdict,
)
from tests.factories.capabilities import capability_snapshot
from tests.factories.operation_reports import make_operation_report
from tests.factories.run_admission import repository_admissions_for


def operation_state(report, freshness: ResultFreshness) -> OperationState:
    return OperationState(report=report, freshness=freshness)


def fleet_repository(
    catalog: RepositoryCatalogEntry,
    *,
    init: OperationState | None = None,
    plan: OperationState | None = None,
    update: OperationState | None = None,
    verify: OperationState | None = None,
) -> IncludedFleetRepository:
    return IncludedFleetRepository(
        scope=FleetScope.INCLUDED,
        repository=catalog.repository,
        repository_instance_id="instance-test",
        init=init,
        plan=plan,
        update=update,
        verify=verify,
    )


def entry(path: Path) -> RepositoryCatalogEntry:
    identity = RepositoryIdentity(name=path.name, path=path)
    return IncludedRepositoryCatalogEntry(scope=FleetScope.INCLUDED, repository=identity)


def run_detail(report, fleet_run_id: str) -> FleetRunDetail:
    now = utc_now()
    attention = report.outcome.verdict.needs_attention
    failed = report.outcome.verdict is Verdict.FAILED
    state = CompletedFleetRun(
        status=FleetRunStatus.COMPLETED,
        fleet_run_id=fleet_run_id,
        capability=report.capability or capability_snapshot(report.action, report.target),
        target=report.target,
        repository_scope=fleet_repository_scope(None),
        targets=[report.repository],
        accepted_at=now,
        started_at=now,
        finished_at=now,
        progress=FleetRunProgress(
            total=1,
            succeeded=0 if attention else 1,
            failed=1 if failed else 0,
            needs_attention=1 if attention else 0,
        ),
    )
    return FleetRunDetail(
        repository_admissions=repository_admissions_for(state.target, state.targets),
        run=state,
        execution=DirectFleetExecutionAdmission(),
        operations=[report],
    )


def test_operation_state_derives_semantic_explanation_from_report(tmp_path: Path) -> None:
    repository = tmp_path / "repo"
    repository.mkdir()
    report = make_operation_report(repository, OperationAction.INIT)
    other = make_operation_report(
        repository,
        OperationAction.UPDATE,
        Verdict.FAILED,
        ReasonCode.TESTS_FAILED,
    )

    with pytest.raises(ValidationError):
        OperationState(
            report=report,
            freshness=ResultFreshness.CURRENT,
            explanation=explain_operation(other),
        )

    canonical = operation_state(report, ResultFreshness.CURRENT)
    assert canonical.explanation == explain_operation(report)


def test_repository_rejects_operation_in_wrong_action_slot(tmp_path: Path) -> None:
    catalog = entry(tmp_path / "repo")
    catalog.repository.path.mkdir()
    report = make_operation_report(catalog.repository, OperationAction.INIT)
    state = operation_state(report, ResultFreshness.CURRENT)

    with pytest.raises(ValidationError, match="plan slot requires a plan report"):
        IncludedFleetRepository(
            scope=FleetScope.INCLUDED,
            repository=catalog.repository,
            repository_instance_id="instance-test",
            plan=state,
        )


def test_repository_derives_readiness_and_guidance_from_operation_facts(tmp_path: Path) -> None:
    catalog = entry(tmp_path / "repo")
    catalog.repository.path.mkdir()
    init = operation_state(
        make_operation_report(catalog.repository, OperationAction.INIT),
        ResultFreshness.CURRENT,
    )
    repository = fleet_repository(catalog, init=init)

    with pytest.raises(ValidationError):
        IncludedFleetRepository(
            scope=FleetScope.INCLUDED,
            repository=catalog.repository,
            repository_instance_id="instance-test",
            init=init,
            readiness_state=ReadinessState.STALE,
        )

    assert repository.readiness_state is ReadinessState.READY
    assert repository.guidance == explain_repository_guidance(
        FleetScope.INCLUDED, init=init, plan=None, update=None, verify=None
    )


def test_summary_attention_is_repository_union_not_max_of_independent_counts(
    tmp_path: Path,
) -> None:
    first_entry = entry(tmp_path / "first")
    second_entry = entry(tmp_path / "second")
    first_entry.repository.path.mkdir()
    second_entry.repository.path.mkdir()

    first_init = operation_state(
        make_operation_report(
            first_entry.repository,
            OperationAction.INIT,
            Verdict.MANUAL_ACTION_REQUIRED,
            ReasonCode.DIRTY_WORKTREE,
        ),
        ResultFreshness.CURRENT,
    )
    second_init = operation_state(
        make_operation_report(second_entry.repository, OperationAction.INIT),
        ResultFreshness.CURRENT,
    )
    second_update = operation_state(
        make_operation_report(
            second_entry.repository,
            OperationAction.UPDATE,
            Verdict.FAILED,
            ReasonCode.TESTS_FAILED,
            fleet_run_id="fleet-update",
        ),
        ResultFreshness.CURRENT,
    )
    repositories = [
        fleet_repository(first_entry, init=first_init),
        fleet_repository(second_entry, init=second_init, update=second_update),
    ]

    summary = build_fleet_summary(
        FleetProjectionContext.build(
            repositories, None, [run_detail(second_update.report, "fleet-update")]
        )
    )

    assert repositories[0].needs_attention is True
    assert repositories[1].needs_attention is True
    assert summary.needs_attention == 2
    assert summary.failed == 1


def test_repository_attention_uses_only_current_operation_facts(tmp_path: Path) -> None:
    catalog = entry(tmp_path / "repo-attention")
    catalog.repository.path.mkdir()
    init = operation_state(
        make_operation_report(catalog.repository, OperationAction.INIT),
        ResultFreshness.CURRENT,
    )
    failed_plan_report = make_operation_report(
        catalog.repository,
        OperationAction.PLAN,
        Verdict.FAILED,
        ReasonCode.TESTS_FAILED,
    )

    current = fleet_repository(
        catalog,
        init=init,
        plan=operation_state(failed_plan_report, ResultFreshness.CURRENT),
    )
    stale = fleet_repository(
        catalog,
        init=init,
        plan=operation_state(failed_plan_report, ResultFreshness.STALE),
    )

    assert current.needs_attention is True
    assert stale.needs_attention is False


def test_repository_current_plan_uses_only_current_successful_plan(tmp_path: Path) -> None:
    catalog = entry(tmp_path / "repo-current-plan")
    catalog.repository.path.mkdir()
    successful_plan = make_operation_report(catalog.repository, OperationAction.PLAN, Verdict.READY)
    failed_plan = make_operation_report(
        catalog.repository,
        OperationAction.PLAN,
        Verdict.FAILED,
        ReasonCode.TESTS_FAILED,
        returncode=1,
    )

    current = fleet_repository(
        catalog,
        plan=operation_state(successful_plan, ResultFreshness.CURRENT),
    )
    stale = fleet_repository(
        catalog,
        plan=operation_state(successful_plan, ResultFreshness.STALE),
    )
    failed = fleet_repository(
        catalog,
        plan=operation_state(failed_plan, ResultFreshness.CURRENT),
    )

    assert current.has_current_plan is True
    assert stale.has_current_plan is False
    assert failed.has_current_plan is False


def test_repository_row_facts_hide_stale_branch_but_keep_last_verdicts(tmp_path: Path) -> None:
    catalog = entry(tmp_path / "repo-row-facts")
    catalog.repository.path.mkdir()
    current_init = operation_state(
        make_operation_report(catalog.repository, OperationAction.INIT),
        ResultFreshness.CURRENT,
    )
    stale_init = operation_state(
        make_operation_report(catalog.repository, OperationAction.INIT),
        ResultFreshness.STALE,
    )
    plan = operation_state(
        make_operation_report(catalog.repository, OperationAction.PLAN, Verdict.READY),
        ResultFreshness.STALE,
    )
    update = operation_state(
        make_operation_report(
            catalog.repository,
            OperationAction.UPDATE,
            Verdict.UPDATED,
            ReasonCode.DEPENDENCIES_UPDATED,
        ),
        ResultFreshness.STALE,
    )
    verify = operation_state(
        make_operation_report(catalog.repository, OperationAction.VERIFY, Verdict.READY),
        ResultFreshness.STALE,
    )

    current = fleet_repository(
        catalog,
        init=current_init,
        plan=plan,
        update=update,
        verify=verify,
    )
    stale = fleet_repository(catalog, init=stale_init, plan=plan, update=update, verify=verify)

    assert current.current_branch_position is not None
    assert current.current_branch_position.state == "named"
    assert current.current_branch_position.branch == "main"
    assert stale.current_branch_position is None
    assert current.last_plan_verdict is Verdict.READY
    assert current.last_update_verdict is Verdict.UPDATED
    assert current.last_verify_verdict is Verdict.READY


def test_summary_does_not_treat_direct_update_as_fleet_run_result(tmp_path: Path) -> None:
    catalog = entry(tmp_path / "repo")
    catalog.repository.path.mkdir()
    init = operation_state(
        make_operation_report(catalog.repository, OperationAction.INIT),
        ResultFreshness.CURRENT,
    )
    direct_update = operation_state(
        make_operation_report(
            catalog.repository,
            OperationAction.UPDATE,
            Verdict.UPDATED,
            ReasonCode.DEPENDENCIES_UPDATED,
            fleet_run_id=None,
        ),
        ResultFreshness.CURRENT,
    )
    repository = fleet_repository(catalog, init=init, update=direct_update)

    summary = build_fleet_summary(FleetProjectionContext.build([repository], None, []))

    assert summary.updated == 0
    assert summary.unchanged == 0
    assert summary.failed == 0


def test_snapshot_derives_summary_attention_and_review_from_source_facts(tmp_path: Path) -> None:
    catalog = entry(tmp_path / "repo")
    catalog.repository.path.mkdir()
    init = operation_state(
        make_operation_report(catalog.repository, OperationAction.INIT),
        ResultFreshness.CURRENT,
    )
    repository = fleet_repository(catalog, init=init)
    snapshot = build_fleet_snapshot(
        [repository],
        durable_revision=1,
        journal_revision=1,
        active_fleet_run=None,
        latest_fleet_runs=[],
    )
    rebuilt = build_fleet_snapshot(
        [repository],
        durable_revision=1,
        journal_revision=1,
        active_fleet_run=None,
        latest_fleet_runs=[],
    )

    with pytest.raises(ValidationError):
        FleetSnapshot(
            provenance=snapshot.provenance,
            repositories=snapshot.repositories,
            summary=snapshot.summary.model_copy(update={"ready_for_update": 99}),
        )

    assert rebuilt.provenance.snapshot_id == snapshot.provenance.snapshot_id
    assert rebuilt.summary == snapshot.summary
    assert rebuilt.attention == snapshot.attention
    assert rebuilt.review_preparation == snapshot.review_preparation
    assert rebuilt.repositories == snapshot.repositories


def test_snapshot_provenance_uses_only_source_facts_not_computed_run_views(tmp_path: Path) -> None:
    repository = tmp_path / "repo"
    repository.mkdir()
    report = make_operation_report(repository, OperationAction.INIT)
    detail = run_detail(report, "snapshot-source-facts")
    context = FleetProjectionContext.build(
        repositories=(),
        active_fleet_run=detail,
        latest_fleet_runs=(detail,),
    )

    payload = snapshot_payload(context, durable_revision=7, journal_revision=5)
    latest = payload["latest_fleet_runs"][0]
    active = payload["active_fleet_run"]

    assert "game_tape" not in latest
    assert "plan_trust_receipts" not in latest
    assert "outcome" not in latest
    assert "outcome" not in active
