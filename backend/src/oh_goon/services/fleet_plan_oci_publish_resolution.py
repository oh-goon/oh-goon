from __future__ import annotations

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.fleet_plan_values import PipelineValueCompiler
from oh_goon.core.models.fleet_plan import (
    OciPublishPlanStep,
    PlanArtifactDeclaration,
    PlanPublishedOutput,
    ResolvedOciPublishStep,
)
from oh_goon.core.models.fleet_plan_oci import OciRegistryEndpoint
from oh_goon.core.models.fleet_plan_oci_publish import (
    OCI_PUBLISH_OUTPUT_NAMES,
    OciPublishTarget,
)
from oh_goon.core.models.fleet_plan_oci_registry import OciRegistryCredentials
from oh_goon.core.models.fleet_plan_step_contract import (
    HostnameAllowlistPlanEgress,
    HostnamePlanEgressRule,
    PlanStepCapabilities,
)
from oh_goon.core.models.fleet_plan_values import HostEnvironmentPart


def resolve_oci_publish(
    step: OciPublishPlanStep,
    *,
    step_id: str,
    compiler: PipelineValueCompiler,
) -> ResolvedOciPublishStep:
    """Compile one registry effect without resolving or serializing credential bytes."""

    registry = OciRegistryEndpoint.model_validate(step.target.registry.model_dump(mode="python"))
    target = OciPublishTarget(registry=registry, repository=step.target.repository)
    credentials = OciRegistryCredentials(
        username_host_env=_require_host_env(compiler, step.credentials.username_env),
        password_host_env=_require_host_env(compiler, step.credentials.password_env),
    )
    capabilities = PlanStepCapabilities(
        egress=HostnameAllowlistPlanEgress(
            rules=(
                HostnamePlanEgressRule(
                    hostname=registry.hostname,
                    protocol="tcp",
                    ports=(registry.port,),
                ),
            )
        ),
    )
    artifacts = (
        PlanArtifactDeclaration(path=step.output.metadata, retention="evidence"),
        PlanArtifactDeclaration(path=step.output.digest, retention="evidence"),
        PlanArtifactDeclaration(path=step.output.reference, retention="evidence"),
    )
    outputs = (
        PlanPublishedOutput(
            name=OCI_PUBLISH_OUTPUT_NAMES[0], path=step.output.digest, max_bytes=80
        ),
        PlanPublishedOutput(
            name=OCI_PUBLISH_OUTPUT_NAMES[1], path=step.output.reference, max_bytes=512
        ),
    )
    return ResolvedOciPublishStep(
        step_id=step_id,
        lifecycle=step.lifecycle,
        archive=step.archive,
        target=target,
        credentials=credentials,
        policy=step.policy,
        output=step.output,
        timeout_seconds=step.timeout_seconds,
        workspace=step.workspace,
        cache=step.cache,
        capabilities=capabilities,
        on_failure=step.on_failure,
        when=step.when,
        retry=step.retry,
        artifacts=artifacts,
        outputs=outputs,
    )


def _require_host_env(compiler: PipelineValueCompiler, name: str) -> str:
    compiled = compiler.describe_env(name)
    if len(compiled.parts) != 1 or not isinstance(compiled.parts[0], HostEnvironmentPart):
        raise FleetPlanInvalid(
            f"Fleet OCI publish credential {name!r} must be declared exactly as env.{name} from host"
        )
    return compiled.parts[0].name
