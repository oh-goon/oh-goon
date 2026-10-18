from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from oh_goon.core.models.fleet_plan import ResolvedKubernetesSetImageStep
from oh_goon.core.path_authority import RepositoryPathSpace
from oh_goon.integrations.exact_file_write import publish_text_file, remove_file_durably
from oh_goon.integrations.fleet_plan_kubernetes_release import KubernetesDeploymentObservation


@dataclass(frozen=True)
class KubernetesSetImageOutputPaths:
    metadata: Path
    resource_version: Path
    generation: Path
    image: Path


def resolve_kubernetes_set_image_output_paths(
    repository: Path, step: ResolvedKubernetesSetImageStep
) -> KubernetesSetImageOutputPaths:
    space = RepositoryPathSpace.from_root(repository)
    paths = KubernetesSetImageOutputPaths(
        metadata=space.resolve(step.output.metadata, label="Fleet Kubernetes mutation metadata"),
        resource_version=space.resolve(
            step.output.resource_version, label="Fleet Kubernetes resourceVersion output"
        ),
        generation=space.resolve(
            step.output.generation, label="Fleet Kubernetes generation output"
        ),
        image=space.resolve(step.output.image, label="Fleet Kubernetes image output"),
    )
    for path in (paths.metadata, paths.resource_version, paths.generation, paths.image):
        path.parent.mkdir(parents=True, exist_ok=True)
    return paths


def clear_kubernetes_set_image_outputs(paths: KubernetesSetImageOutputPaths) -> None:
    for path in (paths.metadata, paths.resource_version, paths.generation, paths.image):
        remove_file_durably(path)


def parse_kubernetes_deployment(
    path: Path, step: ResolvedKubernetesSetImageStep
) -> KubernetesDeploymentObservation:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if (
        not isinstance(payload, dict)
        or payload.get("apiVersion") != "apps/v1"
        or payload.get("kind") != "Deployment"
    ):
        raise ValueError("Fleet Kubernetes API response is not one apps/v1 Deployment")
    metadata = payload.get("metadata")
    spec = payload.get("spec")
    if not isinstance(metadata, dict) or not isinstance(spec, dict):
        raise ValueError("Fleet Kubernetes Deployment response is missing metadata/spec")
    if (
        metadata.get("name") != step.target.name
        or metadata.get("namespace") != step.target.namespace
    ):
        raise ValueError("Fleet Kubernetes API returned a different Deployment identity")
    resource_version = metadata.get("resourceVersion")
    generation = metadata.get("generation")
    if not isinstance(resource_version, str) or not resource_version:
        raise ValueError("Fleet Kubernetes Deployment has no resourceVersion")
    if not isinstance(generation, int) or generation < 1:
        raise ValueError("Fleet Kubernetes Deployment has no positive generation")
    template = spec.get("template")
    pod_spec = template.get("spec") if isinstance(template, dict) else None
    containers = pod_spec.get("containers") if isinstance(pod_spec, dict) else None
    if not isinstance(containers, list):
        raise ValueError("Fleet Kubernetes Deployment has no pod container list")
    matches = [
        (index, item)
        for index, item in enumerate(containers)
        if isinstance(item, dict) and item.get("name") == step.target.container
    ]
    if len(matches) != 1:
        raise ValueError(
            f"Fleet Kubernetes Deployment must contain exactly one container named {step.target.container!r}"
        )
    index, container = matches[0]
    image = container.get("image")
    if not isinstance(image, str) or not image or any(character.isspace() for character in image):
        raise ValueError("Fleet Kubernetes target container has an invalid current image value")
    return KubernetesDeploymentObservation(
        resource_version=resource_version,
        generation=generation,
        container_index=index,
        image=image,
    )


def write_kubernetes_set_image_success_outputs(
    paths: KubernetesSetImageOutputPaths, observation: KubernetesDeploymentObservation
) -> None:
    publish_text_file(paths.resource_version, observation.resource_version + "\n")
    publish_text_file(paths.generation, str(observation.generation) + "\n")
    publish_text_file(paths.image, observation.image + "\n")


def write_kubernetes_set_image_evidence(
    step: ResolvedKubernetesSetImageStep,
    paths: KubernetesSetImageOutputPaths,
    *,
    before: KubernetesDeploymentObservation,
    after: KubernetesDeploymentObservation | None,
    mutated: bool,
    already_desired: bool,
    http_status: int,
    result: str,
) -> None:
    payload = {
        "schema": "oh-goon/kubernetes-set-image-evidence/v1",
        "operation": "deployment_set_image",
        "api_server": step.target.api.authority,
        "namespace": step.target.namespace,
        "deployment": step.target.name,
        "container": step.target.container,
        "desired_image": step.image,
        "precondition_mode": step.precondition.mode,
        "before_resource_version": before.resource_version,
        "before_generation": before.generation,
        "before_image": before.image,
        "after_resource_version": after.resource_version if after else None,
        "after_generation": after.generation if after else None,
        "after_image": after.image if after else None,
        "mutation_committed": mutated,
        "already_desired": already_desired,
        "http_status": http_status,
        "result": result,
        "json_patch_tests": ["metadata.resourceVersion", "container.name", "container.image"],
        "transport_retries": step.policy.transport_retries,
        "tls_verify": step.policy.tls_verify,
        "rollout_waited": step.policy.wait_for_rollout,
        "credential_sources": [
            step.credentials.token_host_env,
            step.credentials.ca_certificate_host_env,
        ],
        "credential_values_recorded": False,
    }
    publish_text_file(paths.metadata, json.dumps(payload, indent=2, sort_keys=True) + "\n")
