from __future__ import annotations

from pathlib import Path

import pytest

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


def _init_state(path: Path, freshness: ResultFreshness) -> OperationState:
    path.mkdir()
    return OperationState(
        report=make_operation_report(path, OperationAction.INIT, Verdict.READY, ReasonCode.READY),
        freshness=freshness,
    )


def test_repository_guidance_consumes_canonical_init_state(tmp_path: Path) -> None:
    current = _init_state(tmp_path / "current", ResultFreshness.CURRENT)
    stale = _init_state(tmp_path / "stale", ResultFreshness.STALE)

    assert (
        explain_repository_guidance(
            FleetScope.INCLUDED, init=current, plan=None, update=None, verify=None
        )
        == current.explanation
    )
    assert (
        explain_repository_guidance(
            FleetScope.INCLUDED, init=stale, plan=None, update=None, verify=None
        ).reason_code
        is ReasonCode.READINESS_STALE
    )
