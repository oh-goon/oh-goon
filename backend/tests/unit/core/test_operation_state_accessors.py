from __future__ import annotations

from pathlib import Path

from oh_goon.core.models.fleet import OperationState
from oh_goon.core.vocabulary import OperationAction, ResultFreshness, Verdict
from tests.factories.operation_reports import make_operation_report


def test_operation_state_owns_generic_current_identity_and_verdict_facts(tmp_path: Path) -> None:
    repository = tmp_path / "repo"
    repository.mkdir()
    report = make_operation_report(repository, OperationAction.UPDATE, Verdict.UPDATED)
    current = OperationState(report=report, freshness=ResultFreshness.CURRENT)
    stale = OperationState(report=report, freshness=ResultFreshness.STALE)

    assert current.verdict is Verdict.UPDATED
    assert current.operation_id == report.provenance.operation_id
    assert current.is_stale is False
    assert stale.is_stale is True
    assert current.is_current_operation(report.provenance.operation_id) is True
    assert stale.is_current_operation(report.provenance.operation_id) is False
    assert current.is_current_operation("different-operation") is False
