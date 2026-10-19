from __future__ import annotations

import hashlib
import json
from pathlib import Path

from oh_goon.core.models.explanation import ReasonCode
from oh_goon.core.models.fleet_observation import (
    DependencyObservationSnapshot,
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
from oh_goon.integrations.hashmarks.dependencies import observe_locked_dependencies
from tests.factories.fleet_plan_targets import structural_execution_identity
from tests.factories.operation_reports import make_operation_report


def dependency_snapshot(
    repository: Path, *packages: tuple[str, str]
) -> DependencyObservationSnapshot:
    repository.mkdir(parents=True, exist_ok=True)
    chunks = [
        "version=1",
        "revision=1",
        "[[package]]",
        'name="fixture-application"',
        'version="1"',
        'source={editable="."}',
    ]
    if packages:
        chunks.append(
            "dependencies=["
            + ",".join("{name=" + json.dumps(name) + "}" for name, version in packages)
            + "]"
        )
    for name, version in packages:
        chunks.extend(
            [
                "[[package]]",
                "name=" + json.dumps(name),
                "version=" + json.dumps(version),
                'source={registry="https://pypi.org/simple"}',
            ]
        )
    (repository / "uv.lock").write_text("\n".join(chunks) + "\n", encoding="utf-8")
    return observe_locked_dependencies(repository)


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
        uv_lock_sha256=hashlib.sha256((repository.path / "uv.lock").read_bytes()).hexdigest()
        if snapshot.sources
        else None,
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
