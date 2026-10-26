from __future__ import annotations

from collections.abc import Iterable, Sequence

from oh_goon.core.fleet_plan_tool_requirements import (
    step_execution_tool,
    step_execution_tool_names,
)
from oh_goon.core.models.fleet_plan import (
    ResolvedAgentStep,
    ResolvedFleetPlan,
    ResolvedKubernetesRolloutVerifyStep,
    ResolvedKubernetesSetImageStep,
    ResolvedOciBuildStep,
    ResolvedOciPublishStep,
    ResolvedOciTagAssertStep,
    ResolvedPlanStep,
    ResolvedPreCommitStep,
)
from oh_goon.core.models.fleet_plan_diagnostics import PlanConfigurationEvidence
from oh_goon.core.models.fleet_plan_execution_policy import (
    CicdExecutionPolicy,
    ParallelRepositoryExecution,
)
from oh_goon.core.models.fleet_plan_requirements import (
    RepositoryFileExistsRequirement,
    RepositoryFolderExistsRequirement,
    RepositoryPathExistsRequirement,
    RepositoryRequirement,
    UvProjectRequirement,
)
from oh_goon.core.models.fleet_plan_step_contract import (
    PlanCacheSource,
    PlanStepEgress,
    PlanWorkspaceSource,
)
from oh_goon.core.models.fleet_plan_step_primitives import (
    PlanArtifactDeclaration,
    PlanPublishedOutput,
    PlanStreamOutput,
)
from oh_goon.integrations.fleet_plan_containerfile_policy import oci_build_repository_policy_id


def configuration_summary(configuration: PlanConfigurationEvidence | None) -> tuple[str, ...]:
    """Render captured facts only; no source access, value echo or admission decision."""
    if configuration is None:
        return ("Configuration lineage: unavailable in retained evidence",)
    lines = ["Configuration lineage (values withheld):"]
    if configuration.caller_file_sha256:
        lines.append(f"  Caller input file SHA-256: {configuration.caller_file_sha256}")
    for entry in configuration.entries:
        location = f" source={entry.source_sha256}" if entry.source_sha256 else ""
        if entry.line is not None:
            location += f":{entry.line}:{entry.column}"
        binding = entry.binding
        scope = "<root>" if entry.scope is None else entry.scope
        parent_scope = "<root>" if binding.parent_scope is None else binding.parent_scope
        parent = (
            f" <- {parent_scope}.input.{binding.parent_input}"
            if binding.parent_input
            else f" <- {binding.producer}"
            if binding.producer
            else ""
        )
        lines.append(
            f"  {scope}.{entry.namespace.value}.{entry.name}: "
            f"{binding.kind} ({entry.value_state}){parent}{location}"
        )
        for index, source in enumerate(binding.candidates, 1):
            label = source.name or source.git_field
            lines.append(
                f"    source {index}: {source.kind}{':' + label if label else ''} {source.state}"
            )
    if configuration.omitted_entries:
        lines.append(f"  Additional decisions omitted: {configuration.omitted_entries}")
    return tuple(lines)


def resolved_plan_summary(plan: ResolvedFleetPlan) -> tuple[str, ...]:
    """Render the concrete semantics users should inspect before execution."""
    lines = list(_plan_header_summary(plan))
    lines.extend(_execution_policy_summary(plan))
    lines.extend(_requirements_summary(plan.requirements))
    lines.append(f"Resolved steps: {len(plan.steps)}")
    for step in plan.steps:
        lines.extend(_step_summary(step))
    return tuple(lines)


def _plan_header_summary(plan: ResolvedFleetPlan) -> tuple[str, ...]:
    repositories = plan.execution.repositories
    repository_policy = repositories.mode.value
    if isinstance(repositories, ParallelRepositoryExecution):
        repository_policy += f" (max_parallel={repositories.max_parallel})"
    return (
        f"Plan: {plan.name}",
        f"Plan ID: {plan.plan_id}",
        f"Execution purpose: {plan.execution.purpose.value}",
        f"Repository execution: {repository_policy}",
    )


