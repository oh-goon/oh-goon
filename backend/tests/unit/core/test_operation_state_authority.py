from pathlib import Path

from oh_goon.core.models.explanation import ReasonCode
from oh_goon.core.models.fleet import OperationState
from oh_goon.core.vocabulary import OperationAction, ResultFreshness, Verdict
from tests.factories.operation_reports import make_operation_report


def test_operation_state_owns_current_success_and_attention_semantics(tmp_path: Path) -> None:
    repository = tmp_path / "repo"
    repository.mkdir()
    ready = make_operation_report(repository, OperationAction.PLAN, Verdict.READY)
    failed = make_operation_report(
        repository,
        OperationAction.PLAN,
        Verdict.FAILED,
        ReasonCode.OPERATION_FAILED,
        returncode=1,
    )

    current_ready = OperationState(report=ready, freshness=ResultFreshness.CURRENT)
    stale_ready = OperationState(report=ready, freshness=ResultFreshness.STALE)
    current_failed = OperationState(report=failed, freshness=ResultFreshness.CURRENT)

    assert current_ready.is_current is True
    assert current_ready.current_successful is True
    assert current_ready.current_needs_attention is False
    assert stale_ready.is_current is False
    assert stale_ready.current_successful is False
    assert stale_ready.current_needs_attention is False
    assert current_failed.is_current is True
    assert current_failed.current_successful is False
    assert current_failed.current_needs_attention is True
