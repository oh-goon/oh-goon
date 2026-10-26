import pytest

from oh_goon.core.canonical_result_semantics import CanonicalResultSemantics
from oh_goon.core.models.execution_evidence_acceptance import AcceptedExecutionEvidence
from oh_goon.core.models.fleet_plan import ResolvedCommandStep, ResolvedFleetPlan, ResolvedImport
from oh_goon.core.models.fleet_plan_evidence import (
    CompletedPlanStepExecution,
    FailedPlanStepExecution,
    FleetPlanResult,
    PlanOutputEvidence,
    PlanStepSettlementEvidence,
)
from oh_goon.core.models.fleet_plan_execution_policy import CicdExecutionPolicy, CicdResourcePolicy
from oh_goon.core.vocabulary.result_semantics import CanonicalResultStatus, FailureImpact


def _step(step_id: str) -> ResolvedCommandStep:
    return ResolvedCommandStep(step_id=step_id, argv=("true",), timeout_seconds=10)


def _output() -> PlanOutputEvidence:
    return PlanOutputEvidence(sha256="0" * 64, byte_count=0, line_count=0)


def _execution(step_id: str, *, failed: bool = False):
    common = dict(
        step_id=step_id,
        kind="command",
        stdout_evidence=_output(),
        stderr_evidence=_output(),
        repository_state_before="same",
        repository_state_after="same",
    )
    if failed:
        return FailedPlanStepExecution(returncode=1, **common)
    return CompletedPlanStepExecution(
        returncode=0,
        settlement=PlanStepSettlementEvidence(
            outcome="SUCCESS", cleanup="VERIFIED", reason="ACCEPTED_EXIT", observed_returncode=0
        ),
        **common,
    )


def _plan(
    *, impact: FailureImpact, nested_impact: FailureImpact | None = None
) -> ResolvedFleetPlan:
    imports = []
    if nested_impact is None:
        imports.append(
            ResolvedImport(
                import_id="child",
                name="general/child",
                source_sha256="a" * 64,
                step_ids=("child.test",),
                failure_impact=impact,
            )
        )
    else:
        imports.extend(
            (
                ResolvedImport(
                    import_id="outer",
                    name="general/outer",
                    source_sha256="a" * 64,
                    step_ids=("outer.inner.test",),
                    failure_impact=impact,
                ),
                ResolvedImport(
                    import_id="outer.inner",
                    parent_import_id="outer",
                    name="general/inner",
                    source_sha256="b" * 64,
                    step_ids=("outer.inner.test",),
                    failure_impact=nested_impact,
                ),
            )
        )
    step_id = imports[0].step_ids[0]
    return ResolvedFleetPlan.create(
        name="result-semantics",
        description="",
        execution=CicdExecutionPolicy(
            trust_repository_code=True,
            resources=CicdResourcePolicy(cpu_millis=1000, memory_mib=512, max_processes=64),
        ),
        imports=tuple(imports),
        steps=(_step(step_id),),
    )


def test_blocking_child_failure_remains_parent_failure() -> None:
    plan = _plan(impact=FailureImpact.BLOCKING)
    result = FleetPlanResult(plan_id=plan.plan_id, steps=(_execution("child.test", failed=True),))
    projected = CanonicalResultSemantics().interpret_plan(plan, result)
    assert projected.status is CanonicalResultStatus.FAILED
    assert projected.children[0].factual_status is CanonicalResultStatus.FAILED
    assert projected.children[0].parent_status is CanonicalResultStatus.FAILED


def test_non_blocking_child_failure_preserves_fact_and_changes_only_parent_impact() -> None:
    plan = _plan(impact=FailureImpact.NON_BLOCKING)
    result = FleetPlanResult(plan_id=plan.plan_id, steps=(_execution("child.test", failed=True),))
    projected = CanonicalResultSemantics().interpret_plan(plan, result)
    assert projected.status is CanonicalResultStatus.ATTENTION_REQUIRED
    assert projected.failed_step_ids == ("child.test",)
    assert projected.children[0].factual_status is CanonicalResultStatus.FAILED
    assert projected.children[0].parent_status is CanonicalResultStatus.ATTENTION_REQUIRED


def test_nested_non_blocking_failure_does_not_reappear_as_blocking_at_outer_edge() -> None:
    plan = _plan(impact=FailureImpact.BLOCKING, nested_impact=FailureImpact.NON_BLOCKING)
    result = FleetPlanResult(
        plan_id=plan.plan_id, steps=(_execution("outer.inner.test", failed=True),)
    )
    projected = CanonicalResultSemantics().interpret_plan(plan, result)
    by_id = {item.import_id: item for item in projected.children}
    assert by_id["outer.inner"].factual_status is CanonicalResultStatus.FAILED
    assert by_id["outer.inner"].parent_status is CanonicalResultStatus.ATTENTION_REQUIRED
    assert by_id["outer"].factual_status is CanonicalResultStatus.ATTENTION_REQUIRED
    assert projected.status is CanonicalResultStatus.ATTENTION_REQUIRED


def test_success_is_unchanged_by_non_blocking_policy() -> None:
    plan = _plan(impact=FailureImpact.NON_BLOCKING)
    result = FleetPlanResult(plan_id=plan.plan_id, steps=(_execution("child.test"),))
    projected = CanonicalResultSemantics().interpret_plan(plan, result)
    assert projected.status is CanonicalResultStatus.SUCCESS


def test_accepted_fact_interpretation_is_bound_to_phase6_result_identity() -> None:
    accepted = AcceptedExecutionEvidence.model_construct(
        acceptance_id="accept-1", result_identity="result-1"
    )
    projected = CanonicalResultSemantics().interpret_accepted(
        accepted, result_identity="result-1", status=CanonicalResultStatus.CANCELLED
    )
    assert projected.status is CanonicalResultStatus.CANCELLED
    with pytest.raises(Exception, match="acceptance identity"):
        CanonicalResultSemantics().interpret_accepted(
            accepted, result_identity="stale-result", status=CanonicalResultStatus.SUCCESS
        )
