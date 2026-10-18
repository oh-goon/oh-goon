from __future__ import annotations

import hashlib

from oh_goon.core.models.explanation import ReasonCode
from oh_goon.core.models.fleet_observation import (
    DependencyObservation,
    DependencyObservationSnapshot,
    ObservationSourceEvidence,
)
from oh_goon.core.models.fleet_plan import ResolvedDependencyInventoryStep, ResolvedFleetPlan
from oh_goon.core.models.fleet_plan_evidence import (
    CompletedPlanStepExecution,
    FleetPlanResult,
    PlanOutputEvidence,
)
from oh_goon.core.models.fleet_plan_execution_policy import InspectionExecutionPolicy
from oh_goon.core.models.goon_plan_source import PlanFileSource
from oh_goon.core.models.operation import FleetPlanOperationEvidence
from oh_goon.core.models.provenance import RepositoryStateFingerprint
from oh_goon.core.models.repository import RepositoryIdentity
from oh_goon.core.models.target import FleetPlanTarget
from oh_goon.core.time import utc_now
from oh_goon.core.vocabulary import GitBranchState, OperationAction, Verdict, WorktreeState
from tests.factories.fleet_plan_targets import structural_execution_identity
from tests.factories.operation_reports import make_operation_report


def dependency_snapshot(*packages: tuple[str, str]) -> DependencyObservationSnapshot:
    return DependencyObservationSnapshot(
        sources=(ObservationSourceEvidence(path="uv.lock", sha256="1" * 64),),
        dependencies=tuple(
            DependencyObservation(
                package_name=name,
                relationship="transitive",
                resolved_version=version,
                resolved_source="uv.lock",
            )
            for name, version in packages
        ),
    )


def dependency_report(
    repository: RepositoryIdentity, snapshot: DependencyObservationSnapshot, *, state: str
):
    plan = ResolvedFleetPlan.create(
        name="dependency-observation-test",
        description="",
        execution=InspectionExecutionPolicy(),
        steps=(ResolvedDependencyInventoryStep(step_id="observe-dependencies"),),
    )
    target = FleetPlanTarget(
        kind="fleet_plan",
        plan=plan,
        source=PlanFileSource(sha256="0" * 64),
        identity=structural_execution_identity(plan),
    )
    now = utc_now()
    output = PlanOutputEvidence(sha256=hashlib.sha256(b"").hexdigest(), byte_count=0, line_count=0)
    step = CompletedPlanStepExecution(
        step_id="observe-dependencies",
        kind="dependency_inventory",
        started_at=now,
        finished_at=now,
        stdout_evidence=output,
        stderr_evidence=output,
        repository_state_before=state,
        repository_state_after=state,
        observations=(snapshot,),
    )
    report = make_operation_report(
        repository,
        OperationAction.EXECUTE_PLAN,
        Verdict.READY,
        ReasonCode.READY,
        fleet_run_id=f"fleet-{state}",
        target=target,
        evidence=FleetPlanOperationEvidence(
            kind="FLEET_PLAN", plan=FleetPlanResult(plan_id=plan.plan_id, steps=(step,))
        ),
    )
    fingerprint = RepositoryStateFingerprint(
        captured_at=now,
        head_sha=state,
        branch_state=GitBranchState.NAMED,
        branch="main",
        worktree_state=WorktreeState.CLEAN,
        pyproject_sha256="2" * 64,
        uv_lock_sha256="1" * 64,
    )
    return report.model_copy(
        update={
            "provenance": report.provenance.model_copy(
                update={
                    "started_at": now,
                    "finished_at": now,
                    "before": fingerprint,
                    "after": fingerprint,
                }
            )
        }
    )