def _execution_policy_summary(plan: ResolvedFleetPlan) -> tuple[str, ...]:
    if not isinstance(plan.execution, CicdExecutionPolicy):
        return ()
    resources = plan.execution.resources
    cpu = str(resources.cpu_millis) if resources.cpu_millis is not None else "executor-available"
    memory = str(resources.memory_mib) if resources.memory_mib is not None else "executor-available"
    process_limit = (
        str(resources.max_processes)
        if resources.max_processes is not None
        else "executor-available"
    )
    return (
        "Repository admission: requirements",
        "Trust repository code: " + ("yes" if plan.execution.trust_repository_code else "no"),
        f"Resources: cpu_millis={cpu} memory_mib={memory} max_processes={process_limit}",
    )


def _requirements_summary(requirements: Sequence[RepositoryRequirement]) -> tuple[str, ...]:
    if not requirements:
        return ("Repository requirements: none",)
    lines = [f"Repository requirements: {len(requirements)}"]
    for requirement in requirements:
        lines.append(_requirement_summary(requirement))
    return tuple(lines)


def _requirement_summary(requirement: RepositoryRequirement) -> str:
    if isinstance(
        requirement,
        (
            RepositoryFileExistsRequirement,
            RepositoryFolderExistsRequirement,
            RepositoryPathExistsRequirement,
        ),
    ):
        return f"  require {requirement.kind}: {requirement.path}"
    if isinstance(requirement, UvProjectRequirement):
        selection = (
            f"group={requirement.group}" if requirement.group is not None else "base dependencies"
        )
        return f"  require uv_project: {selection} lock_mode={requirement.lock_mode}"
    qualifiers = []
    if requirement.non_empty:
        qualifiers.append("non_empty=true")
    if requirement.pattern is not None:
        qualifiers.append(f"pattern={requirement.pattern!r}")
    suffix = f" {' '.join(qualifiers)}" if qualifiers else ""
    return (
        "  require pyproject_value: "
        f"{requirement.field} type={requirement.value_type.value}{suffix}"
    )


def _oci_step_summary(step: ResolvedPlanStep) -> str | None:
    if isinstance(step, ResolvedOciBuildStep):
        registries = ",".join(item.image_authority for item in step.registries) or "scratch-only"
        return (
            "    oci_build: "
            f"builder=buildah containerfile={step.containerfile} context={step.context} "
            f"platform={step.policy.platform} registries={registries} "
            f"run_network={step.policy.run_network} pull={step.policy.pull} "
            f"repository_policy={oci_build_repository_policy_id(step)} "
            f"registry_retries={step.policy.registry_retries} tls_verify={str(step.policy.tls_verify).lower()} "
            f"archive={step.output.archive} metadata={step.output.metadata}"
        )
    if isinstance(step, ResolvedOciPublishStep):
        return (
            "    oci_publish: "
            f"publisher=skopeo archive={step.archive} "
            f"repository={step.target.repository_reference} reference_mode=digest "
            f"preserve_digests={str(step.policy.preserve_digests).lower()} "
            f"registry_retries={step.policy.registry_retries} parallel_copies={step.policy.parallel_copies} "
            f"tls_verify={str(step.policy.tls_verify).lower()} credentials=host:{step.credentials.username_host_env},host:{step.credentials.password_host_env} "
            "credential_values=redacted"
        )
    if isinstance(step, ResolvedOciTagAssertStep):
        return (
            "    oci_tag_assert: "
            f"inspector=skopeo target={step.target.tag_reference} "
            f"expected_digest={step.expected_digest} mutation=none "
            f"registry_retries={step.policy.registry_retries} "
            f"tls_verify={str(step.policy.tls_verify).lower()} "
            f"credentials=host:{step.credentials.username_host_env},host:{step.credentials.password_host_env} "
            "credential_values=redacted"
        )
    if isinstance(step, ResolvedKubernetesSetImageStep):
        return (
            "    kubernetes_set_image: "
            f"api={step.target.api.authority} namespace={step.target.namespace} "
            f"deployment={step.target.name} container={step.target.container} image={step.image} "
            f"mutation={step.policy.mutation} resource_version_test=true current_image_test=true "
            f"transport_retries={step.policy.transport_retries} tls_verify=true rollout_waited=false "
            f"credentials=host:{step.credentials.token_host_env},host:{step.credentials.ca_certificate_host_env} "
            "credential_values=redacted"
        )
    if isinstance(step, ResolvedKubernetesRolloutVerifyStep):
        return (
            "    kubernetes_rollout_verify: "
            f"api={step.target.api.authority} namespace={step.target.namespace} "
            f"deployment={step.target.name} container={step.target.container} "
            f"expected_generation={step.expected_generation} expected_image={step.expected_image} "
            f"timeout={step.timeout_seconds:g}s poll_interval={step.policy.poll_interval_seconds:g}s "
            f"request_timeout={step.policy.request_timeout_seconds}s transport_retries=0 mutation=none "
            f"credentials=host:{step.credentials.token_host_env},host:{step.credentials.ca_certificate_host_env} "
            "credential_values=redacted"
        )
    return None


