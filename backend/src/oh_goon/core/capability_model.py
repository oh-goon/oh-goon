from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from oh_goon.core.models.capability import (
    CapabilityInfo,
    CapabilityRequirement,
    CapabilitySnapshot,
    CapabilityTargetKind,
)
from oh_goon.core.models.explanation import ReasonCode
from oh_goon.core.models.target import OperationTarget
from oh_goon.core.product_identity import stable_id
from oh_goon.core.vocabulary import CommandSafety, OperationAction, UvRecipeId, Verdict


class CapabilityRunnerKind(StrEnum):
    ENGINE = "ENGINE"
    RECIPE = "RECIPE"
    MIGRATION = "MIGRATION"
    FLEET_PLAN = "FLEET_PLAN"


class CapabilityTargetSelector(StrEnum):
    INCLUDED = "INCLUDED"
    LEAGUE_TEAM = "LEAGUE_TEAM"
    MIGRATION_SAFE = "MIGRATION_SAFE"
    REVIEW_READY = "REVIEW_READY"


class OperationEvidenceKind(StrEnum):
    READINESS = "READINESS"
    PLAN = "PLAN"
    UPDATE = "UPDATE"
    VERIFY = "VERIFY"
    REVIEW = "REVIEW"
    RECIPE = "RECIPE"
    MIGRATION = "MIGRATION"
    FLEET_PLAN = "FLEET_PLAN"


def operation_evidence_kind(action: OperationAction) -> OperationEvidenceKind:
    """Return the canonical result-evidence family for one capability action."""
    if action in (OperationAction.STATUS, OperationAction.INIT):
        return OperationEvidenceKind.READINESS
    if action is OperationAction.PLAN:
        return OperationEvidenceKind.PLAN
    if action is OperationAction.UPDATE:
        return OperationEvidenceKind.UPDATE
    if action is OperationAction.VERIFY:
        return OperationEvidenceKind.VERIFY
    if action is OperationAction.PREPARE_REVIEW:
        return OperationEvidenceKind.REVIEW
    if action is OperationAction.RECIPE:
        return OperationEvidenceKind.RECIPE
    if action is OperationAction.ADOPT_UV:
        return OperationEvidenceKind.MIGRATION
    if action is OperationAction.EXECUTE_PLAN:
        return OperationEvidenceKind.FLEET_PLAN
    raise ValueError(f"Operation action has no result evidence family: {action.value}")


@dataclass(frozen=True, slots=True)
class RecipeExecutionPolicy:
    argv: tuple[str, ...]
    structured_json: bool = False
    temporary_output: bool = False
    append_task_argv: bool = False
    audit_findings: bool = False
    failure_reason: ReasonCode = ReasonCode.OPERATION_FAILED
    failure_verdict: Verdict = Verdict.FAILED


