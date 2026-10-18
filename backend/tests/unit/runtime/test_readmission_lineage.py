from __future__ import annotations

from pathlib import Path

import pytest

from oh_goon.core.capability_registry import resolve_capability_intent
from oh_goon.core.errors import ExecutorPlacementLost, JournalConflict, StateConflict
from oh_goon.core.product_identity import repository_attempt_operation_id
from oh_goon.core.models.execution_identity import ExecutionIdentity
from oh_goon.core.models.explanation import ReasonCode
from oh_goon.core.models.fleet_executor import (
    ExecutorCapacityEvidenceInput,
    FleetExecutorIdentity,
    FleetExecutorPlacement,
    PlanExecutorCapacityEvidence,
)
from oh_goon.core.models.fleet_plan import ResolvedCommandStep, ResolvedFleetPlan
from oh_goon.core.models.fleet_plan_evidence import FleetPlanResult, PlanIsolationBackendEvidence
from oh_goon.core.models.fleet_plan_execution_policy import (
    CicdExecutionPolicy,
)
from oh_goon.core.models.fleet_plan_isolation import (
    CicdResourcePolicy,
)
from oh_goon.core.models.fleet_plan_step_contract import PlanStepCapabilities
from oh_goon.core.models.operation import FleetPlanOperationEvidence
from oh_goon.core.models.repository import RepositoryIdentity
from oh_goon.core.models.run import FleetRunFailure
from oh_goon.core.models.run_admission import (
    FleetRunAdmissionIntent,
    StrictFleetExecutionAdmission,
    fleet_repository_scope,
)
from oh_goon.core.models.target import FleetPlanTarget
from oh_goon.core.vocabulary import (
    FleetRunFailureStage,
    FleetRunSource,
    FleetRunTerminalIntent,
    OperationAction,
    Verdict,
)
from oh_goon.runtime.database import LocalDatabase
from oh_goon.runtime.journal import Journal
from tests.factories.fleet_plan_targets import structural_fleet_plan_target
from tests.factories.journal import admit_run, commit_operation, start_repository, start_run, terminalize_run
from tests.factories.operation_reports import make_operation_report
from tests.factories.run_admission import repository_admissions_for


def _target() -> FleetPlanTarget:
    plan = ResolvedFleetPlan.create(
        name="readmission-lineage",
        description="",
        execution=CicdExecutionPolicy(
            trust_repository_code=True,
            resources=CicdResourcePolicy(cpu_millis=1000, memory_mib=1024, max_processes=256),
        ),
        requirements=(),
        steps=(
            ResolvedCommandStep(
                timeout_seconds=3600,
                step_id="noop",
                argv=("true",),
                capabilities=PlanStepCapabilities(),
            ),
        ),
    )
    return structural_fleet_plan_target(plan)


def _capacity(run_id: str, backend: PlanIsolationBackendEvidence) -> PlanExecutorCapacityEvidence:
    return PlanExecutorCapacityEvidence.create(
        ExecutorCapacityEvidenceInput(
            fleet_run_id=run_id,
            executor=FleetExecutorIdentity(executor_id="executor-a", generation=1),
            source="remote_operator_attested",
            backend_configuration_id=backend.configuration_id,
            capacity_cpu_millis=4000,
            capacity_memory_bytes=4 * 1024 * 1024 * 1024,
            capacity_max_processes=None,
            requested_parallelism=1,
            admitted_parallelism=1,
            per_attempt_cpu_millis=1000,
            per_attempt_memory_bytes=1024 * 1024 * 1024,
            per_attempt_max_processes=None,
            pool_id="pool-test",
            placement_policy="priority_rendezvous_fifo_v2",
            placement_rank=0,
            executor_priority=0,
        )
    )


