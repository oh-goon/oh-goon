from __future__ import annotations

from oh_goon.core.models.fleet_plan import (
    ResolvedFleetPlan,
    ResolvedPreCommitStep,
    ResolvedStepSource,
)
from oh_goon.core.models.fleet_plan_evidence import PlanExecutionInputEvidence
from oh_goon.core.models.fleet_plan_requirements import RepositoryRequirement
from oh_goon.core.models.game_tape import (
    GameTapeEnvironmentFingerprint,
    GameTapeIssueFocus,
    GameTapeReceipt,
    GameTapeRepositoryExecution,
    GameTapeRequirementFact,
    GameTapeRuntimeFact,
    GameTapeStepExecution,
    GameTapeSummary,
    GameTapeToolFingerprint,
)
from oh_goon.core.models.operation import FleetPlanOperationEvidence, OperationReport
from oh_goon.core.models.run import FleetRunDetail
from oh_goon.core.models.target import FleetPlanTarget
from oh_goon.core.vocabulary import GameTapeStatus

GAME_TAPE_ISSUE_FOCUS_LIMIT = 5


def environment_fingerprint(
    target: FleetPlanTarget | None,
    report: OperationReport | None,
    execution_inputs: PlanExecutionInputEvidence | None,
) -> GameTapeEnvironmentFingerprint:
    before = report.provenance.before if report is not None else None
    plan = target.plan if target is not None else None
    source_sha = target.source.sha256 if target is not None else None
    admission = execution_inputs.repository_admission if execution_inputs is not None else None
    return GameTapeEnvironmentFingerprint(
        repository_head_sha=before.head_sha if before is not None else None,
        branch_state=before.branch_state.value if before is not None else None,
        branch=before.branch if before is not None else None,
        plan_source_sha256=source_sha,
        plan_contract_version=plan.contract_version if plan is not None else None,
        execution_input_id=execution_inputs.input_id if execution_inputs is not None else None,
        requirements_validation_id=admission.validation_id if admission is not None else None,
        base_workspace_sha256=(
            execution_inputs.base_workspace.sha256 if execution_inputs is not None else None
        ),
        runtime_root_sha256=execution_inputs.runtime_root.sha256
        if execution_inputs is not None
        else None,
        host_toolchain_id=execution_inputs.host_toolchain_id
        if execution_inputs is not None
        else None,
        isolation_backend_id=(
            execution_inputs.isolation_backend.backend_id if execution_inputs is not None else None
        ),
        isolation_configuration_id=(
            execution_inputs.isolation_backend.configuration_id
            if execution_inputs is not None
            else None
        ),
        isolation_kernel_boundary=(
            execution_inputs.isolation_backend.kernel_boundary
            if execution_inputs is not None
            else None
        ),
        isolation_remote_execution=(
            execution_inputs.isolation_backend.remote_execution
            if execution_inputs is not None
            else None
        ),
        isolation_experimental=(
            execution_inputs.isolation_backend.experimental
            if execution_inputs is not None
            else None
        ),
        host_environment_names=(
            execution_inputs.host_environment_names if execution_inputs is not None else ()
        ),
        managed_input_count=len(execution_inputs.managed_inputs)
        if execution_inputs is not None
        else 0,
        content_input_count=len(execution_inputs.content_inputs)
        if execution_inputs is not None
        else 0,
        tools=(
            tuple(
                GameTapeToolFingerprint(
                    name=item.name,
                    origin="host",
                    sha256=item.sha256,
                    byte_count=item.byte_count,
                )
                for item in execution_inputs.host_tools
            )
            if execution_inputs is not None
            else ()
        ),
        runtime_facts=_runtime_facts(plan),
    )


def _runtime_facts(plan: ResolvedFleetPlan | None) -> tuple[GameTapeRuntimeFact, ...]:
    facts: list[GameTapeRuntimeFact] = []
    seen: set[tuple[str, str]] = set()
    if plan is not None:
        for step in plan.steps:
            if not isinstance(step, ResolvedPreCommitStep):
                continue
            candidates = (
                ("pre-commit", step.runner_requirement.removeprefix("pre-commit==")),
            )
            for name, version in candidates:
                key = (name, version)
                if key not in seen:
                    seen.add(key)
                    facts.append(
                        GameTapeRuntimeFact(
                            name=name,
                            version=version,
                            source="declared_managed_runtime",
                        )
                    )
    return tuple(facts)


def step_source_map(plan: ResolvedFleetPlan | None) -> dict[str, ResolvedStepSource]:
    return {item.step_id: item for item in plan.step_sources} if plan is not None else {}


def plan_source_label(target: FleetPlanTarget | None) -> str:
    if target is None:
        return "resolved plan"
    source = target.source
    if source.kind == "team_goon":
        return "Team goon.yml"
    if source.kind == "shared_plan":
        return f"Shared Plan · {source.plan_key}"
    return "plan file"


