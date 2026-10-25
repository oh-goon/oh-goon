from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from pydantic import TypeAdapter

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.fleet_plan_output_refs import step_output_references
from oh_goon.core.models.agent_providers import ResolvedNativeAgent
from oh_goon.core.models.fleet_plan_execution_policy import ExecutionPurpose
from oh_goon.core.models.fleet_plan_step_contract import (
    RetainedPlanCacheSource,
    StepPlanCacheSource,
    StepWorkspaceSource,
)
from oh_goon.core.models.fleet_plan_step_primitives import PlanStreamOutput
from oh_goon.core.path_authority import repository_relative_parts
from oh_goon.core.product_identity import stable_id

if TYPE_CHECKING:
    from oh_goon.core.models.fleet_plan import ResolvedPlanStep

_NATIVE_AGENT_ADAPTER = TypeAdapter(ResolvedNativeAgent)


def validate_resolved_plan(plan: Any) -> None:
    """Validate cross-step invariants that require the fully resolved plan graph."""
    from oh_goon.core.agent_review_plan import agent_phase_role, agent_review_phases

    native = getattr(plan, "native_agent", None)
    for step in plan.steps:
        if step.kind != "agent":
            continue
        if step.harness == "native":
            if native is None or step.native_agent != native:
                raise ValueError("Native Agent Step differs from the original Plan profile")
        elif native is not None:
            raise ValueError("A native execution profile cannot silently host built-in Agent steps")
    if plan.contract_version < 44:
        if any(
            isinstance(output, PlanStreamOutput)
            for step in plan.steps
            for output in getattr(step, "outputs", ())
        ):
            raise ValueError("native stream publication requires Plan contract version 44")
        if any(
            getattr(step, "harness", None) == "local-model-proposal"
            and getattr(step, "feedback_reference", None) is not None
            for step in plan.steps
        ):
            raise ValueError("proposal feedback requires Plan contract version 44")
    prior: set[str] = set()
    published: dict[str, set[str]] = {}
    published_workspaces: set[str] = set()
    published_caches: set[str] = set()
    finalizers_started = False
    for step in plan.steps:
        finalizers_started = _validate_step_structure(
            plan, step, prior, published_workspaces, published_caches, finalizers_started
        )
        _validate_step_dataflow(step, prior, published)
        _validate_step_paths(step)
        _validate_retry_safety(plan, step)
        prior.add(step.step_id)
        published[step.step_id] = {item.name for item in getattr(step, "outputs", ())}
        # A validated Agent proposal is a scalar Step output, not a hidden
        # model-controlled command or grant. Only this explicit harness exports it.
        if step.kind == "agent" and (
            step.harness == "local-model-proposal" or agent_phase_role(step) == "proposal"
        ):
            published[step.step_id].add("proposal")
        if step.workspace.publish:
            published_workspaces.add(step.step_id)
        if step.cache.publish:
            published_caches.add(step.step_id)
    _validate_kubernetes_rollout_links(plan)
    _validate_plan_requirements(plan)
    _validate_imports(plan)
    agent_review_phases(plan)
    _validate_plan_identity(plan)