def _intent(
    run_id: str,
    repository: RepositoryIdentity,
    *,
    parent_id: str | None = None,
    source: FleetRunSource = FleetRunSource.MANUAL,
) -> FleetRunAdmissionIntent:
    target = _target()
    resolved = resolve_capability_intent(OperationAction.EXECUTE_PLAN, target)
    backend = PlanIsolationBackendEvidence(
        backend_id="remote-test",
        configuration_id="backend-config",
        kernel_boundary="virtual_machine",
        remote_execution=True,
        experimental=False,
    )
    return FleetRunAdmissionIntent(
        source=source,
        target=target,
        capability=resolved.definition.durable_snapshot(),
        execution=StrictFleetExecutionAdmission(
            placement=FleetExecutorPlacement(
                isolation_backend=backend,
                executor_capacity=_capacity(run_id, backend),
                executor_trust_epoch=0,
            ),
            readmitted_from_run_id=parent_id,
        ),
        repository_admissions=repository_admissions_for(target, (repository,)),
    )


def _placement_lost_failure() -> FleetRunFailure:
    return FleetRunFailure.from_exception(
        ExecutorPlacementLost("executor disappeared"),
        stage=FleetRunFailureStage.EXECUTION,
    )


def _other_failure() -> FleetRunFailure:
    return FleetRunFailure.from_exception(
        StateConflict("other failure"),
        stage=FleetRunFailureStage.EXECUTION,
    )


def test_journal_allows_one_exact_child_for_placement_lost_parent(tmp_path: Path) -> None:
    journal = Journal(LocalDatabase(tmp_path / "readmission-lineage.db"))
    repository = RepositoryIdentity(path=tmp_path / "repo")
    scope = fleet_repository_scope({repository.repository_id})

    admit_run(journal, "parent", _intent("parent", repository), scope, [repository])
    terminalize_run(journal, "parent", FleetRunTerminalIntent.FAIL, _placement_lost_failure())
    admit_run(journal, "child", _intent("child", repository, parent_id="parent"), scope, [repository])

    child = journal.read_view().readmission_child("parent")
    assert child is not None
    assert child.run.fleet_run_id == "child"


def test_journal_rejects_second_readmission_child_even_after_first_child_finishes(
    tmp_path: Path,
) -> None:
    journal = Journal(LocalDatabase(tmp_path / "readmission-one-child.db"))
    repository = RepositoryIdentity(path=tmp_path / "repo")
    scope = fleet_repository_scope({repository.repository_id})

    admit_run(journal, "parent", _intent("parent", repository), scope, [repository])
    terminalize_run(journal, "parent", FleetRunTerminalIntent.FAIL, _placement_lost_failure())
    admit_run(journal, "child-a", _intent("child-a", repository, parent_id="parent"), scope, [repository])
    terminalize_run(journal, "child-a", FleetRunTerminalIntent.FAIL, _other_failure())

    with pytest.raises(JournalConflict, match="already has child child-a"):
        admit_run(journal, "child-b", _intent("child-b", repository, parent_id="parent"), scope, [repository])


def test_journal_rejects_readmission_from_non_placement_failure(tmp_path: Path) -> None:
    journal = Journal(LocalDatabase(tmp_path / "readmission-parent-status.db"))
    repository = RepositoryIdentity(path=tmp_path / "repo")
    scope = fleet_repository_scope({repository.repository_id})

    admit_run(journal, "parent", _intent("parent", repository), scope, [repository])
    terminalize_run(journal, "parent", FleetRunTerminalIntent.FAIL, _other_failure())

    with pytest.raises(JournalConflict, match="EXECUTOR_PLACEMENT_LOST"):
        admit_run(journal, "child", _intent("child", repository, parent_id="parent"), scope, [repository])



def test_journal_rejects_readmission_that_changes_execution_source(tmp_path: Path) -> None:
    journal = Journal(LocalDatabase(tmp_path / "readmission-source.db"))
    repository = RepositoryIdentity(path=tmp_path / "repo")
    scope = fleet_repository_scope({repository.repository_id})

    admit_run(journal, "parent", _intent("parent", repository), scope, [repository])
    terminalize_run(journal, "parent", FleetRunTerminalIntent.FAIL, _placement_lost_failure())

    with pytest.raises(JournalConflict, match="another execution source"):
        admit_run(
            journal,
            "child",
            _intent("child", repository, parent_id="parent", source=FleetRunSource.CICD),
            scope,
            [repository],
        )

