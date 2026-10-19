from __future__ import annotations

import hashlib
import posixpath
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING

from oh_goon.core.errors import FleetExtensionInvalid, FleetPlanInvalid
from oh_goon.core.fleet_plan_git import resolve_git_step
from oh_goon.core.fleet_plan_values import PipelineValueCompiler
from oh_goon.core.models.fleet_plan import (
    AgentPlanStep,
    CommandPlanStep,
    ExtensionPlanStep,
    GitPlanStep,
    KubernetesRolloutVerifyPlanStep,
    KubernetesSetImagePlanStep,
    OciBuildPlanStep,
    OciPublishPlanStep,
    OciTagAssertPlanStep,
    PlanStep,
    PreCommitPlanStep,
    ResolvedCommandStep,
    ResolvedExtensionStep,
    ResolvedLocalSearchAgentStep,
    ResolvedPlanStep,
)
from oh_goon.core.models.fleet_plan_observation_steps import (
    DependencyInventoryPlanStep,
    ResolvedDependencyInventoryStep,
)
from oh_goon.core.path_authority import repository_relative_parts
from oh_goon.engine.project import read_pyproject
from oh_goon.services.fleet_plan_capability_compilation import compile_step_capabilities
from oh_goon.services.fleet_plan_kubernetes_resolution import (
    resolve_kubernetes_rollout_verify,
    resolve_kubernetes_set_image,
)
from oh_goon.services.fleet_plan_oci_publish_resolution import resolve_oci_publish
from oh_goon.services.fleet_plan_oci_resolution import resolve_oci_build
from oh_goon.services.fleet_plan_oci_tag_resolution import resolve_oci_tag_assert
from oh_goon.services.fleet_plan_precommit_resolution import resolve_pre_commit
from oh_goon.services.fleet_plan_resolution_context import FleetPlanResolutionContext

if TYPE_CHECKING:
    from oh_goon.core.models.agent_source import FrozenAgentStepHandoff


def resolve_plan_step(
    context: FleetPlanResolutionContext,
    step: PlanStep,
    *,
    plan_base: Path,
    step_id: str,
    value_compiler: PipelineValueCompiler,
    agent_handoff: object | None = None,
) -> ResolvedPlanStep:
    if isinstance(step, OciBuildPlanStep):
        return resolve_oci_build(step, step_id=step_id)
    if isinstance(step, OciPublishPlanStep):
        return resolve_oci_publish(step, step_id=step_id, compiler=value_compiler)
    if isinstance(step, OciTagAssertPlanStep):
        return resolve_oci_tag_assert(step, step_id=step_id, compiler=value_compiler)
    if isinstance(step, KubernetesSetImagePlanStep):
        return resolve_kubernetes_set_image(step, step_id=step_id, compiler=value_compiler)
    if isinstance(step, KubernetesRolloutVerifyPlanStep):
        return resolve_kubernetes_rollout_verify(step, step_id=step_id, compiler=value_compiler)
    if isinstance(step, GitPlanStep):
        capabilities = compile_step_capabilities(step, value_compiler)
        environment = compiled_environment(step, value_compiler)
        compiled_step = step.model_copy(update={"capabilities": capabilities})
        git_step = resolve_git_step(compiled_step, step_id=step_id, environment=environment)
        assert git_step is not None
        return git_step
    if isinstance(step, AgentPlanStep):
        from oh_goon.core.models.agent_source import FrozenAgentStepHandoff

        handoff = agent_handoff if agent_handoff is not None else context.agent_handoff
        if not isinstance(handoff, FrozenAgentStepHandoff):
            raise FleetPlanInvalid("Agent Step requires its original frozen handoff")
        if agent_handoff is None and context.agent_owner_path != plan_base / "goon.yml":
            raise FleetPlanInvalid("Agent Step is not bound to original frozen source owner")
        require_agent_step_handoff(
            step,
            handoff,
            owner_source_sha256=context.source_graph.node(plan_base / "goon.yml").source_sha256,
        )
        connection = context.agent_connection if step.harness.startswith("provider-") else None
        if step.harness.startswith("provider-") and callable(connection):
            connection = connection()
        resolved = resolve_delivered_local_search_agent_step(handoff, connection=connection)
        if step.feedback is not None:
            from oh_goon.core.fleet_plan_output_refs import step_output_reference
            from oh_goon.core.models.fleet_plan_step_primitives import StepStatusCondition

            reference = step_output_reference(step.feedback)
            if (
                step.harness not in ("local-model-skill-loop", "local-model-proposal")
                or reference is None
                or not isinstance(step.when, StepStatusCondition)
                or step.when.step_id != reference[0]
                or step.when.statuses != ("FAILED",)
            ):
                raise FleetPlanInvalid(
                    "Agent feedback requires one exact failed prior Step output and a matching FAILED-only condition"
                )
        return ResolvedLocalSearchAgentStep.model_validate(
            {
                **resolved.model_dump(),
                "step_id": step_id,
                "source_step_id": handoff.step_id,
                "feedback_reference": step.feedback,
                "when": step.when,
            }
        )
    capabilities = compile_step_capabilities(step, value_compiler)
    if isinstance(step, CommandPlanStep):
        if step.uv is not None:
            argv = list(step.argv)
            index = argv.index("--project")
            argv[index + 1] = posixpath.relpath(step.uv.project, step.working_directory)
            publications = {}
            for field in ("artifacts", "outputs"):
                declarations = []
                for declaration in getattr(step, field):
                    path = getattr(declaration, "path", None)
                    if path is not None:
                        repository_relative_parts(path, label=f"uv {field} path")
                        declaration = declaration.model_copy(
                            update={
                                "path": str(PurePosixPath(step.working_directory) / path),
                            }
                        )
                    declarations.append(declaration)
                publications[field] = tuple(declarations)
            step = step.model_copy(update={"argv": tuple(argv), **publications})
        return ResolvedCommandStep(
            **step.model_dump(exclude={"step_id", "capabilities"}),
            step_id=step_id,
            capabilities=capabilities,
            environment=compiled_environment(step, value_compiler),
        )
    if isinstance(step, DependencyInventoryPlanStep):
        return ResolvedDependencyInventoryStep(
            **step.model_dump(exclude={"step_id", "capabilities"}),
            step_id=step_id,
            capabilities=capabilities,
            environment=compiled_environment(step, value_compiler),
        )
    if isinstance(step, PreCommitPlanStep):
        return resolve_pre_commit(
            context.tools,
            step,
            step_id=step_id,
            value_compiler=value_compiler,
        )
    if isinstance(step, ExtensionPlanStep):
        return resolve_extension_step(
            context,
            step,
            step_id=step_id,
            value_compiler=value_compiler,
        )
    raise FleetPlanInvalid(f"Unsupported Fleet plan step kind: {step.kind}")