def requirement_facts(
    requirements: tuple[RepositoryRequirement, ...],
) -> tuple[GameTapeRequirementFact, ...]:
    return tuple(
        GameTapeRequirementFact(
            order=order, kind=item.kind, description=_requirement_description(item)
        )
        for order, item in enumerate(requirements, start=1)
    )


def _requirement_description(requirement: RepositoryRequirement) -> str:
    data = requirement.model_dump(mode="json")
    kind = str(data.pop("kind"))
    if not data:
        return kind.replace("_", " ")
    facts = ", ".join(f"{key}={value}" for key, value in data.items())
    return f"{kind.replace('_', ' ')} · {facts}"



def _fleet_plan_result(report: OperationReport):
    evidence = report.evidence
    return evidence.plan if isinstance(evidence, FleetPlanOperationEvidence) else None

def project_receipt(detail: FleetRunDetail) -> GameTapeReceipt:
    run = detail.run
    started_at = getattr(run, "started_at", None)
    finished_at = getattr(run, "finished_at", None)
    admitted_counts = tuple(
        result.execution_inputs.repository_admission.requirement_count
        for report in detail.operations
        if (result := _fleet_plan_result(report)) is not None
        and result.execution_inputs is not None
        and result.execution_inputs.repository_admission is not None
    )
    return GameTapeReceipt(
        requirements_admitted=min(admitted_counts) if admitted_counts else None,
        accepted_at=run.accepted_at,
        started_at=started_at,
        finished_at=finished_at,
    )


def project_summary(repositories: tuple[GameTapeRepositoryExecution, ...]) -> GameTapeSummary:
    steps = tuple(step for repository in repositories for step in repository.steps)
    return GameTapeSummary(
        repositories=len(repositories),
        passed=sum(item.status is GameTapeStatus.PASSED for item in repositories),
        failed=sum(item.status is GameTapeStatus.FAILED for item in repositories),
        attention_required=sum(
            item.status is GameTapeStatus.ATTENTION_REQUIRED for item in repositories
        ),
        not_run=sum(item.status is GameTapeStatus.NOT_RUN for item in repositories),
        planned_steps=len(steps),
        executed_steps=sum(
            item.status not in {GameTapeStatus.PLANNED, GameTapeStatus.NOT_RUN} for item in steps
        ),
        passed_steps=sum(item.status is GameTapeStatus.PASSED for item in steps),
        failed_steps=sum(item.status is GameTapeStatus.FAILED for item in steps),
        skipped_steps=sum(item.status is GameTapeStatus.SKIPPED for item in steps),
        not_run_steps=sum(item.status is GameTapeStatus.NOT_RUN for item in steps),
    )


def project_repository_failure_focus(
    repositories: tuple[GameTapeRepositoryExecution, ...],
) -> tuple[GameTapeIssueFocus, ...]:
    """Interpret repository/step failures once in canonical repository/plan order."""

    failures: list[GameTapeIssueFocus] = []
    for repository in repositories:
        failed_steps = tuple(
            step for step in repository.steps if step.status is GameTapeStatus.FAILED
        )
        if repository.status is GameTapeStatus.FAILED and not failed_steps:
            failures.append(
                GameTapeIssueFocus(
                    scope="REPOSITORY",
                    repository_execution_id=repository.repository_execution_id,
                    repository_id=repository.repository.repository_id,
                    repository_name=repository.repository.name,
                    occurred_at=repository.finished_at or repository.started_at,
                )
            )
        for step in failed_steps:
            failure = step.failure
            failures.append(
                GameTapeIssueFocus(
                    scope="STEP",
                    repository_execution_id=repository.repository_execution_id,
                    repository_id=repository.repository.repository_id,
                    repository_name=repository.repository.name,
                    step_execution_id=step.step_execution_id,
                    step_id=step.step_id,
                    occurred_at=step.finished_at or step.started_at or repository.finished_at,
                    code=failure.reason_code if failure is not None else None,
                    message=step.reason,
                    exit_code=failure.exit_code if failure is not None else step.exit_code,
                    signature_id=failure.signature_id if failure is not None else None,
                )
            )
    return tuple(failures)


def project_issue_focus(
    detail: FleetRunDetail,
    repository_failures: tuple[GameTapeIssueFocus, ...],
) -> tuple[int, tuple[GameTapeIssueFocus, ...]]:
    """Bound canonical failures without reinterpreting repository/step evidence."""

    run_failure = getattr(detail.run, "failure", None)
    run_focus = (
        (
            GameTapeIssueFocus(
                scope="RUN",
                occurred_at=getattr(detail.run, "finished_at", None),
                code=run_failure.code,
                message=run_failure.message,
            ),
        )
        if run_failure is not None
        else ()
    )
    ordered = run_focus + repository_failures
    visible = ordered[:GAME_TAPE_ISSUE_FOCUS_LIMIT]
    return len(ordered), visible
