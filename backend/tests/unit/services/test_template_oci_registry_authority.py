"""M135: independent OCI registry roles survive production Plan resolution."""

from __future__ import annotations

import pytest

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.models.fleet_plan import ResolvedOciBuildStep, ResolvedOciPublishStep
from oh_goon.services.fleet_plan_resolver import FleetPlanResolver
from tests.factories.shipped_templates import GOON_TEMPLATES, PLAN_TEMPLATES


@pytest.mark.parametrize("plan_key", ("oci/publish-image", "oci/release", "oci/release-deploy"))
def test_m135_different_pull_and_publish_registries_remain_independent(plan_key: str) -> None:
    """No extra resolver, Step calls, or inferred registry fallback."""
    arguments = {
        "base_registry_hostname": "base.example.org",
        "publish_registry_hostname": "destination.example.net",
        "publish_repository": "team/product",
    }
    if plan_key == "oci/release-deploy":
        arguments.update(
            kubernetes_api_hostname="api.example.org",
            namespace="production",
            deployment="service",
            container="service",
        )
    plan = FleetPlanResolver(goon_templates=GOON_TEMPLATES).resolve(
        PLAN_TEMPLATES.source(plan_key).path, inputs=arguments
    )
    build = next(step for step in plan.steps if isinstance(step, ResolvedOciBuildStep))
    publish = next(step for step in plan.steps if isinstance(step, ResolvedOciPublishStep))
    assert build.capabilities.egress.rules[0].hostname == "base.example.org"
    assert publish.capabilities.egress.rules[0].hostname == "destination.example.net"
    assert publish.target.repository_reference == "destination.example.net/team/product"
    assert publish.workspace.source.step_id == build.step_id
    assert publish.policy.reference_mode == "digest"


@pytest.mark.parametrize(
    "absent", ("base_registry_hostname", "publish_registry_hostname", "publish_repository")
)
def test_m135_missing_independent_registry_value_rejects_before_plan_exists(absent: str) -> None:
    arguments = {
        "base_registry_hostname": "base.example.org",
        "publish_registry_hostname": "destination.example.net",
        "publish_repository": "team/product",
    }
    del arguments[absent]
    with pytest.raises(FleetPlanInvalid):
        FleetPlanResolver(goon_templates=GOON_TEMPLATES).resolve(
            PLAN_TEMPLATES.source("oci/publish-image").path, inputs=arguments
        )


def test_m135_image_build_does_not_invent_publisher_authority() -> None:
    plan = FleetPlanResolver(goon_templates=GOON_TEMPLATES).resolve(
        PLAN_TEMPLATES.source("oci/image").path,
        inputs={"base_registry_hostname": "base.example.org"},
    )
    assert len(plan.steps) == 1
    assert isinstance(plan.steps[0], ResolvedOciBuildStep)
    assert plan.steps[0].capabilities.egress.rules[0].hostname == "base.example.org"