def resolve_extension_step(
    context: FleetPlanResolutionContext,
    step: ExtensionPlanStep,
    *,
    step_id: str,
    value_compiler: PipelineValueCompiler,
) -> ResolvedExtensionStep:
    if context.catalog is None or context.extension_root is None:
        raise FleetExtensionInvalid(
            f"Step {step.step_id} references extension {step.extension!r} but no TOML catalog was supplied"
        )
    matches = [item for item in context.catalog.extensions if item.name == step.extension]
    if not matches:
        raise FleetExtensionInvalid(f"Unknown Fleet extension: {step.extension}")
    declaration = matches[0]
    if step.command not in declaration.commands:
        raise FleetExtensionInvalid(
            f"Extension {step.extension} does not allow command {step.command!r}"
        )
    if context.extensions is None:
        raise FleetExtensionInvalid("Fleet extension store is not configured")
    managed = context.extensions.resolve_cached(declaration, catalog_root=context.extension_root)
    context.extensions.check_environment(managed.extension_id)
    entrypoint = extension_entrypoint(managed.project_path, step.extension, step.command)
    return ResolvedExtensionStep(
        step_id=step_id,
        lifecycle=step.lifecycle,
        extension=step.extension,
        extension_id=managed.extension_id,
        source=managed.source,
        revision=managed.revision,
        project_name=managed.project_name,
        project_fingerprint=managed.project_fingerprint,
        pyproject_sha256=managed.pyproject_sha256,
        uv_lock_sha256=managed.uv_lock_sha256,
        command=step.command,
        entrypoint=entrypoint,
        args=step.args,
        timeout_seconds=step.timeout_seconds,
        working_directory=step.working_directory,
        imports=step.imports,
        workspace=step.workspace,
        capabilities=compile_step_capabilities(step, value_compiler),
        environment=compiled_environment(step, value_compiler),
        success_codes=step.success_codes,
        on_failure=step.on_failure,
        when=step.when,
        retry=step.retry,
        artifacts=step.artifacts,
        outputs=step.outputs,
    )


