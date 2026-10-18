from __future__ import annotations

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.fleet_plan_values import PipelineValueCompiler
from oh_goon.core.models.fleet_plan import (
    OciTagAssertPlanStep,
    PlanArtifactDeclaration,
    PlanPublishedOutput,
    ResolvedOciTagAssertStep,
)
from oh_goon.core.models.fleet_plan_oci import OciRegistryEndpoint
from oh_goon.core.models.fleet_plan_oci_registry import OciRegistryCredentials
from oh_goon.core.models.fleet_plan_oci_tag import (
    OCI_TAG_ASSERT_OUTPUT_NAMES,
    OciTagTarget,
    is_valid_oci_expected_digest,
)
from oh_goon.core.models.fleet_plan_step_contract import (
    HostnameAllowlistPlanEgress,
    HostnamePlanEgressRule,
    PlanStepCapabilities,
)
from oh_goon.core.models.fleet_plan_values import HostEnvironmentPart


def resolve_oci_tag_assert(
    step: OciTagAssertPlanStep,
    *,
    step_id: str,
    compiler: PipelineValueCompiler,
) -> ResolvedOciTagAssertStep:
    """Compile one read-only tag assertion without resolving credential bytes."""

    registry = OciRegistryEndpoint.model_validate(step.target.registry.model_dump(mode="python"))
    target = OciTagTarget(
        registry=registry,
        repository=step.target.repository,
        tag=step.target.tag,
    )
    expected_digest = step.expected_digest
    if not is_valid_oci_expected_digest(expected_digest):
        raise FleetPlanInvalid(
            "Fleet OCI expected digest must be sha256:<64 lowercase hex> "
            "or one exact earlier step output reference"
        )
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
        PlanArtifactDeclaration(path=step.output.resolved_reference, retention="evidence"),
    )
    outputs = (
        PlanPublishedOutput(
            name=OCI_TAG_ASSERT_OUTPUT_NAMES[0], path=step.output.digest, max_bytes=80
        ),
        PlanPublishedOutput(
            name=OCI_TAG_ASSERT_OUTPUT_NAMES[1],
            path=step.output.resolved_reference,
            max_bytes=512,
        ),
    )
    return ResolvedOciTagAssertStep(
        step_id=step_id,
        lifecycle=step.lifecycle,
        target=target,
        expected_digest=expected_digest,
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
            f"Fleet OCI tag assertion credential {name!r} must be declared exactly as env.{name} from host"
        )
    return compiled.parts[0].name
