from __future__ import annotations

from oh_goon.core.models.fleet_plan import (
    OciBuildPlanStep,
    PlanArtifactDeclaration,
    ResolvedOciBuildStep,
)
from oh_goon.core.models.fleet_plan_oci import OciBuildPolicy, OciRegistryEndpoint
from oh_goon.core.models.fleet_plan_step_contract import (
    HostnameAllowlistPlanEgress,
    HostnamePlanEgressRule,
    PlanStepCapabilities,
)


def resolve_oci_build(step: OciBuildPlanStep, *, step_id: str) -> ResolvedOciBuildStep:
    """Compile one typed OCI build into explicit immutable runtime authority."""

    registries = tuple(
        OciRegistryEndpoint.model_validate(item.model_dump(mode="python"))
        for item in step.registries
    )
    policy = OciBuildPolicy.model_validate(step.policy.model_dump(mode="python"))
    egress = (
        HostnameAllowlistPlanEgress(
            rules=tuple(
                HostnamePlanEgressRule(
                    hostname=endpoint.hostname,
                    protocol="tcp",
                    ports=(endpoint.port,),
                )
                for endpoint in registries
            )
        )
        if registries
        else PlanStepCapabilities().egress
    )
    capabilities = PlanStepCapabilities(egress=egress)
    artifacts = (
        PlanArtifactDeclaration(path=step.output.archive, retention=step.output.retention),
        PlanArtifactDeclaration(path=step.output.metadata, retention="evidence"),
    )
    return ResolvedOciBuildStep(
        step_id=step_id,
        lifecycle=step.lifecycle,
        containerfile=step.containerfile,
        context=step.context,
        registries=registries,
        policy=policy,
        output=step.output,
        timeout_seconds=step.timeout_seconds,
        workspace=step.workspace,
        cache=step.cache,
        capabilities=capabilities,
        on_failure=step.on_failure,
        when=step.when,
        retry=step.retry,
        artifacts=artifacts,
    )