def _validate_step_structure(
    plan: Any,
    step: Any,
    prior: set[str],
    published_workspaces: set[str],
    published_caches: set[str],
    finalizers_started: bool,
) -> bool:
    lifecycle = getattr(step.lifecycle, "value", step.lifecycle)
    failure = getattr(step.on_failure, "value", step.on_failure)
    if lifecycle == "finalizer":
        finalizers_started = True
        if failure != "continue":
            raise ValueError("Resolved Fleet finalizers must use on_failure=continue")
    elif finalizers_started:
        raise ValueError("Resolved Fleet finalizer steps must form one trailing suffix")
    source = step.workspace.source
    if step.kind == "command" and step.uv is not None and isinstance(source, StepWorkspaceSource):
        raise ValueError(
            f"Resolved Fleet uv-managed command step {step.step_id} must use repository workspace source; "
            "native uv preparation is bound to the admitted repository source"
        )
    if isinstance(source, StepWorkspaceSource):
        if source.step_id not in prior:
            raise ValueError(
                f"Resolved Fleet step {step.step_id} workspace must reference an earlier step: {source.step_id}"
            )
        if source.step_id not in published_workspaces:
            raise ValueError(
                f"Resolved Fleet step {step.step_id} workspace references step {source.step_id} "
                "which does not publish its workspace"
            )
    cache_source = step.cache.source
    if plan.execution.purpose is not ExecutionPurpose.CICD and any(
        artifact.retention == "content_store" for artifact in step.artifacts
    ):
        raise ValueError(
            f"Resolved Fleet step {step.step_id} content_store artifact retention requires "
            "execution.purpose=cicd; direct execution does not own retained publication"
        )
    if isinstance(cache_source, RetainedPlanCacheSource):
        if plan.execution.purpose is not ExecutionPurpose.CICD:
            raise ValueError(
                f"Resolved Fleet step {step.step_id} retained cache source requires "
                "execution.purpose=cicd; direct execution does not own retained cross-run inputs"
            )
        if re.fullmatch(r"ref-[0-9a-f]{32}", cache_source.reference_id) is None:
            raise ValueError(
                f"Resolved Fleet step {step.step_id} retained cache reference is unresolved"
            )
    if isinstance(cache_source, StepPlanCacheSource):
        if cache_source.step_id not in prior:
            raise ValueError(
                f"Resolved Fleet step {step.step_id} cache must reference an earlier step: {cache_source.step_id}"
            )
        if cache_source.step_id not in published_caches:
            raise ValueError(
                f"Resolved Fleet step {step.step_id} cache references step {cache_source.step_id} "
                "which does not publish its cache"
            )
    if plan.execution.purpose is ExecutionPurpose.CICD and step.kind == "command":
        executable = step.argv[0]
        if executable.startswith("/") or re.match(r"^[A-Za-z]:[\\/]", executable):
            raise ValueError(
                f"Resolved Fleet CI/CD step {step.step_id} uses an absolute executable path; "
                "use a bare execution-environment tool name or a repository-relative executable"
            )
    return finalizers_started


def _validate_retry_safety(plan: Any, step: Any) -> None:
    policy = step.retry
    if policy.max_attempts == 1:
        return
    if plan.execution.purpose is not ExecutionPurpose.CICD:
        raise ValueError(
            f"Resolved Fleet step {step.step_id} cannot use automatic retries outside strict CI/CD; "
            "outer replay requires attempt-private execution"
        )
    success_codes = set(getattr(step, "success_codes", (0,)))
    overlap = sorted(success_codes & set(policy.known_transient_exit_codes))
    if overlap:
        raise ValueError(
            f"Resolved Fleet step {step.step_id} retry codes cannot also be success codes: "
            + ", ".join(str(code) for code in overlap)
        )
    if step.capabilities.egress.mode != "none":
        raise ValueError(
            f"Resolved Fleet step {step.step_id} cannot use automatic retries with network egress; "
            "outer replay is allowed only for attempt-private, network-denied work"
        )


def _validate_step_dataflow(step: Any, prior: set[str], published: dict[str, set[str]]) -> None:
    imported_names = set(step.imports.env)
    for argument in _runtime_strings(step):
        match = re.fullmatch(r"\$([A-Za-z_][A-Za-z0-9_]*)", argument)
        if match and match.group(1) in imported_names:
            raise ValueError(
                f"Resolved Fleet step {step.step_id} argv contains shell-style environment reference "
                f"{argument!r}; Fleet argv has no shell expansion. Use the pipeline expression directly "
                "or let the program read the imported environment value."
            )
    condition = getattr(step, "when", None)
    if (
        condition is not None
        and getattr(condition, "kind", None) == "step_status"
        and condition.step_id not in prior
    ):
        raise ValueError(
            f"Resolved Fleet step {step.step_id} condition must reference an earlier step: {condition.step_id}"
        )
    references = [ref for value in _runtime_strings(step) for ref in step_output_references(value)]
    for binding in getattr(step, "environment", ()):
        references.extend(
            (part.step_id, part.output)
            for part in binding.parts
            if getattr(part, "kind", None) == "step_output"
        )
    for source_step, output_name in references:
        if source_step not in prior:
            raise ValueError(
                f"Resolved Fleet step {step.step_id} output reference must target an earlier step: {source_step}"
            )
        if output_name not in published.get(source_step, set()):
            raise ValueError(
                f"Resolved Fleet step {step.step_id} references undeclared output {source_step}.{output_name}"
            )


