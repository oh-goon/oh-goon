from __future__ import annotations

from datetime import timedelta
from pathlib import Path

from oh_goon.core.models.explanation import ReasonCode
from oh_goon.core.models.fleet import OperationState
from oh_goon.core.projections.explanation import explain_repository_guidance
from oh_goon.core.vocabulary import (
    FleetScope,
    OperationAction,
    ResultFreshness,
    Verdict,
)
from tests.factories.operation_reports import make_operation_report


def test_operation_state_owns_generic_lineage_and_finished_time(tmp_path: Path) -> None:
    repository = tmp_path / "repo"
    repository.mkdir()
    report = make_operation_report(repository, OperationAction.UPDATE, Verdict.UPDATED)
    state = OperationState(report=report, freshness=ResultFreshness.CURRENT)

    assert state.action is OperationAction.UPDATE
    assert state.repository_id == report.repository.repository_id
    assert state.finished_at == report.provenance.finished_at


def test_repository_guidance_orders_current_attention_by_operation_state_time(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repo-order"
    repository.mkdir()
    update_report = make_operation_report(
        repository,
        OperationAction.UPDATE,
        Verdict.FAILED,
        ReasonCode.TESTS_FAILED,
    )
    plan_report = make_operation_report(
        repository,
        OperationAction.PLAN,
        Verdict.FAILED,
        ReasonCode.DIRTY_WORKTREE,
    )
    later = update_report.provenance.finished_at + timedelta(seconds=1)
    plan_report = plan_report.model_copy(
        update={
            "provenance": plan_report.provenance.model_copy(
                update={"started_at": later, "finished_at": later}
            )
        }
    )
    update = OperationState(report=update_report, freshness=ResultFreshness.CURRENT)
    plan = OperationState(report=plan_report, freshness=ResultFreshness.CURRENT)

    guidance = explain_repository_guidance(
        FleetScope.INCLUDED,
        init=None,
        plan=plan,
        update=update,
        verify=None,
    )

    assert guidance.reason_code is ReasonCode.DIRTY_WORKTREE
