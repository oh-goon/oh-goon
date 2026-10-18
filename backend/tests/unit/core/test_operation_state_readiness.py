from __future__ import annotations

from pathlib import Path

import pytest

from oh_goon.core.models.explanation import ReasonCode
from oh_goon.core.models.fleet import OperationState
from oh_goon.core.models.operation import CompletedAttentionOperationOutcome
from oh_goon.core.vocabulary import OperationAction, ReadinessState, ResultFreshness, Verdict
from tests.factories.operation_reports import make_operation_report


@pytest.mark.parametrize(
    ("freshness", "verdict", "expected"),
    [
        (ResultFreshness.CURRENT, Verdict.READY, ReadinessState.READY),
        (ResultFreshness.STALE, Verdict.READY, ReadinessState.STALE),
        (ResultFreshness.CURRENT, Verdict.FAILED, ReadinessState.FAILED),
    ],
)
def test_init_operation_state_owns_readiness(
    tmp_path: Path,
    freshness: ResultFreshness,
    verdict: Verdict,
    expected: ReadinessState,
) -> None:
    repository = tmp_path / "repo"
    repository.mkdir()
    state = OperationState(
        report=make_operation_report(repository, OperationAction.INIT, verdict),
        freshness=freshness,
    )
    assert state.readiness_state is expected
    assert state.current_ready is (
        freshness is ResultFreshness.CURRENT and verdict is Verdict.READY
    )


def test_non_init_operation_has_no_readiness_projection(tmp_path: Path) -> None:
    repository = tmp_path / "repo"
    repository.mkdir()
    state = OperationState(
        report=make_operation_report(repository, OperationAction.PLAN, Verdict.READY),
        freshness=ResultFreshness.CURRENT,
    )
    with pytest.raises(ValueError, match="Init operation state"):
        _ = state.readiness_state


def test_current_manual_attention_init_state_maps_to_manual_action_required(tmp_path: Path) -> None:
    repository = tmp_path / "repo-manual"
    repository.mkdir()
    report = make_operation_report(repository, OperationAction.INIT, Verdict.READY).model_copy(
        update={
            "outcome": CompletedAttentionOperationOutcome(
                state="completed_attention",
                returncode=1,
                reason_code=ReasonCode.MANUAL_ACTION_REQUIRED,
            )
        }
    )
    state = OperationState(report=report, freshness=ResultFreshness.CURRENT)
    assert state.readiness_state is ReadinessState.MANUAL_ACTION_REQUIRED
