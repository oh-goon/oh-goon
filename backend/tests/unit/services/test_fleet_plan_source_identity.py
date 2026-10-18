from __future__ import annotations

import hashlib
from pathlib import Path
from typing import cast

import pytest
from pydantic import ValidationError

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.models.goon_plan_source import PlanFileSource, TeamGoonSource
from oh_goon.core.models.repository import RepositoryIdentity
from oh_goon.core.models.run_admission import (
    DirectFleetExecutionAdmission,
    fleet_repository_scope,
)
from oh_goon.core.models.run_events import RunAdmittedEvent
from oh_goon.core.models.target import FleetPlanTarget
from oh_goon.core.vocabulary import FleetRunEventType, OperationAction
from oh_goon.services.fleet_plan_resolver import FleetPlanResolver
from oh_goon.services.fleet_plans import FleetPlanService
from tests.factories.capabilities import capability_snapshot
from tests.factories.fleet_plan_targets import structural_fleet_plan_target


def _plan(path: Path) -> None:
    path.write_text(
        """version: 1
execution:
  purpose: verification
steps:
  - id: check
    run: "true"
""",
        encoding="utf-8",
    )


def _service(runs: object) -> FleetPlanService:
    return FleetPlanService(
        FleetPlanResolver(),
        cast(object, runs),
        cast(object, object()),
        cast(object, object()),
        cast(object, object()),
    )


def test_plan_run_binds_source_to_exact_resolved_bytes(tmp_path: Path) -> None:
    plan_path = tmp_path / "goon.yml"
    _plan(plan_path)
    observed: dict[str, object] = {}

    class RecordingRuns:
        def start_prepared_plan(self, target, **kwargs):
            observed["target"] = target
            return object()

    service = _service(RecordingRuns())
    service.lock(plan_path)
    service.run(plan_path)

    target = cast(FleetPlanTarget, observed["target"])
    assert target.source.sha256 == hashlib.sha256(plan_path.read_bytes()).hexdigest()


def test_plan_run_rejects_plan_identity_changed_after_preview(tmp_path: Path) -> None:
    plan_path = tmp_path / "goon.yml"
    _plan(plan_path)

    class NoRuns:
        def start(self, *args, **kwargs):
            raise AssertionError("plan identity mismatch must fail before FleetRun admission")

    service = _service(NoRuns())
    service.lock(plan_path)
    with pytest.raises(FleetPlanInvalid, match="changed after preview"):
        service.run(plan_path, expected_plan_id="plan-from-earlier-preview")


def test_plan_run_rejects_source_digest_not_matching_resolved_bytes(tmp_path: Path) -> None:
    plan_path = tmp_path / "goon.yml"
    _plan(plan_path)

    class NoRuns:
        def start(self, *args, **kwargs):
            raise AssertionError("source mismatch must fail before FleetRun admission")

    service = _service(NoRuns())
    service.lock(plan_path)
    with pytest.raises(FleetPlanInvalid, match="exact bytes resolved for execution"):
        service.run(plan_path, source=PlanFileSource(sha256="0" * 64))


def test_team_goon_source_is_structurally_single_team_at_admission(tmp_path: Path) -> None:
    plan_path = tmp_path / "goon.yml"
    _plan(plan_path)
    plan = FleetPlanResolver().resolve(plan_path)
    target = structural_fleet_plan_target(
        plan,
        source=TeamGoonSource(sha256=hashlib.sha256(plan_path.read_bytes()).hexdigest()),
    )
    repository_a = RepositoryIdentity(path=tmp_path / "a")
    repository_b = RepositoryIdentity(path=tmp_path / "b")

    with pytest.raises(ValidationError, match="Team Goon source requires exactly one repository"):
        RunAdmittedEvent(
            event_type=FleetRunEventType.RUN_ADMITTED,
            sequence=1,
            fleet_run_id="team-source-mismatch",
            target=target,
            capability=capability_snapshot(OperationAction.EXECUTE_PLAN, target),
            repository_scope=fleet_repository_scope(
                {repository_a.repository_id, repository_b.repository_id}
            ),
            targets=(repository_a, repository_b),
            execution=DirectFleetExecutionAdmission(),
            repository_admissions=(),
        )
