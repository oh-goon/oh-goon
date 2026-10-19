from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest.mock import Mock

from oh_goon.core.models.goon_plan_source import TeamGoonSource
from oh_goon.core.models.league import ConfiguredTeamPipeline, LeagueTeam
from oh_goon.core.models.repository import RepositoryIdentity
from oh_goon.core.models.scheduling import SchedulingPriority
from oh_goon.core.models.target import FleetPlanTarget
from oh_goon.services.fleet_plan_resolver import FleetPlanResolver
from oh_goon.services.fleet_plans import FleetPlanService
from oh_goon.services.goon import GoonService


def _team(repository: Path) -> LeagueTeam:
    return LeagueTeam(
        repository=RepositoryIdentity(path=repository),
        repository_instance_id="repo-instance-test",
        pipeline=ConfiguredTeamPipeline(
            state="CONFIGURED",
            path="goon.yml",
            sha256="a" * 64,
            byte_count=42,
        ),
    )


def test_goon_inspection_uses_team_root_and_canonical_plan_preview(tmp_path: Path) -> None:
    repository = tmp_path / "api"
    team = _team(repository)
    preview = Mock(name="preview")
    league = SimpleNamespace(configured_team=Mock(return_value=team))
    plans = SimpleNamespace(preview=Mock(return_value=preview))
    service = GoonService(league, plans, cast(object, object()))

    inspection = service.inspect(repository / "src", inputs={"profile": "strict"})

    league.configured_team.assert_called_once_with(repository / "src")
    plans.preview.assert_called_once_with(
        repository / "goon.yml",
        repository,
        inputs={"profile": "strict"},
    )
    assert inspection.team is team
    assert inspection.preview is preview


def test_goon_run_delegates_to_the_same_canonical_plan_run_boundary(tmp_path: Path) -> None:
    repository = tmp_path / "api"
    team = _team(repository)
    admitted = Mock(name="admitted")
    league = SimpleNamespace(configured_team=Mock(return_value=team))
    plans = SimpleNamespace(
        run=Mock(return_value=admitted), require_plan_ingress_preparation=Mock()
    )
    service = GoonService(league, plans, cast(object, object()))

    result = service.run(
        repository / "src",
        expected_pipeline_sha256=league.configured_team(repository).pipeline.sha256,
        inputs={"profile": "strict"},
        fleet_run_id="run-1",
    )

    assert result is admitted
    plans.require_plan_ingress_preparation.assert_called_once_with("run-1")
    plans.run.assert_called_once_with(
        repository / "goon.yml",
        repository_ids={team.repository.repository_id},
        fleet_run_id="run-1",
        requested_priority=SchedulingPriority.NORMAL,
        inputs={"profile": "strict"},
        source=TeamGoonSource(sha256="a" * 64),
    )


def test_goon_run_rejects_pipeline_changed_since_display(tmp_path: Path) -> None:
    repository = tmp_path / "api"
    team = _team(repository)
    league = SimpleNamespace(configured_team=Mock(return_value=team))
    plans = SimpleNamespace(run=Mock(), require_plan_ingress_preparation=Mock())
    service = GoonService(league, plans, cast(object, object()))

    import pytest

    from oh_goon.core.errors import StateConflict

    with pytest.raises(StateConflict, match="pipeline changed"):
        service.run(repository, expected_pipeline_sha256="b" * 64)
    plans.run.assert_not_called()


def test_single_team_run_resolves_the_exact_repository_goon_bytes(tmp_path: Path) -> None:
    repository = tmp_path / "api"
    repository.mkdir()
    goon = repository / "goon.yml"
    goon.write_text(
        """version: 1
steps:
  - id: check
    run: "true"
""",
        encoding="utf-8",
    )
    digest = hashlib.sha256(goon.read_bytes()).hexdigest()
    team = LeagueTeam(
        repository=RepositoryIdentity(path=repository),
        repository_instance_id="repo-instance-test",
        pipeline=ConfiguredTeamPipeline(
            state="CONFIGURED",
            path="goon.yml",
            sha256=digest,
            byte_count=goon.stat().st_size,
        ),
    )
    observed: dict[str, object] = {}

    class RecordingRuns:
        def require_plan_ingress_preparation(self, run_id):
            # This fixture exercises nonproduction canonical Plan forwarding.
            assert run_id is None

        def start_prepared_plan(self, target, **kwargs):
            observed["target"] = target
            observed["repository_ids"] = kwargs["repository_ids"]
            return object()

    plans = FleetPlanService(
        FleetPlanResolver(),
        cast(object, RecordingRuns()),
        cast(object, object()),
        cast(object, object()),
        cast(object, object()),
    )
    service = GoonService(
        SimpleNamespace(configured_team=Mock(return_value=team)),
        plans,
        cast(object, object()),
    )

    plans.lock(goon)
    service.run(repository, expected_pipeline_sha256=team.pipeline.sha256)

    target = cast(FleetPlanTarget, observed["target"])
    assert isinstance(target.source, TeamGoonSource)
    assert target.source.sha256 == digest
    assert observed["repository_ids"] == {team.repository.repository_id}


def test_goon_run_rejects_replacement_repository_instance_before_plan_run(tmp_path: Path) -> None:
    repository = tmp_path / "api"
    team = _team(repository)
    league = SimpleNamespace(configured_team=Mock(return_value=team))
    plans = SimpleNamespace(run=Mock(), require_plan_ingress_preparation=Mock())
    service = GoonService(league, plans, cast(object, object()))

    import pytest

    from oh_goon.core.errors import StateConflict

    with pytest.raises(StateConflict, match="repository instance changed"):
        service.run(
            repository,
            expected_pipeline_sha256=team.pipeline.sha256,
            expected_repository_instance_id="repo-instance-from-old-browser-snapshot",
        )
    plans.run.assert_not_called()
