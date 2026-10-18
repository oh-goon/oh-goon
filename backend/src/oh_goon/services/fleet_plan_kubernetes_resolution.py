from __future__ import annotations

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.fleet_plan_values import PipelineValueCompiler
from oh_goon.core.models.fleet_plan import (
    KubernetesRolloutVerifyPlanStep,
    KubernetesSetImagePlanStep,
    PlanArtifactDeclaration,
    PlanPublishedOutput,
    ResolvedKubernetesRolloutVerifyStep,
    ResolvedKubernetesSetImageStep,
)
from oh_goon.core.models.fleet_plan_kubernetes import (
    KUBERNETES_SET_IMAGE_OUTPUT_NAMES,
    AuthoringKubernetesApiCredentials,
    AuthoringKubernetesDeploymentTarget,
    KubernetesApiCredentials,
    KubernetesApiEndpoint,
    KubernetesDeploymentTarget,
)
from oh_goon.core.models.fleet_plan_step_contract import (
    HostnameAllowlistPlanEgress,
    HostnamePlanEgressRule,
    PlanStepCapabilities,
)
from oh_goon.core.models.fleet_plan_values import HostEnvironmentPart


def resolve_kubernetes_set_image(
    step: KubernetesSetImagePlanStep,
    *,
    step_id: str,
    compiler: PipelineValueCompiler,
) -> ResolvedKubernetesSetImageStep:
    """Compile one exact Deployment image mutation without resolving credential bytes."""

    target = _target(step.target)
    return ResolvedKubernetesSetImageStep(
        step_id=step_id,
        lifecycle=step.lifecycle,
        target=target,
        image=step.image,
        precondition=step.precondition,
        credentials=_credentials(step.credentials, compiler),
        policy=step.policy,
        output=step.output,
        timeout_seconds=step.timeout_seconds,
        workspace=step.workspace,
        cache=step.cache,
        capabilities=_capabilities(target.api),
        on_failure=step.on_failure,
        when=step.when,
        retry=step.retry,
        artifacts=_artifacts(step),
        outputs=_outputs(step),
    )


def resolve_kubernetes_rollout_verify(
    step: KubernetesRolloutVerifyPlanStep,
    *,
    step_id: str,
    compiler: PipelineValueCompiler,
) -> ResolvedKubernetesRolloutVerifyStep:
    """Compile one bounded read-only readiness check for an exact Deployment generation."""

    target = _target(step.target)
    return ResolvedKubernetesRolloutVerifyStep(
        step_id=step_id,
        lifecycle=step.lifecycle,
        target=target,
        expected_generation=step.expected_generation,
        expected_image=step.expected_image,
        credentials=_credentials(step.credentials, compiler),
        policy=step.policy,
        timeout_seconds=step.timeout_seconds,
        workspace=step.workspace,
        cache=step.cache,
        capabilities=_capabilities(target.api),
        on_failure=step.on_failure,
        when=step.when,
        retry=step.retry,
    )


def _target(source: AuthoringKubernetesDeploymentTarget) -> KubernetesDeploymentTarget:
    api = KubernetesApiEndpoint.model_validate(source.api.model_dump(mode="python"))
    return KubernetesDeploymentTarget(
        api=api,
        namespace=source.namespace,
        name=source.name,
        container=source.container,
    )


def _credentials(
    source: AuthoringKubernetesApiCredentials,
    compiler: PipelineValueCompiler,
) -> KubernetesApiCredentials:
    return KubernetesApiCredentials(
        token_host_env=_require_host_env(compiler, source.token_env),
        ca_certificate_host_env=_require_host_env(compiler, source.ca_certificate_env),
    )


def _require_host_env(compiler: PipelineValueCompiler, name: str) -> str:
    compiled = compiler.describe_env(name)
    if len(compiled.parts) != 1 or not isinstance(compiled.parts[0], HostEnvironmentPart):
        raise FleetPlanInvalid(
            f"Fleet Kubernetes credential {name!r} must be declared exactly as env.{name} from host"
        )
    return compiled.parts[0].name


def _capabilities(api: KubernetesApiEndpoint) -> PlanStepCapabilities:
    return PlanStepCapabilities(
        egress=HostnameAllowlistPlanEgress(
            rules=(
                HostnamePlanEgressRule(hostname=api.hostname, protocol="tcp", ports=(api.port,)),
            )
        ),
    )


def _artifacts(step: KubernetesSetImagePlanStep) -> tuple[PlanArtifactDeclaration, ...]:
    return tuple(
        PlanArtifactDeclaration(path=path, retention="evidence")
        for path in (
            step.output.metadata,
            step.output.resource_version,
            step.output.generation,
            step.output.image,
        )
    )


def _outputs(step: KubernetesSetImagePlanStep) -> tuple[PlanPublishedOutput, ...]:
    return (
        PlanPublishedOutput(
            name=KUBERNETES_SET_IMAGE_OUTPUT_NAMES[0],
            path=step.output.resource_version,
            max_bytes=128,
        ),
        PlanPublishedOutput(
            name=KUBERNETES_SET_IMAGE_OUTPUT_NAMES[1],
            path=step.output.generation,
            max_bytes=64,
        ),
        PlanPublishedOutput(
            name=KUBERNETES_SET_IMAGE_OUTPUT_NAMES[2],
            path=step.output.image,
            max_bytes=512,
        ),
    )