@dataclass(frozen=True, slots=True)
class CapabilityDefinition:
    """One capability's public metadata, admission, execution and invalidation policy."""

    capability_id: str
    action: OperationAction
    label: str
    description: str
    runner_kind: CapabilityRunnerKind
    safety: CommandSafety
    target_kind: CapabilityTargetKind = CapabilityTargetKind.NONE
    selector: CapabilityTargetSelector = CapabilityTargetSelector.INCLUDED
    requirements: tuple[CapabilityRequirement, ...] = ()
    invalidates_capabilities: frozenset[str] = frozenset()
    invalidates_all: bool = False
    fleet_supported: bool = True
    schedule_supported: bool = False
    direct_supported: bool = False
    rerun_supported: bool = True
    parallel_safe: bool = False
    requires_uv_project: bool = True
    recipe: UvRecipeId | None = None
    user_configurable: bool = False
    produces_security_findings: bool = False
    produces_sbom: bool = False
    recipe_execution: RecipeExecutionPolicy | None = None

    def durable_snapshot(self) -> CapabilitySnapshot:
        """Freeze the policy needed to interpret durable execution facts later."""
        return CapabilitySnapshot(
            capability_id=self.capability_id,
            policy_fingerprint=self._policy_fingerprint(),
            action=self.action,
            target_kind=self.target_kind,
            safety=self.safety,
            invalidates_capabilities=tuple(sorted(self.invalidates_capabilities)),
            invalidates_all=self.invalidates_all,
        )

    def _policy_fingerprint(self) -> str:
        recipe_execution = self.recipe_execution
        payload = {
            "capability_id": self.capability_id,
            "action": self.action.value,
            "runner_kind": self.runner_kind.value,
            "safety": self.safety.value,
            "target_kind": self.target_kind.value,
            "selector": self.selector.value,
            "requirements": [
                item.model_dump(mode="json", exclude_computed_fields=True)
                for item in sorted(
                    self.requirements,
                    key=lambda requirement: (
                        requirement.capability_id,
                        requirement.target_binding.value,
                        requirement.condition.value,
                    ),
                )
            ],
            "invalidates_capabilities": sorted(self.invalidates_capabilities),
            "invalidates_all": self.invalidates_all,
            "fleet_supported": self.fleet_supported,
            "schedule_supported": self.schedule_supported,
            "parallel_safe": self.parallel_safe,
            "requires_uv_project": self.requires_uv_project,
            "recipe": self.recipe.value if self.recipe is not None else None,
            "user_configurable": self.user_configurable,
            "produces_security_findings": self.produces_security_findings,
            "produces_sbom": self.produces_sbom,
            "recipe_execution": (
                {
                    "argv": list(recipe_execution.argv),
                    "structured_json": recipe_execution.structured_json,
                    "temporary_output": recipe_execution.temporary_output,
                    "append_task_argv": recipe_execution.append_task_argv,
                    "audit_findings": recipe_execution.audit_findings,
                    "failure_reason": recipe_execution.failure_reason.value,
                    "failure_verdict": recipe_execution.failure_verdict.value,
                }
                if recipe_execution is not None
                else None
            ),
        }
        return stable_id("capability-policy", payload)

    def public_info(self) -> CapabilityInfo:
        return CapabilityInfo(
            capability_id=self.capability_id,
            action=self.action,
            label=self.label,
            description=self.description,
            target_kind=self.target_kind,
            fleet_supported=self.fleet_supported,
            schedule_supported=self.schedule_supported,
            direct_supported=self.direct_supported,
            rerun_supported=self.rerun_supported,
            parallel_safe=self.parallel_safe,
            requires_uv_project=self.requires_uv_project,
            requirements=list(self.requirements),
            safety=self.safety,
            recipe=self.recipe,
            user_configurable=self.user_configurable,
            produces_security_findings=self.produces_security_findings,
            produces_sbom=self.produces_sbom,
        )


@dataclass(frozen=True, slots=True)
class ResolvedCapabilityIntent:
    """One normalized capability/target binding resolved exactly once at the service boundary."""

    definition: CapabilityDefinition
    target: OperationTarget | None

    @property
    def capability_id(self) -> str:
        return self.definition.capability_id

    @property
    def action(self) -> OperationAction:
        return self.definition.action

    @property
    def parallel_safe(self) -> bool:
        from oh_goon.core.models.fleet_plan import ParallelRepositoryExecution
        from oh_goon.core.models.target import FleetPlanTarget

        if isinstance(self.target, FleetPlanTarget):
            return isinstance(self.target.plan.execution.repositories, ParallelRepositoryExecution)
        return self.definition.parallel_safe

    @property
    def max_parallel(self) -> int | None:
        from oh_goon.core.models.fleet_plan import ParallelRepositoryExecution
        from oh_goon.core.models.target import FleetPlanTarget

        if isinstance(self.target, FleetPlanTarget):
            policy = self.target.plan.execution.repositories
            return policy.max_parallel if isinstance(policy, ParallelRepositoryExecution) else None
        return 4 if self.definition.parallel_safe else None
