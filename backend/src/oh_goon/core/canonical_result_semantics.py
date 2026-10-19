from __future__ import annotations

from collections import defaultdict

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.models.canonical_result_semantics import (
    CanonicalAcceptedResult,
    CanonicalChildResult,
    CanonicalPlanResult,
)
from oh_goon.core.models.execution_evidence_acceptance import AcceptedExecutionEvidence
from oh_goon.core.models.fleet_plan import ResolvedFleetPlan, ResolvedImport
from oh_goon.core.models.fleet_plan_evidence import (
    FailedPlanStepExecution,
    FailedPlanStepTeardownEvidence,
    FleetPlanResult,
)
from oh_goon.core.vocabulary.result_semantics import CanonicalResultStatus, FailureImpact


class CanonicalResultSemantics:
    """Single owner for accepted facts and parent composition impact."""

    def interpret_accepted(
        self,
        accepted: AcceptedExecutionEvidence,
        *,
        result_identity: str,
        status: CanonicalResultStatus,
    ) -> CanonicalAcceptedResult:
        if result_identity != accepted.result_identity:
            raise FleetPlanInvalid(
                "Canonical result semantics cannot interpret a result outside its acceptance identity"
            )
        return CanonicalAcceptedResult(
            acceptance_id=accepted.acceptance_id, result_identity=result_identity, status=status
        )

    def interpret_plan(
        self, plan: ResolvedFleetPlan, result: FleetPlanResult
    ) -> CanonicalPlanResult:
        if result.plan_id != plan.plan_id:
            raise FleetPlanInvalid(
                "Canonical result semantics requires result and resolved Plan identity to match"
            )
        plan_step_ids = tuple(step.step_id for step in plan.steps)
        result_step_ids = tuple(step.step_id for step in result.steps)
        if result_step_ids != plan_step_ids:
            raise FleetPlanInvalid(
                "Canonical result semantics requires exact resolved Plan step coverage and order"
            )

        failed = frozenset(
            step.step_id
            for step in result.steps
            if isinstance(step, FailedPlanStepExecution)
            or isinstance(step.teardown, FailedPlanStepTeardownEvidence)
        )
        imports = {item.import_id: item for item in plan.imports}
        children: dict[str | None, list[ResolvedImport]] = defaultdict(list)
        for item in plan.imports:
            if item.parent_import_id is not None and item.parent_import_id not in imports:
                raise FleetPlanInvalid("Resolved import references unknown parent import")
            children[item.parent_import_id].append(item)

        projected: list[CanonicalChildResult] = []

        def evaluate(item: ResolvedImport) -> CanonicalResultStatus:
            nested = children.get(item.import_id, [])
            nested_step_ids = {step_id for child in nested for step_id in child.step_ids}
            direct_failed = failed.intersection(set(item.step_ids) - nested_step_ids)
            factual = (
                CanonicalResultStatus.FAILED if direct_failed else CanonicalResultStatus.SUCCESS
            )
            descendant_statuses: list[CanonicalResultStatus] = []
            for child in nested:
                descendant_statuses.append(evaluate(child))
            if CanonicalResultStatus.FAILED in descendant_statuses:
                factual = CanonicalResultStatus.FAILED
            elif (
                CanonicalResultStatus.ATTENTION_REQUIRED in descendant_statuses
                and factual is CanonicalResultStatus.SUCCESS
            ):
                factual = CanonicalResultStatus.ATTENTION_REQUIRED
            parent = factual
            if (
                factual is CanonicalResultStatus.FAILED
                and item.failure_impact is FailureImpact.NON_BLOCKING
            ):
                parent = CanonicalResultStatus.ATTENTION_REQUIRED
            projected.append(
                CanonicalChildResult(
                    import_id=item.import_id,
                    name=item.name,
                    failure_impact=item.failure_impact,
                    factual_status=factual,
                    parent_status=parent,
                    failed_step_ids=tuple(
                        step_id for step_id in item.step_ids if step_id in failed
                    ),
                )
            )
            return parent

        top = children.get(None, [])
        top_step_ids = {step_id for child in top for step_id in child.step_ids}
        root_direct_failed = failed.intersection(set(plan_step_ids) - top_step_ids)
        top_statuses = [evaluate(item) for item in top]
        if root_direct_failed or CanonicalResultStatus.FAILED in top_statuses:
            status = CanonicalResultStatus.FAILED
        elif CanonicalResultStatus.ATTENTION_REQUIRED in top_statuses:
            status = CanonicalResultStatus.ATTENTION_REQUIRED
        else:
            status = CanonicalResultStatus.SUCCESS
        order = {item.import_id: i for i, item in enumerate(plan.imports)}
        projected.sort(key=lambda item: order[item.import_id])
        return CanonicalPlanResult(
            plan_id=plan.plan_id,
            status=status,
            failed_step_ids=tuple(step_id for step_id in plan_step_ids if step_id in failed),
            children=tuple(projected),
        )
