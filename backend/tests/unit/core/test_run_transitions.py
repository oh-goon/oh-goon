from __future__ import annotations

import pytest
from pydantic import TypeAdapter, ValidationError

from oh_goon.core.models.run import (
    FleetRunProgress,
    FleetRunState,
    QueuedFleetRun,
    RunningFleetRun,
)
from oh_goon.core.models.run_admission import fleet_repository_scope
from oh_goon.core.time import utc_now
from oh_goon.core.vocabulary import FleetRunStatus, OperationAction
from tests.factories.capabilities import capability_snapshot


def _run_common() -> dict[str, object]:
    now = utc_now()
    return {
        "fleet_run_id": "fleet-structural",
        "capability": capability_snapshot(OperationAction.INIT),
        "repository_scope": fleet_repository_scope(None),
        "targets": [],
        "accepted_at": now,
        "progress": FleetRunProgress(total=0, succeeded=0, failed=0),
    }


def test_fleet_run_state_is_structural_and_discriminator_is_required() -> None:
    common = _run_common()
    queued = QueuedFleetRun(status=FleetRunStatus.QUEUED, **common)
    parsed = TypeAdapter(FleetRunState).validate_python(
        queued.model_dump(mode="python", exclude_computed_fields=True)
    )
    assert isinstance(parsed, QueuedFleetRun)

    with pytest.raises(ValidationError):
        QueuedFleetRun(status=FleetRunStatus.QUEUED, started_at=utc_now(), **common)
    with pytest.raises(ValidationError):
        RunningFleetRun(status=FleetRunStatus.RUNNING, **common)
    with pytest.raises(ValidationError):
        QueuedFleetRun(**common)
    with pytest.raises(ValidationError):
        FleetRunProgress(total=1, succeeded=1, failed=0, completed=1)