def _validate_step_paths(step: Any) -> None:
    values: list[tuple[str, str]] = []
    if hasattr(step, "working_directory"):
        values.append((step.working_directory, "working_directory"))
    if getattr(step, "kind", None) == "oci_build":
        values.append((step.containerfile, "Containerfile path"))
        values.append((step.context, "OCI build context"))
    elif getattr(step, "kind", None) == "oci_publish":
        values.append((step.archive, "OCI publish archive"))
    values.extend((item.path, "artifact path") for item in getattr(step, "artifacts", ()))
    values.extend(
        (item.path, "published output path")
        for item in getattr(step, "outputs", ())
        if not isinstance(item, PlanStreamOutput)
    )
    condition = getattr(step, "when", None)
    if condition is not None and getattr(condition, "kind", None) in {
        "path_exists",
        "path_missing",
    }:
        values.append((condition.path, "condition path"))
    for value, kind in values:
        try:
            repository_relative_parts(value, label=f"Resolved Fleet step {step.step_id} {kind}")
        except FleetPlanInvalid as exc:
            raise ValueError(str(exc)) from exc


def _validate_kubernetes_rollout_links(plan: Any) -> None:
    steps = {step.step_id: step for step in plan.steps}
    for step in plan.steps:
        if step.kind != "kubernetes_rollout_verify":
            continue
        generation_ref = step_output_references(step.expected_generation)
        image_ref = step_output_references(step.expected_image)
        if len(generation_ref) != 1 or len(image_ref) != 1:
            raise ValueError(
                f"Resolved Fleet rollout step {step.step_id} requires exact generation and image output references"
            )
        generation_source, generation_name = generation_ref[0]
        image_source, image_name = image_ref[0]
        if generation_source != image_source:
            raise ValueError(
                f"Resolved Fleet rollout step {step.step_id} generation/image must come from the same release step"
            )
        source = steps.get(generation_source)
        if source is None or source.kind != "kubernetes_set_image":
            raise ValueError(
                f"Resolved Fleet rollout step {step.step_id} must verify one kubernetes_set_image step"
            )
        if generation_name != "KUBERNETES_GENERATION" or image_name != "KUBERNETES_IMAGE":
            raise ValueError(
                f"Resolved Fleet rollout step {step.step_id} must consume canonical Kubernetes generation/image outputs"
            )
        if step.target != source.target or step.credentials != source.credentials:
            raise ValueError(
                f"Resolved Fleet rollout step {step.step_id} target/credentials must exactly match release step {source.step_id}"
            )


def _validate_plan_requirements(plan: Any) -> None:
    requirements = tuple(getattr(plan, "requirements", ()))
    if (
        requirements
        and plan.execution.purpose is not ExecutionPurpose.CICD
        and (
            plan.execution.purpose is not ExecutionPurpose.VERIFICATION
            or any(item.kind != "uv_project" for item in requirements)
        )
    ):
        raise ValueError(
            "Fleet repository requirements are supported only for execution.purpose=cicd"
        )
    identities = [item.model_dump_json() for item in requirements]
    if len(identities) != len(set(identities)):
        raise ValueError("Fleet repository requirements must be unique")
    for requirement in requirements:
        _validate_requirement_paths(requirement)


def _validate_imports(plan: Any) -> None:
    imports = tuple(getattr(plan, "imports", ()))
    if (
        any(item.kind == "goon" for item in imports)
        and plan.execution.purpose is not ExecutionPurpose.CICD
    ):
        if plan.execution.purpose is not ExecutionPurpose.VERIFICATION:
            raise ValueError(
                "Imported Goons are supported outside CI/CD only for execution.purpose=verification"
            )
        owners = sorted(
            item.name
            for item in imports
            if any(requirement.kind != "uv_project" for requirement in item.requirements)
        )
        if owners:
            raise ValueError(
                "Verification Goon imports cannot carry repository admission requirements; "
                "repository requirements remain CI/CD admission authority: " + ", ".join(owners)
            )
    step_ids = {step.step_id for step in plan.steps}
    import_ids = [item.import_id for item in imports]
    if len(import_ids) != len(set(import_ids)):
        raise ValueError("Resolved Fleet template import_id values must be unique")
    known_ids = set(import_ids)
    seen_ids: set[str] = set()
    by_id = {item.import_id: item for item in imports}
    for imported in imports:
        _validate_import_fact(imported, step_ids, known_ids, seen_ids, by_id)
        seen_ids.add(imported.import_id)
    _validate_sibling_import_scopes(imports)
    parents = {item.import_id: item.parent_import_id for item in imports}
    for import_id in import_ids:
        seen: set[str] = set()
        current: str | None = import_id
        while current is not None:
            if current in seen:
                raise ValueError("Resolved template import graph must be acyclic")
            seen.add(current)
            current = parents.get(current)


