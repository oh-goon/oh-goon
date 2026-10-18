"""M137: network declarations survive canonical resolver and preview unchanged."""

from __future__ import annotations

from pathlib import Path

import pytest

from oh_goon.integrations.fleet_plan_compilation import parse_goon_template
from oh_goon.integrations.fleet_plan_preview import preview_plan
from oh_goon.services.fleet_plan_resolver import FleetPlanResolver
from tests.factories.shipped_templates import GOON_TEMPLATES, PLAN_TEMPLATES


@pytest.mark.parametrize("key", ["python/uv-audit", "security/trivy-audit", "python/secure"])
def test_m137_shipped_audits_have_exact_resolved_network_authority(key: str, tmp_path: Path) -> None:
    plan = FleetPlanResolver(goon_templates=GOON_TEMPLATES).resolve(PLAN_TEMPLATES.source(key).path)
    online = [step for step in plan.steps if step.capabilities.egress.mode != "none"]
    if key == "python/secure":
        assert {step.argv[0] for step in online} == {"uv", "trivy"}
        assert len(online) == 2
        assert len(plan.steps) > len(online)
    else:
        assert len(plan.steps) == len(online) == 1
    assert all(step.capabilities.egress.mode == "host_unrestricted" for step in online)
    assert all(step.retry.max_attempts == 1 for step in online)
    # Preview must report the same immutable Step policy, without starting work.
    repo = tmp_path / "repository"
    repo.mkdir()
    (repo / ".git").mkdir()
    preview = preview_plan(repo, plan)
    assert [(s.step_id, s.egress_mode) for s in preview.steps] == [
        (s.step_id, s.capabilities.egress.mode) for s in plan.steps
    ]


def test_m137_no_implicit_network_from_native_tool_or_shorthand() -> None:
    no_egress = parse_goon_template("version: 1\nsteps:\n- run: uv --preview audit --frozen\n")
    assert no_egress.steps[0].capabilities.egress.mode == "none"
    shorthand = parse_goon_template("version: 1\nsteps:\n- run: trivy fs .\n  network: true\n")
    # Alias is valid but must remain an explicit authoring decision; catalog policy
    # prohibits it for *shipped* packages in favour of the full mode declaration.
    assert shorthand.steps[0].capabilities.egress.mode == "host_unrestricted"
