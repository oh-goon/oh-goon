"""The original Fleet read owner supplies one immutable campaign read cut."""

from datetime import timedelta
from types import SimpleNamespace

import pytest

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.models.agent_repair import AgentRepairContext
from oh_goon.core.models.fleet_plan import ResolvedCommandStep, ResolvedFleetPlan
from oh_goon.core.models.fleet_plan_execution_policy import (
    FleetPlanRunLimits,
    MaintenanceExecutionPolicy,
)
from oh_goon.core.models.target import FleetPlanTarget
from oh_goon.core.time import utc_now
from oh_goon.services.fleet_run_control_plane import FleetRunControlPlane
from tests.factories.fleet_plan_targets import structural_fleet_plan_target


def test_campaign_readback_uses_one_verified_cut_without_loading_captured_sources(monkeypatch):
    plan = ResolvedFleetPlan.create(
        name="Campaign",
        description="Projection fixture",
        execution=MaintenanceExecutionPolicy(),
        steps=(ResolvedCommandStep(step_id="verify", argv=("check",), timeout_seconds=10),),
    )
    base = structural_fleet_plan_target(plan)
    target = FleetPlanTarget(
        **{
            **base.model_dump(),
            "agent_repair": AgentRepairContext(
                repair_id="initial",
                attempt=2,
                previous_run_id="initial",
                max_attempts=2,
                limits=FleetPlanRunLimits(
                    deadline_at=utc_now() + timedelta(seconds=60), step_timeout_seconds=10
                ),
                phase="verification",
                review_pass=1,
                original_execution_digest="original",
                candidate_identity="candidate",
                scope_identity="scope",
                write_roots=("code.txt",),
            ),
        }
    )
    selected = SimpleNamespace(run=SimpleNamespace(target=target))
    calls = []
    members = (selected,)
    campaign = SimpleNamespace(
        campaign_id="initial", journal_revision=34, phases=(SimpleNamespace(fleet_run_id="child"),)
    )
    view = SimpleNamespace(
        revision=34,
        campaign=lambda _campaign_id: campaign,
        required_run_detail=lambda run_id: (calls.append(("selected", run_id)) or selected),
        run_details=lambda: (calls.append(("members",)) or members),
    )
    journal = SimpleNamespace(read_view=lambda: (calls.append(("cut",)) or view))
    expected = {"schema": "oh-goon.agent-campaign-result/v2", "result": "incomplete"}

    def project(campaign_id, details, *, journal_revision, campaign):
        assert campaign.campaign_id == "initial" and campaign.journal_revision == 34
        assert campaign_id == "initial" and details == members and journal_revision == 34
        return expected

    monkeypatch.setattr("oh_goon.services.fleet_run_control_plane.project_agent_campaign", project)
    result = FleetRunControlPlane.agent_campaign(SimpleNamespace(_journal=journal), "child")
    assert result is expected and calls == [("cut",), ("selected", "child"), ("selected", "child")]


def test_ordinary_run_cannot_be_presented_as_a_campaign():
    view = SimpleNamespace(
        required_run_detail=lambda _: SimpleNamespace(run=SimpleNamespace(target=None))
    )
    manager = SimpleNamespace(_journal=SimpleNamespace(read_view=lambda: view))
    with pytest.raises(FleetPlanInvalid, match="not part"):
        FleetRunControlPlane.agent_campaign(manager, "ordinary")