def _step_summary(step: ResolvedPlanStep) -> tuple[str, ...]:
    workspace_source = _source_summary(step.workspace.source)
    cache_source = _source_summary(step.cache.source)
    transient_codes = ",".join(str(code) for code in step.retry.known_transient_exit_codes) or "-"
    retry_delays = ",".join(f"{delay:g}s" for delay in step.retry.delays_seconds) or "-"
    retry_reason = step.retry.reason or "-"
    timeout = (
        f"{step.timeout_seconds:g}s" if step.timeout_seconds is not None else "none (explicit null)"
    )
    success_codes = ",".join(str(code) for code in getattr(step, "success_codes", (0,)))
    working_directory = getattr(step, "working_directory", ".")
    condition = step.when.model_dump_json() if step.when is not None else "always"
    lines = [
        f"  {step.step_id}: {step.kind} | lifecycle={step.lifecycle.value} "
        f"when={condition} on_failure={step.on_failure.value} "
        f"success_codes={success_codes} timeout={timeout} "
        f"attempts={step.retry.max_attempts} known_transient_exit_codes={transient_codes} "
        f"retry_delays={retry_delays} retry_reason={retry_reason!r} working_directory={working_directory}",
        "    dataflow: "
        f"workspace={workspace_source} publish_workspace={str(step.workspace.publish).lower()} "
        f"cache={cache_source} publish_cache={str(step.cache.publish).lower()} "
        f"retain_cache={str(step.cache.retain).lower()} "
        f"imports.env={_csv(step.imports.env)}",
        "    authority: "
        f"egress={_egress(step.capabilities.egress)} "
        f"required_tool={step_execution_tool(step) or '-'}",
        f"    evidence: artifacts={_artifacts(step.artifacts)} outputs={_outputs(step.outputs)}",
    ]
    oci = _oci_step_summary(step)
    if oci is not None:
        lines.append(oci)
    if isinstance(step, ResolvedPreCommitStep):
        if_missing = step.if_missing.value if step.if_missing is not None else "embedded-config"
        lines.append(
            "    pre_commit: "
            f"if_missing={if_missing} "
            f"runner={step.runner_requirement} runtime_id={step.runtime_id}"
        )
    if step.kind == "extension":
        lines.append(
            "    extension: "
            f"name={step.extension} command={step.command} extension_id={step.extension_id}"
        )
    if isinstance(step, ResolvedAgentStep) and step.workload.harness == "native":
        selected = step.workload.native_agent
        lines.append(
            "    native_agent: "
            f"agent={selected.agent} executable={selected.definition.executable} "
            f"sha256={selected.executable_sha256} "
            f"host_support={_csv(step_execution_tool_names(step))}"
        )
    return tuple(lines)


def _source_summary(source: PlanWorkspaceSource | PlanCacheSource) -> str:
    if source.kind == "step":
        return f"{source.kind}:{source.step_id}"
    if source.kind == "retained":
        return f"{source.kind}:{source.reference_id}"
    return source.kind


def _csv(values: Iterable[object]) -> str:
    return ",".join(str(value) for value in values) or "-"


def _egress(egress: PlanStepEgress) -> str:
    if egress.mode == "none" or egress.mode == "host_unrestricted":
        return egress.mode
    rules = []
    for rule in egress.rules:
        destination = getattr(rule, "cidr", None) or getattr(rule, "hostname", None)
        rules.append(f"{destination}/{rule.protocol}:{','.join(str(port) for port in rule.ports)}")
    return f"{egress.mode}[{';'.join(rules)}]"


def _artifacts(values: Sequence[PlanArtifactDeclaration]) -> str:
    if not values:
        return "-"
    return ",".join(f"{item.path}:{item.retention}" for item in values)


def _outputs(values: Sequence[PlanPublishedOutput | PlanStreamOutput]) -> str:
    if not values:
        return "-"
    return ",".join(
        f"{item.name}={item.path if isinstance(item, PlanPublishedOutput) else item.stream}({item.max_bytes}B)"
        for item in values
    )
