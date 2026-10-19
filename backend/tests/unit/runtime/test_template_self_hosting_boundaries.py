"""M138: canonical compiled workspace bindings are not interchangeable by command text."""

from __future__ import annotations

import pytest

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.integrations.fleet_plan_compilation import parse_goon_template
from tests.factories.shipped_templates import GOON_TEMPLATES

BROWSER_GOONS = (
    "browser-localhost-transport-preflight",
    "browser-engine-preflight",
    "browser-harness-preflight",
    "browser-runtime-preflight",
)


def test_m138_integrations_share_argv_but_not_source_authority() -> None:
    repository = GOON_TEMPLATES.source("oh-goon/integration-tests").compiled
    producer = GOON_TEMPLATES.source("oh-goon/integration-contract").compiled
    assert len(repository.steps) == len(producer.steps) == 1
    assert repository.steps[0].argv == producer.steps[0].argv == ("make", "test-integration")
    assert not repository.inputs
    assert repository.steps[0].workspace.source.kind == "repository"
    assert len(producer.inputs) == 1
    assert producer.inputs[0].name == "source_workspace"
    assert producer.inputs[0].required
    assert producer.inputs[0].input_type.value == "workspace_reference"
    assert producer.steps[0].workspace.source.kind == "step"
    assert producer.steps[0].workspace.source.step_id == "${{ inputs.source_workspace }}"


@pytest.mark.parametrize("key", BROWSER_GOONS)
def test_m138_browser_preflight_carries_exact_required_producer_source(key: str) -> None:
    compiled = GOON_TEMPLATES.source(f"oh-goon/{key}").compiled
    assert len(compiled.steps) == 1
    assert len(compiled.inputs) == 1
    assert compiled.inputs[0].name == "source_workspace"
    assert compiled.inputs[0].required
    assert compiled.inputs[0].input_type.value == "workspace_reference"
    assert compiled.steps[0].workspace.source.kind == "step"
    assert compiled.steps[0].workspace.source.step_id == "${{ inputs.source_workspace }}"
    assert compiled.steps[0].argv[0] == "make"


def test_m138_missing_explicit_step_reference_rejected_at_canonical_compile() -> None:
    with pytest.raises(FleetPlanInvalid, match="non-empty step name"):
        parse_goon_template("""version: 1
inputs:
  source_workspace:
    type: workspace_reference
steps:
- run: make test-integration
  workspace:
    from: step
    step: ""
""")


def test_m138_plain_make_command_does_not_implicitly_acquire_a_producer() -> None:
    compiled = parse_goon_template("version: 1\nsteps:\n- run: make test-integration\n")
    assert compiled.steps[0].workspace.source.kind == "repository"
    assert compiled.inputs == ()


def test_m138_unbound_integration_source_rejects_before_execution(tmp_path) -> None:
    from oh_goon.services.fleet_plan_resolver import FleetPlanResolver

    plan_path = tmp_path / "plan.yml"
    plan_path.write_text(
        "version: 1\nexecution:\n  purpose: verification\ngoons:\n- oh-goon/integration-contract\n",
        encoding="utf-8",
    )
    resolver = FleetPlanResolver(goon_templates=GOON_TEMPLATES)
    with pytest.raises(FleetPlanInvalid):
        resolver.resolve(plan_path)
    # Supplying a name is not permission to fabricate a producer Step.
    with pytest.raises(FleetPlanInvalid):
        resolver.resolve(plan_path, inputs={"source_workspace": "nonexistent-producer"})
