from __future__ import annotations

from pathlib import Path

import pytest

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.runtime.goon_templates import GoonTemplateStore
from oh_goon.runtime.plan_templates import PlanTemplateStore
from oh_goon.services.fleet_plan_resolver import FleetPlanResolver

TEMPLATES = Path(__file__).resolve().parents[4] / "templates"
PLANS = PlanTemplateStore(TEMPLATES / "plans")
RESOLVER = FleetPlanResolver(goon_templates=GoonTemplateStore(TEMPLATES / "goons"))
DIGEST = "sha256:" + "a" * 64


def test_oci_plan_rejects_repository_path_traversal_before_execution() -> None:
    for name, value in (("containerfile", "../Dockerfile"), ("context", "/tmp/context")):
        with pytest.raises(FleetPlanInvalid, match="invalid repository_path syntax"):
            RESOLVER.resolve(
                PLANS.source("oci/image").path,
                inputs={"base_registry_hostname": "registry.example.com", name: value},
            )


def test_kubernetes_plan_rejects_mutable_image_reference_before_execution() -> None:
    with pytest.raises(FleetPlanInvalid, match="invalid oci_reference syntax"):
        RESOLVER.resolve(
            PLANS.source("kubernetes/set-image").path,
            inputs={
                "kubernetes_api_hostname": "kube.example.com",
                "namespace": "production",
                "deployment": "api",
                "container": "api",
                "image": "harbor.example.com/team/app:latest",
            },
        )


@pytest.mark.parametrize(
    "goon_key",
    ["kubernetes/set-image", "kubernetes/rollout-verify"],
)
@pytest.mark.parametrize(
    "deployment",
    ["Api", "api..service", "api_underscore", "a" * 64, "a." * 127 + "a"],
)
def test_each_kubernetes_consumer_rejects_invalid_deployment_in_production_resolution(
    tmp_path: Path, goon_key: str, deployment: str
) -> None:
    # Resolve through the canonical production resolver, without admission or Step execution.
    # A regression to type: string in either Goon must fail this test, even when
    # the Kubernetes step model would reject the target later in compilation.
    plan_path = tmp_path / "plan.yml"
    extras = (
        "- image\n"
        if goon_key == "kubernetes/set-image"
        else "- expected_generation\n- expected_image\n"
    )
    plan_path.write_text(
        "version: 1\n"
        "inputs:\n"
        "- kubernetes_api_hostname\n- namespace\n- deployment\n- container\n"
        f"{extras}goons:\n- {goon_key}\n",
        encoding="utf-8",
    )
    with pytest.raises(FleetPlanInvalid, match="invalid kubernetes_deployment_name syntax"):
        RESOLVER.resolve(
            plan_path,
            inputs={
                "kubernetes_api_hostname": "kube.example.com",
                "namespace": "production",
                "deployment": deployment,
                "container": "api",
                **(
                    {"image": f"harbor.example.com/team/api@{DIGEST}"}
                    if goon_key == "kubernetes/set-image"
                    else {
                        "expected_generation": "7",
                        "expected_image": f"harbor.example.com/team/api@{DIGEST}",
                    }
                ),
            },
        )


@pytest.mark.parametrize("goon_key", ["kubernetes/set-image", "kubernetes/release"])
def test_kubernetes_consumers_accept_dns_subdomain_deployment_names(
    tmp_path: Path, goon_key: str
) -> None:
    plan_path = tmp_path / "plan.yml"
    plan_path.write_text(
        "version: 1\ninputs:\n- kubernetes_api_hostname\n- namespace\n"
        "- deployment\n- container\n"
        f"- image\ngoons:\n- {goon_key}\n",
        encoding="utf-8",
    )
    plan = RESOLVER.resolve(
        plan_path,
        inputs={
            "kubernetes_api_hostname": "kube.example.com",
            "namespace": "production",
            "deployment": "api.service",
            "container": "api",
            "image": f"harbor.example.com/team/api@{DIGEST}",
        },
    )
    assert all(step.target.name == "api.service" for step in plan.steps)  # type: ignore[attr-defined]


def test_oci_tag_gate_rejects_non_sha256_digest_before_execution() -> None:
    with pytest.raises(FleetPlanInvalid, match="invalid digest syntax"):
        RESOLVER.resolve(
            PLANS.source("oci/tag-gate").path,
            inputs={
                "registry_hostname": "harbor.example.com",
                "repository": "team/app",
                "tag": "production",
                "expected_digest": "sha256:abc",
            },
        )


def test_hardened_production_inputs_still_resolve_valid_values() -> None:
    kubernetes = RESOLVER.resolve(
        PLANS.source("kubernetes/set-image").path,
        inputs={
            "kubernetes_api_hostname": "kube.example.com",
            "namespace": "production",
            "deployment": "api",
            "container": "api",
            "image": f"harbor.example.com/team/app@{DIGEST}",
        },
    )
    oci = RESOLVER.resolve(
        PLANS.source("oci/image").path,
        inputs={
            "base_registry_hostname": "registry.example.com",
            "containerfile": "containers/Dockerfile",
            "context": "service",
        },
    )

    assert kubernetes.steps[0].image.endswith(f"@{DIGEST}")  # type: ignore[attr-defined]
    assert oci.steps[0].containerfile == "containers/Dockerfile"  # type: ignore[attr-defined]


def _release_inputs() -> dict[str, object]:
    return {
        "release_channel": "production",
        "revision": "abc1234",
        "coverage_threshold": 90,
    }


def test_python_release_metadata_rejects_removed_registry_inputs() -> None:
    with pytest.raises(FleetPlanInvalid, match="unknown inputs: harbor_host"):
        RESOLVER.resolve(
            PLANS.source("python/release-metadata").path,
            inputs={
                "release_channel": "production",
                "revision": "abc1234",
                "harbor_host": "harbor.example.com",
            },
        )


def test_release_metadata_accepts_version_only_inputs() -> None:
    plan = RESOLVER.resolve(PLANS.source("python/standard").path, inputs=_release_inputs())
    release = next(item for item in plan.imports if item.name == "python/release-metadata")
    assert release.step_ids == ("python-release-metadata.compute",)


def test_standalone_release_metadata_plan_has_two_public_inputs() -> None:
    source = PLANS.source("python/release-metadata")
    assert set(source.compiled.projected_inputs) == {"release_channel", "revision"}
    assert source.compiled.inputs == ()
    plan = RESOLVER.resolve(source.path, inputs={"release_channel": "beta", "revision": "abc1234"})
    assert [item.name for item in plan.imports] == ["python/release-metadata"]


@pytest.mark.parametrize("revision", ["ABC1234", "abc_1234", "abc-1234", "abc123", "g" * 7])
def test_release_metadata_rejects_noncanonical_revision_before_execution(revision: str) -> None:
    with pytest.raises(FleetPlanInvalid, match="invalid git_revision"):
        RESOLVER.resolve(
            PLANS.source("python/release-metadata").path,
            inputs={"release_channel": "alpha", "revision": revision},
        )
