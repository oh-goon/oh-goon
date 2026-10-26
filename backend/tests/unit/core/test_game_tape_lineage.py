from __future__ import annotations

import hashlib

from oh_goon.core.models.fleet_plan import (
    ResolvedCommandStep,
    ResolvedFleetPlan,
    ResolvedStepSource,
)
from oh_goon.core.models.fleet_plan_evidence import (
    CompletedPlanStepExecution,
    FailedPlanStepTeardownEvidence,
    FleetPlanResult,
    PlanEgressEvidence,
    PlanOutputEvidence,
    PlanResourceEvidence,
    PlanStepAttemptEvidence,
    PlanStepSettlementEvidence,
    PlanToolEvidence,
)
from oh_goon.core.models.fleet_plan_execution_policy import MaintenanceExecutionPolicy
from oh_goon.core.models.fleet_plan_retry import (
    AttemptsExhaustedPlanRetryDecisionEvidence,
)
from oh_goon.core.models.fleet_plan_values import (
    HostEnvironmentPart,
    LiteralEnvironmentPart,
    ResolvedEnvironmentBinding,
)
from oh_goon.core.models.goon_plan_source import PlanFileSource
from oh_goon.core.models.target import FleetPlanTarget
from oh_goon.core.vocabulary import (
    GameTapeStatus,
)
from tests.factories.fleet_plan_targets import structural_execution_identity


def _output(text: str) -> PlanOutputEvidence:
    encoded = text.encode()
    return PlanOutputEvidence(
        sha256=hashlib.sha256(encoded).hexdigest(),
        byte_count=len(encoded),
        line_count=len(text.splitlines()),
    )


def _failed_attempt() -> PlanStepAttemptEvidence:
    return PlanStepAttemptEvidence(
        attempt=1,
        argv=("pytest",),
        returncode=1,
        stdout_evidence=_output("failed\n"),
        stderr_evidence=_output("attempt trace\n"),
        duration_ms=3000,
        repository_state_before="prepared",
        repository_state_after="prepared",
        tool=PlanToolEvidence(
            name="pytest",
            origin="host",
            sha256="1" * 64,
            byte_count=42,
        ),
        egress=PlanEgressEvidence(mode="none"),
        resources=PlanResourceEvidence(
            cpu_millis=1000,
            memory_bytes=128 * 1024 * 1024,
            max_processes=64,
            cpu_usage_usec=10_000,
            memory_peak_bytes=32 * 1024 * 1024,
            pids_peak=4,
            oom_kill_count=0,
        ),
        retry=AttemptsExhaustedPlanRetryDecisionEvidence(
            exit_code=1,
            policy_reason="configured attempts exhausted",
        ),
    )


def _plan() -> ResolvedFleetPlan:
    environment = (
        ResolvedEnvironmentBinding(
            name="REGISTRY_TOKEN",
            parts=(HostEnvironmentPart(name="REGISTRY_TOKEN"),),
        ),
        ResolvedEnvironmentBinding(
            name="LABEL",
            parts=(LiteralEnvironmentPart(value="secret-literal"),),
        ),
    )
    return ResolvedFleetPlan.create(
        name="game-tape-test",
        description="",
        execution=MaintenanceExecutionPolicy(),
        steps=(
            ResolvedCommandStep(
                step_id="prepare",
                argv=("echo", "prepare"),
                timeout_seconds=30,
                environment=environment,
            ),
            ResolvedCommandStep(step_id="test", argv=("pytest",), timeout_seconds=30),
            ResolvedCommandStep(step_id="publish", argv=("echo", "publish"), timeout_seconds=30),
        ),
        step_sources=(
            ResolvedStepSource(step_id="prepare", source="goon.yml", line=10, column=5),
            ResolvedStepSource(step_id="test", source="goon.yml", line=14, column=5),
            ResolvedStepSource(step_id="publish", source="goon.yml", line=18, column=5),
        ),
    )


def _target(plan: ResolvedFleetPlan) -> FleetPlanTarget:
    return FleetPlanTarget(
        kind="fleet_plan",
        plan=plan,
        source=PlanFileSource(sha256="0" * 64),
        identity=structural_execution_identity(plan),
    )


def _assert_tape_derived_views_are_not_source_facts(tape) -> None:
    source_facts = tape.model_dump(mode="json", exclude_computed_fields=True)
    normal = tape.model_dump(mode="json")
    for field in ("summary", "team", "observability"):
        assert field not in source_facts
        assert field in normal


