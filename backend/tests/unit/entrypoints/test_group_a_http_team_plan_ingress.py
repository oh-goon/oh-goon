"""M181: Team HTTP execution must deny unprovisioned Runs before Team source reads.

The service guard delegates to the existing Fleet lifecycle; HTTP owns only
conflict presentation, never a second issuer, placement or admission policy.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from oh_goon.api.routes.league import run_team
from oh_goon.api.schemas.league import TeamRunRequest
from oh_goon.core.enforcer_lace_trust import TrustProtocolError
from oh_goon.core.errors import StateConflict
from oh_goon.core.models.goon_plan_source import TeamGoonSource
from oh_goon.core.models.league import ConfiguredTeamPipeline, LeagueTeam
from oh_goon.core.models.repository import RepositoryIdentity
from oh_goon.core.models.scheduling import SchedulingPriority
from oh_goon.services.fleet_plans import FleetPlanService
from oh_goon.services.fleet_prework_provisioner import FleetPreworkProvisioner
from oh_goon.services.fleet_run_control_plane import FleetRunControlPlane
from oh_goon.services.fleet_run_lifecycle import FleetRunLifecycle
from oh_goon.services.goon import GoonService

REPO = Path("/managed/team")
DIGEST = "a" * 64
INSTANCE = "current-instance"


def request(run_id: str) -> TeamRunRequest:
    return TeamRunRequest(
        repository=REPO,
        expected_pipeline_sha256=DIGEST,
        expected_repository_instance_id=INSTANCE,
        fleet_run_id=run_id,
        inputs={"profile": "strict"},
    )


def provisioner(ids: tuple[str, ...]) -> FleetPreworkProvisioner:
    # An enrolled identity is a cheap preflight fact, not a signed contract.
    value = object.__new__(FleetPreworkProvisioner)
    object.__setattr__(value, "enrolled", tuple(SimpleNamespace(run_id=i) for i in ids))
    return value


def service(*, enrolled: FleetPreworkProvisioner | None, authenticated=True):
    owner = object.__new__(FleetRunLifecycle)
    owner._require_authenticated_fleet = authenticated
    owner._fleet_provisioner = enrolled
    control = object.__new__(FleetRunControlPlane)
    control._lifecycle = owner
    plans = FleetPlanService(object(), control, object(), object(), object())
    plans.run = Mock(return_value=object())
    league = SimpleNamespace(
        configured_team=Mock(side_effect=AssertionError("Team source read too early"))
    )
    return GoonService(league, plans, object()), plans, league


@pytest.mark.parametrize("run_id", [None, "", "unknown", 42, True])
def test_direct_team_service_rejects_missing_or_forged_run_without_team_source(run_id):
    goons, plans, league = service(enrolled=provisioner(("known",)))
    with pytest.raises(TrustProtocolError):
        goons.run(REPO, expected_pipeline_sha256=DIGEST, fleet_run_id=run_id)
    league.configured_team.assert_not_called()
    plans.run.assert_not_called()


@pytest.mark.parametrize("run_id", [None, "", "known"])
def test_missing_provisioner_denies_even_a_named_run_before_source(run_id):
    goons, plans, league = service(enrolled=None)
    with pytest.raises(TrustProtocolError, match="not provisioned"):
        goons.run(REPO, expected_pipeline_sha256=DIGEST, fleet_run_id=run_id)
    league.configured_team.assert_not_called()
    plans.run.assert_not_called()


def test_team_http_route_maps_trust_to_generic_conflict_and_no_source():
    goons, plans, league = service(enrolled=provisioner(("known",)))
    with pytest.raises(StateConflict, match="valid authenticated admission authority") as exc:
        run_team(request("forged"), service=goons)
    assert "signer" not in str(exc.value)
    assert "provisioner" not in str(exc.value)
    league.configured_team.assert_not_called()
    plans.run.assert_not_called()


def test_team_membership_fence_never_observes_native_signers(monkeypatch):
    monkeypatch.setattr(
        FleetPreworkProvisioner,
        "_observe",
        lambda *a, **kw: pytest.fail("native issuer observed at preflight"),
    )
    goons, plans, league = service(enrolled=provisioner(("known",)))
    with pytest.raises(AssertionError, match="Team source read too early"):
        goons.run(REPO, expected_pipeline_sha256=DIGEST, fleet_run_id="known")
    league.configured_team.assert_called_once_with(REPO)
    plans.run.assert_not_called()


def test_enrolled_team_preserves_original_plan_source_and_repository_identity():
    goons, plans, league = service(enrolled=provisioner(("known",)))
    team = LeagueTeam(
        repository=RepositoryIdentity(path=REPO),
        repository_instance_id=INSTANCE,
        pipeline=ConfiguredTeamPipeline(
            state="CONFIGURED",
            path="goon.yml",
            sha256=DIGEST,
            byte_count=42,
        ),
    )
    league.configured_team.side_effect = None
    league.configured_team.return_value = team
    result = run_team(request("known"), service=goons)
    assert result is plans.run.return_value
    league.configured_team.assert_called_once_with(REPO)
    plans.run.assert_called_once_with(
        REPO / "goon.yml",
        repository_ids={team.repository.repository_id},
        fleet_run_id="known",
        requested_priority=SchedulingPriority.NORMAL,
        inputs={"profile": "strict"},
        source=TeamGoonSource(sha256=DIGEST),
    )


@pytest.mark.parametrize("kind", ["pipeline", "instance"])
def test_enrolled_team_preserves_stale_repository_and_pipeline_rejection(kind):
    goons, plans, league = service(enrolled=provisioner(("known",)))
    team = LeagueTeam(
        repository=RepositoryIdentity(path=REPO),
        repository_instance_id="replacement" if kind == "instance" else INSTANCE,
        pipeline=ConfiguredTeamPipeline(
            state="CONFIGURED",
            path="goon.yml",
            sha256="b" * 64 if kind == "pipeline" else DIGEST,
            byte_count=42,
        ),
    )
    league.configured_team.side_effect = None
    league.configured_team.return_value = team
    with pytest.raises(StateConflict, match="changed since it was displayed"):
        run_team(request("known"), service=goons)
    plans.run.assert_not_called()


def test_nonproduction_team_service_does_not_require_enrollment():
    goons, plans, league = service(enrolled=None, authenticated=False)
    with pytest.raises(AssertionError, match="Team source read too early"):
        goons.run(REPO, expected_pipeline_sha256=DIGEST)
    league.configured_team.assert_called_once_with(REPO)
    plans.run.assert_not_called()


def test_real_fastapi_team_route_returns_409_without_issuer_detail():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from oh_goon.api import dependencies
    from oh_goon.api.errors import domain_exception_handler
    from oh_goon.api.routes import league as league_routes
    from oh_goon.core.errors import FleetDomainError

    goons, plans, league = service(enrolled=provisioner(("known",)))
    app = FastAPI()
    app.add_exception_handler(FleetDomainError, domain_exception_handler)
    app.include_router(league_routes.router, prefix="/api")
    app.dependency_overrides[dependencies.goon_service] = lambda: goons
    with TestClient(app) as client:
        response = client.post(
            "/api/league/team-runs",
            json={
                "repository": str(REPO),
                "fleet_run_id": "forged",
                "expected_pipeline_sha256": DIGEST,
                "expected_repository_instance_id": INSTANCE,
                "inputs": {},
            },
        )
    assert response.status_code == 409
    payload = response.json()
    assert payload["kind"] == "CONFLICT"
    assert (
        payload["message"]
        == "Fleet Plan execution requires valid authenticated admission authority"
    )
    league.configured_team.assert_not_called()
    plans.run.assert_not_called()


def test_http_validation_still_rejects_missing_run_without_team_source():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        TeamRunRequest(
            repository=REPO,
            expected_pipeline_sha256=DIGEST,
            expected_repository_instance_id=INSTANCE,
            fleet_run_id="",
        )