def _validate_import_fact(
    imported: Any,
    step_ids: set[str],
    known_ids: set[str],
    seen_ids: set[str],
    by_id: dict[str, Any],
) -> None:
    parent_id = imported.parent_import_id
    if parent_id is not None and parent_id not in known_ids:
        raise ValueError(
            f"Resolved template import {imported.import_id} references unknown parent {parent_id}"
        )
    if parent_id is not None and parent_id not in seen_ids:
        raise ValueError(
            f"Resolved template import {imported.import_id} must appear after its parent {parent_id}"
        )
    if parent_id == imported.import_id:
        raise ValueError("Resolved template import cannot parent itself")
    unknown = sorted(set(imported.step_ids) - step_ids)
    if unknown:
        raise ValueError(
            f"Resolved template import {imported.name} references unknown steps: "
            + ", ".join(unknown)
        )
    if len(imported.step_ids) != len(set(imported.step_ids)):
        raise ValueError(f"Resolved template import {imported.name} step_ids must be unique")
    identities = [item.model_dump_json() for item in imported.requirements]
    if len(identities) != len(set(identities)):
        raise ValueError(f"Resolved template import {imported.name} requirements must be unique")
    if parent_id is not None and not set(imported.step_ids).issubset(by_id[parent_id].step_ids):
        raise ValueError(
            f"Resolved template import {imported.import_id} step scope must be contained "
            f"by parent {parent_id}"
        )
    for requirement in imported.requirements:
        _validate_requirement_paths(requirement, prefix=f"imported {imported.kind} {imported.name}")


def _validate_sibling_import_scopes(imports: tuple[Any, ...]) -> None:
    siblings: dict[str | None, list[Any]] = {}
    for imported in imports:
        siblings.setdefault(imported.parent_import_id, []).append(imported)
    for parent_id, items in siblings.items():
        owned: dict[str, str] = {}
        for imported in items:
            for step_id in imported.step_ids:
                previous = owned.get(step_id)
                if previous is not None:
                    scope = parent_id or "<root>"
                    raise ValueError(
                        f"Resolved sibling imports under {scope} overlap on step {step_id}: "
                        f"{previous}, {imported.import_id}"
                    )
                owned[step_id] = imported.import_id


def _validate_requirement_paths(requirement: Any, *, prefix: str = "") -> None:
    paths: tuple[tuple[str, str], ...] = ()
    kind = getattr(requirement, "kind", None)
    if kind in {"path_exists", "file_exists", "folder_exists"}:
        paths = ((requirement.path, f"repository requirement {kind} path"),)
    for value, label in paths:
        scoped = f"{prefix} {label}".strip()
        try:
            repository_relative_parts(value, label=f"Resolved Fleet {scoped}")
        except FleetPlanInvalid as exc:
            raise ValueError(str(exc)) from exc


def _validate_plan_identity(plan: Any) -> None:
    sources = plan.skill_sources
    if tuple(item.name for item in sources) != tuple(sorted({item.name for item in sources})):
        raise ValueError("Resolved Skill sources must have unique sorted names")
    payload = {
        "contract_version": plan.contract_version,
        "name": plan.name,
        "description": plan.description,
        "execution": plan.execution.model_dump(mode="json", exclude_computed_fields=True),
        "requirements": [
            item.model_dump(mode="json", exclude_computed_fields=True)
            for item in getattr(plan, "requirements", ())
        ],
        "imports": [
            item.model_dump(mode="json", exclude_computed_fields=True)
            for item in getattr(plan, "imports", ())
        ],
        "steps": [
            step.model_dump(mode="json", exclude_computed_fields=True) for step in plan.steps
        ],
        **(
            {"native_agent": _NATIVE_AGENT_ADAPTER.dump_python(plan.native_agent, mode="json")}
            if getattr(plan, "native_agent", None) is not None
            else {}
        ),
        **(
            {"skill_sources": [item.model_dump(mode="json") for item in sources]} if sources else {}
        ),
    }
    if plan.plan_id != stable_id("fleet-plan", payload):
        raise ValueError("Resolved Fleet plan_id must match canonical resolved content")


def _runtime_strings(step: ResolvedPlanStep) -> tuple[str, ...]:
    if step.kind == "agent" and step.feedback_reference is not None:
        return (step.feedback_reference,)
    if step.kind == "command":
        return step.argv
    if step.kind == "extension":
        return step.args
    if step.kind == "git":
        return step.argv
    if step.kind == "oci_tag_assert":
        return (step.expected_digest,)
    if step.kind == "kubernetes_set_image":
        return (step.image,)
    if step.kind == "kubernetes_rollout_verify":
        return (step.expected_generation, step.expected_image)
    return ()