def test_journal_rejects_readmission_that_changes_repository_scope(tmp_path: Path) -> None:
    journal = Journal(LocalDatabase(tmp_path / "readmission-scope.db"))
    first = RepositoryIdentity(path=tmp_path / "repo-a")
    second = RepositoryIdentity(path=tmp_path / "repo-b")
    original_scope = fleet_repository_scope({first.repository_id})
    changed_scope = fleet_repository_scope({second.repository_id})

    admit_run(journal, "parent", _intent("parent", first), original_scope, [first])
    terminalize_run(journal, "parent", FleetRunTerminalIntent.FAIL, _placement_lost_failure())

    with pytest.raises(JournalConflict, match="repository scope"):
        admit_run(
            journal,
            "child",
            _intent("child", second, parent_id="parent"),
            changed_scope,
            [second],
        )


def test_journal_rejects_readmission_with_changed_execution_identity(tmp_path: Path) -> None:
    journal = Journal(LocalDatabase(tmp_path / "readmission-identity.db"))
    repository = RepositoryIdentity(path=tmp_path / "repo")
    scope = fleet_repository_scope({repository.repository_id})

    admit_run(journal, "parent", _intent("parent", repository), scope, [repository])
    terminalize_run(journal, "parent", FleetRunTerminalIntent.FAIL, _placement_lost_failure())

    child = _intent("child", repository, parent_id="parent")
    changed_target = child.target.model_copy(
        update={"identity": ExecutionIdentity(digest="sha256:" + "2" * 64)}
    )
    changed_child = child.model_copy(update={"target": changed_target})
    with pytest.raises(JournalConflict, match="another operation intent"):
        admit_run(journal, "child", changed_child, scope, [repository])


def test_journal_rejects_readmission_after_parent_completed_repository_effect(
    tmp_path: Path,
) -> None:
    journal = Journal(LocalDatabase(tmp_path / "readmission-completed-effect.db"))
    repository = RepositoryIdentity(path=tmp_path / "repo")
    scope = fleet_repository_scope({repository.repository_id})

    parent_intent = _intent("parent", repository)
    admit_run(journal, "parent", parent_intent, scope, [repository])
    start_run(journal, "parent")
    operation_id = start_repository(journal, "parent", repository)
    lifecycle = journal.execution_lifecycle_observer("parent", repository.repository_id, operation_id)
    lifecycle("noop", 1, "STEP_REACHED")
    lifecycle("noop", 1, "STEP_STARTED")
    lifecycle("noop", 1, "STEP_SUCCEEDED", returncode=0)
    commit_operation(
        journal,
        make_operation_report(
            repository,
            OperationAction.EXECUTE_PLAN,
            verdict=Verdict.READY,
            fleet_run_id="parent",
            operation_id=repository_attempt_operation_id("parent", repository.repository_id),
            reason_code=ReasonCode.READY,
            target=parent_intent.target,
            evidence=FleetPlanOperationEvidence(
                kind="FLEET_PLAN",
                plan=FleetPlanResult(plan_id=parent_intent.target.plan.plan_id, steps=()),
            ),
        ),
    )
    terminalize_run(journal, "parent", FleetRunTerminalIntent.FAIL, _placement_lost_failure())

    with pytest.raises(JournalConflict, match="durably completed repository effects"):
        admit_run(journal, "child", _intent("child", repository, parent_id="parent"), scope, [repository])


def test_journal_rejects_readmission_that_reuses_parent_run_identity(tmp_path: Path) -> None:
    journal = Journal(LocalDatabase(tmp_path / "readmission-self-parent.db"))
    repository = RepositoryIdentity(path=tmp_path / "repo")
    scope = fleet_repository_scope({repository.repository_id})

    with pytest.raises(JournalConflict, match="cannot reuse its parent run identity"):
        admit_run(journal, "same", _intent("same", repository, parent_id="same"), scope, [repository])


def test_journal_requires_readmission_parent_to_exist(tmp_path: Path) -> None:
    journal = Journal(LocalDatabase(tmp_path / "readmission-parent-exists.db"))
    repository = RepositoryIdentity(path=tmp_path / "repo")
    scope = fleet_repository_scope({repository.repository_id})

    with pytest.raises(JournalConflict, match="parent does not exist"):
        admit_run(journal, "child", _intent("child", repository, parent_id="missing"), scope, [repository])