def compiled_environment(step: PlanStep, compiler: PipelineValueCompiler):
    return compiler.compile_imports(env_names=step.imports.env)


def extension_entrypoint(project: Path, extension: str, command: str) -> str:
    payload = read_pyproject(project)
    project_table = payload.get("project")
    scripts = project_table.get("scripts") if isinstance(project_table, dict) else None
    entrypoint = scripts.get(command) if isinstance(scripts, dict) else None
    if not isinstance(entrypoint, str) or not entrypoint.strip():
        raise FleetExtensionInvalid(
            f"Managed extension {extension} no longer exposes a valid entry point for {command!r}"
        )
    return entrypoint


def require_agent_step_handoff(
    step: AgentPlanStep,
    handoff: "FrozenAgentStepHandoff",
    *,
    owner_source_sha256: str | None,
) -> None:
    """Verify the same authored Step binding at resolution and repair recapture."""
    from oh_goon.core.models.agent_source import FrozenAgentStepHandoff

    if (
        type(handoff) is not FrozenAgentStepHandoff
        or handoff.step_id != step.step_id
        or handoff.owner_source_sha256 != owner_source_sha256
    ):
        raise FleetPlanInvalid("Agent Step is not bound to original frozen source owner")
    handoff.verify()
    if (
        step.harness != handoff.harness
        or step.task != handoff.task
        or step.timeout_seconds != handoff.timeout_seconds
        or step.requested_effects != handoff.requested_effects
    ):
        raise FleetPlanInvalid("Agent Step meaning changed after source capture")


def resolve_delivered_local_search_agent_step(
    handoff: "FrozenAgentStepHandoff",
    *,
    connection=None,
) -> ResolvedLocalSearchAgentStep:
    """Preserve the typed Agent Step across the original resolved-Step boundary.

    This validates and freezes a *non-authorizing* typed Step. Only the original
    Fleet admission path may select it for an attempt. No alternative execution,
    permission, network access, or model recipient is introduced.
    """
    from oh_goon.core.models.agent_source import FrozenAgentStepHandoff

    if type(handoff) is not FrozenAgentStepHandoff:
        raise FleetPlanInvalid("resolved Agent Step requires original frozen handoff")
    handoff.verify()
    requested = handoff.requested_effects
    if (
        handoff.harness
        not in (
            "local-search",
            "local-model-search",
            "local-model-review",
            "local-model-tools",
            "local-model-files",
            "local-model-skill-resources",
            "local-model-skill-loop",
            "local-model-proposal",
            "provider-review",
            "provider-adjudicate",
            "provider-proposal",
        )
        or requested.repository_access != "read_only"
        or requested.capabilities.egress.mode != "none"
        or requested.capabilities.tools
        or requested.capabilities.nested_strict_execution
    ):
        raise FleetPlanInvalid("resolved Agent requires an explicitly bounded read-only harness")
    if handoff.harness == "local-search":
        if (
            requested.recipients
            or requested.model is not None
            or not handoff.task.startswith("search:")
        ):
            raise FleetPlanInvalid("resolved offline Agent requires fixed search and no recipient")
        term = handoff.task[7:].strip()
        if not term or len(term) > 200 or any(ord(ch) < 32 for ch in term):
            raise FleetPlanInvalid("resolved Agent search query must be bounded printable text")
    elif handoff.harness.startswith("provider-"):
        from oh_goon.core.models.agent_providers import AgentConnection

        if (
            not isinstance(connection, AgentConnection)
            or requested.recipients not in ((connection.provider_name,), ("configured_model",))
            or requested.model not in (None, connection.profile)
        ):
            raise FleetPlanInvalid(
                "provider Agent requires its explicitly selected recipient and profile"
            )
    else:
        if (
            requested.recipients != ("ollama_local",)
            or requested.model is None
            or not handoff.task.strip()
        ):
            raise FleetPlanInvalid("model Agent requires selected local Ollama recipient and model")
    return ResolvedLocalSearchAgentStep(
        step_id=handoff.step_id,
        argv=("python",),
        success_codes=(0,),
        timeout_seconds=handoff.timeout_seconds,
        delivery_sha256=hashlib.sha256(handoff.encode_prework_delivery()).hexdigest(),
        handoff_identity=handoff.handoff_identity,
        source_step_id=handoff.step_id,
        harness=handoff.harness,
        model=requested.model,
        connection=connection if handoff.harness.startswith("provider-") else None,
    )