def test_game_tape_marks_successful_command_with_failed_teardown_as_failed() -> None:
    from oh_goon.core.game_tape_step_projection import project_steps

    planned = ResolvedCommandStep(timeout_seconds=60, step_id="cleanup-sensitive", argv=("true",))
    execution = CompletedPlanStepExecution(
        step_id="cleanup-sensitive",
        kind="command",
        settlement=PlanStepSettlementEvidence(
            outcome="SUCCESS", cleanup="VERIFIED", reason="ACCEPTED_EXIT", observed_returncode=0
        ),
        argv=("true",),
        stdout_evidence=_output(""),
        stderr_evidence=_output(""),
        repository_state_before="before",
        repository_state_after="after",
        teardown=FailedPlanStepTeardownEvidence(
            attempt_roots_observed=1, reason_code="ROOT_REMAINS"
        ),
    )
    projected = project_steps(
        "run-teardown",
        "repo-teardown",
        (planned,),
        (execution,),
        step_sources={},
        source_label="goon.yml",
        terminal=True,
        operation_reason_code="OPERATION_FAILED",
    )
    assert projected[0].status is GameTapeStatus.FAILED
    assert projected[0].exit_code == 0
    assert projected[0].reason == "Step teardown failed: ROOT_REMAINS."
    assert projected[0].failure is not None
    assert projected[0].failure.reason_code == "STEP_TEARDOWN_ROOT_REMAINS"


def test_game_tape_projects_canonical_shard_execution_identities_behaviorally() -> None:
    from oh_goon.core.game_tape_projection import _verification_execution_boundary
    from oh_goon.core.models.shard_execution import (
        CanonicalShardExecutionEvidence,
        CanonicalShardResultEvidence,
    )

    shard_execution = CanonicalShardExecutionEvidence(
        plan_identity="sha256:" + "1" * 64,
        repository_generation_identity="sha256:" + "2" * 64,
        execution_context_identity="sha256:" + "3" * 64,
        execution_policy_identity="sha256:" + "4" * 64,
        producer_contract_schema="hashmarks.verification-selection.v1",
        producer_version="0.14.0",
        producer_artifact_identity="sha256:" + "5" * 64,
        producer_implementation_identity="sha256:" + "6" * 64,
        producer_contract_identity="sha256:" + "7" * 64,
        repository_content_identity="sha256:" + "8" * 64,
        verification_selection_evidence_identity="sha256:" + "9" * 64,
        selected_membership_identity="sha256:" + "a" * 64,
        verification_revision_identity="sha256:" + "b" * 64,
        selected_members=("tests/test_a.py::test_a",),
        actual_executed_membership_identity="sha256:" + "c" * 64,
        actual_executed_members=("tests/test_a.py::test_a",),
        results=(
            CanonicalShardResultEvidence(
                shard_id="verification-0",
                described_work_identity="sha256:" + "d" * 64,
                physical_work_identity="sha256:" + "e" * 64,
                status="PASS",
                members=("tests/test_a.py::test_a",),
            ),
        ),
    )
    result = FleetPlanResult(plan_id="plan", steps=(), shard_execution=shard_execution)

    projected = _verification_execution_boundary(result, None)

    assert projected is not None
    assert projected.execution_plan_identity == shard_execution.plan_identity
    assert projected.execution_context_identity == shard_execution.execution_context_identity
    assert projected.execution_policy_identity == shard_execution.execution_policy_identity


def test_game_tape_projects_hashmarks_described_work_and_oh_goon_attempt_lineage_separately() -> (
    None
):
    from oh_goon.core.game_tape_projection import _shard_lineage
    from oh_goon.core.models.shard_execution import (
        CanonicalShardAttemptEvidence,
        CanonicalShardExecutionEvidence,
        CanonicalShardResultEvidence,
    )

    described = "sha256:" + "a" * 64
    physical = "sha256:" + "b" * 64
    attempt = "sha256:" + "c" * 64
    shard_execution = CanonicalShardExecutionEvidence(
        plan_identity="sha256:" + "d" * 64,
        repository_generation_identity="sha256:" + "e" * 64,
        execution_context_identity="sha256:" + "f" * 64,
        execution_policy_identity="sha256:" + "e" * 64,
        producer_contract_schema="hashmarks.test-shards.v3",
        producer_version="0.14.0",
        producer_artifact_identity="sha256:" + "1" * 64,
        producer_contract_identity="sha256:" + "9" * 64,
        results=(
            CanonicalShardResultEvidence(
                shard_id="hashmarks-pytest-0.r1.0",
                described_work_identity=described,
                physical_work_identity=physical,
                parent_work_identity="sha256:" + "2" * 64,
                refinement_depth=1,
                status="PASS",
                attempts=(
                    CanonicalShardAttemptEvidence(
                        attempt_identity=attempt,
                        attempt=1,
                        physical_work_identity=physical,
                        status="PASS",
                        returncode=0,
                        duration_ms=3,
                    ),
                ),
            ),
        ),
    )
    result = FleetPlanResult(plan_id="plan", steps=(), shard_execution=shard_execution)

    lineage = _shard_lineage(result)

    assert lineage[0].producer_contract_identity == "sha256:" + "9" * 64
    assert lineage[0].producer_artifact_identity == "sha256:" + "1" * 64
    assert lineage[0].producer_contract_schema == "hashmarks.test-shards.v3"
    assert lineage[0].producer_version == "0.14.0"
    assert lineage[0].described_work_identity == described
    assert lineage[0].physical_work_identity == physical
    assert lineage[0].attempts[0].attempt_identity == attempt
    assert lineage[0].attempts[0].attempt_identity != lineage[0].described_work_identity
