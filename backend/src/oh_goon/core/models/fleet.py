from __future__ import annotations

from typing import Annotated, Literal

from pydantic import AwareDatetime, Field, computed_field, model_validator

from oh_goon.core.models.attention import FleetAttention
from oh_goon.core.models.base import ImmutableModel
from oh_goon.core.models.explanation import RepositoryExplanation
from oh_goon.core.models.operation import (
    GitPosition,
    OperationReport,
    ReadinessOperationEvidence,
)
from oh_goon.core.models.provenance import FleetSnapshotProvenance
from oh_goon.core.models.repository import RepositoryIdentity
from oh_goon.core.models.review import ReviewPreparation
from oh_goon.core.models.run import FleetRunDetail
from oh_goon.core.models.target import OperationTarget
from oh_goon.core.product_identity import operation_intent_key
from oh_goon.core.vocabulary import (
    FleetRunStatus,
    FleetScope,
    OperationAction,
    ReadinessState,
    ResultFreshness,
    Verdict,
)


class OperationState(ImmutableModel):
    report: OperationReport
    freshness: ResultFreshness

    @property
    def is_current(self) -> bool:
        return self.freshness is ResultFreshness.CURRENT

    @property
    def is_stale(self) -> bool:
        return not self.is_current

    @property
    def verdict(self) -> Verdict:
        return self.report.outcome.verdict

    @property
    def operation_id(self) -> str:
        return self.report.provenance.operation_id

    @property
    def action(self) -> OperationAction:
        return self.report.action

    @property
    def repository_id(self) -> str:
        return self.report.repository.repository_id

    @property
    def finished_at(self) -> AwareDatetime:
        return self.report.provenance.finished_at

    def is_current_operation(self, operation_id: str) -> bool:
        return self.is_current and self.operation_id == operation_id

    @property
    def current_needs_attention(self) -> bool:
        return self.is_current and self.verdict.needs_attention

    @property
    def current_successful(self) -> bool:
        return self.is_current and self.verdict.successful

    @property
    def current_ready(self) -> bool:
        return self.is_current and self.verdict is Verdict.READY

    @property
    def readiness_state(self) -> ReadinessState:
        if self.action is not OperationAction.INIT:
            raise ValueError("Readiness can only be derived from an Init operation state")
        if self.is_stale:
            return ReadinessState.STALE
        verdict = self.verdict
        if verdict is Verdict.READY:
            return ReadinessState.READY
        if verdict is Verdict.MANUAL_ACTION_REQUIRED:
            return ReadinessState.MANUAL_ACTION_REQUIRED
        if verdict is Verdict.FAILED:
            return ReadinessState.FAILED
        return ReadinessState.NOT_CHECKED

    @computed_field
    @property
    def explanation(self) -> RepositoryExplanation:
        from oh_goon.core.projections.explanation import explain_operation

        return explain_operation(self.report)


class _FleetRepositoryBase(ImmutableModel):
    repository: RepositoryIdentity
    repository_instance_id: str = Field(min_length=1)
    scope: FleetScope

    def operation_states(self) -> tuple[tuple[OperationAction, OperationState | None], ...]:
        return (
            (OperationAction.INIT, getattr(self, "init", None)),
            (OperationAction.PLAN, getattr(self, "plan", None)),
            (OperationAction.UPDATE, getattr(self, "update", None)),
            (OperationAction.VERIFY, getattr(self, "verify", None)),
        )


class IncludedFleetRepository(_FleetRepositoryBase):
    scope: Literal[FleetScope.INCLUDED]
    init: OperationState | None = None
    plan: OperationState | None = None
    update: OperationState | None = None
    verify: OperationState | None = None

    @computed_field
    @property
    def exclusion_reason(self) -> None:
        return None

    @computed_field
    @property
    def excluded_at(self) -> None:
        return None

    @computed_field
    @property
    def readiness_state(self) -> ReadinessState:
        return self.init.readiness_state if self.init is not None else ReadinessState.NOT_CHECKED

    @computed_field
    @property
    def needs_attention(self) -> bool:
        if self.readiness_state.needs_attention:
            return True
        return any(
            state is not None and state.current_needs_attention
            for action, state in self.operation_states()
            if action is not OperationAction.INIT
        )

    @computed_field
    @property
    def has_current_plan(self) -> bool:
        return self.plan is not None and self.plan.current_successful

    @computed_field
    @property
    def current_branch_position(self) -> GitPosition | None:
        if self.init is None or self.init.is_stale:
            return None
        evidence = self.init.report.evidence
        if not isinstance(evidence, ReadinessOperationEvidence):
            return None
        return evidence.readiness.position

    @computed_field
    @property
    def last_plan_verdict(self) -> Verdict:
        return self._last_verdict(self.plan)

    @computed_field
    @property
    def last_update_verdict(self) -> Verdict:
        return self._last_verdict(self.update)

    @computed_field
    @property
    def last_verify_verdict(self) -> Verdict:
        return self._last_verdict(self.verify)

    @staticmethod
    def _last_verdict(state: OperationState | None) -> Verdict:
        return state.verdict if state is not None else Verdict.NOT_CHECKED

    @computed_field
    @property
    def guidance(self) -> RepositoryExplanation:
        from oh_goon.core.projections.explanation import explain_repository_guidance

        return explain_repository_guidance(
            self.scope,
            init=self.init,
            plan=self.plan,
            update=self.update,
            verify=self.verify,
        )

    @model_validator(mode="after")
    def validate_operation_projection_lineage(self) -> IncludedFleetRepository:
        for action, state in self.operation_states():
            if state is None:
                continue
            if state.action is not action:
                raise ValueError(
                    f"FleetRepository {action.value} slot requires a {action.value} report"
                )
            if state.repository_id != self.repository.repository_id:
                raise ValueError("FleetRepository operation states must match repository identity")
        return self


class ExcludedFleetRepository(_FleetRepositoryBase):
    scope: Literal[FleetScope.EXCLUDED]
    exclusion_reason: str = Field(min_length=1)
    excluded_at: AwareDatetime

    @computed_field
    @property
    def readiness_state(self) -> Literal[ReadinessState.NOT_CHECKED]:
        return ReadinessState.NOT_CHECKED

    @computed_field
    @property
    def guidance(self) -> RepositoryExplanation:
        from oh_goon.core.projections.explanation import explain_repository_guidance

        return explain_repository_guidance(
            self.scope,
            init=None,
            plan=None,
            update=None,
            verify=None,
            exclusion_reason=self.exclusion_reason,
        )

    @computed_field
    @property
    def needs_attention(self) -> Literal[False]:
        return False

    @computed_field
    @property
    def has_current_plan(self) -> Literal[False]:
        return False

    @computed_field
    @property
    def current_branch_position(self) -> None:
        return None

    @computed_field
    @property
    def last_plan_verdict(self) -> Literal[Verdict.NOT_CHECKED]:
        return Verdict.NOT_CHECKED

    @computed_field
    @property
    def last_update_verdict(self) -> Literal[Verdict.NOT_CHECKED]:
        return Verdict.NOT_CHECKED

    @computed_field
    @property
    def last_verify_verdict(self) -> Literal[Verdict.NOT_CHECKED]:
        return Verdict.NOT_CHECKED

    @computed_field
    @property
    def init(self) -> None:
        return None

    @computed_field
    @property
    def plan(self) -> None:
        return None

    @computed_field
    @property
    def update(self) -> None:
        return None

    @computed_field
    @property
    def verify(self) -> None:
        return None


type FleetRepository = Annotated[
    IncludedFleetRepository | ExcludedFleetRepository,
    Field(discriminator="scope"),
]


class FleetSummary(ImmutableModel):
    discovered: int = 0
    included: int = 0
    excluded: int = 0
    not_checked: int = 0
    ready_for_update: int = 0
    stale: int = 0
    needs_attention: int = 0
    updated: int = 0
    unchanged: int = 0
    failed: int = 0


class FleetSnapshot(ImmutableModel):
    provenance: FleetSnapshotProvenance
    repositories: tuple[FleetRepository, ...] = Field(default_factory=tuple)
    active_fleet_run: FleetRunDetail | None = None
    latest_fleet_runs: tuple[FleetRunDetail, ...] = Field(default_factory=tuple)

    @computed_field
    @property
    def summary(self) -> FleetSummary:
        from oh_goon.core.projections.fleet import build_fleet_summary

        return build_fleet_summary(self._projection_context())

    @computed_field
    @property
    def attention(self) -> FleetAttention:
        from oh_goon.core.projections.attention import build_fleet_attention

        return build_fleet_attention(self._projection_context())

    @computed_field
    @property
    def review_preparation(self) -> ReviewPreparation | None:
        from oh_goon.core.projections.review import build_review_preparation

        return build_review_preparation(self._projection_context())

    def _projection_context(self):
        from oh_goon.core.projections.fleet_context import FleetProjectionContext

        return FleetProjectionContext.build(
            self.repositories, self.active_fleet_run, self.latest_fleet_runs
        )

    @model_validator(mode="after")
    def validate_repository_membership(self) -> FleetSnapshot:
        repository_ids = [repository.repository.repository_id for repository in self.repositories]
        if len(repository_ids) != len(set(repository_ids)):
            raise ValueError("FleetSnapshot repositories must contain unique repository identities")
        return self

    @model_validator(mode="after")
    def validate_run_references(self) -> FleetSnapshot:
        intents = [
            operation_intent_key(detail.run.capability.capability_id, detail.run.target)
            for detail in self.latest_fleet_runs
        ]
        if len(intents) != len(set(intents)):
            raise ValueError(
                "FleetSnapshot latest_fleet_runs must contain at most one run per exact intent"
            )
        return self

    @model_validator(mode="after")
    def validate_generation_time_lineage(self) -> FleetSnapshot:
        source_times = [
            state.finished_at
            for repository in self.repositories
            for _, state in repository.operation_states()
            if state is not None
        ]
        details = self._run_details()
        run_times = [detail.run.accepted_at for detail in details]
        for detail in details:
            if detail.run.status is not FleetRunStatus.QUEUED:
                run_times.append(detail.run.started_at)
            if detail.run.status.terminal:
                run_times.append(detail.run.finished_at)
        operation_times = [
            report.provenance.finished_at for detail in details for report in detail.operations
        ]
        if any(
            timestamp > self.provenance.generated_at
            for timestamp in (*source_times, *run_times, *operation_times)
            if timestamp is not None
        ):
            raise ValueError("FleetSnapshot generated_at cannot precede its source facts")
        return self

    def latest_run_exact(
        self,
        capability_id: str,
        target: OperationTarget | None = None,
    ) -> FleetRunDetail | None:
        matches = [
            detail
            for detail in self.latest_fleet_runs
            if detail.run.capability.capability_id == capability_id and detail.run.target == target
        ]
        return max(
            matches,
            key=lambda detail: (detail.run.accepted_at, detail.run.fleet_run_id),
            default=None,
        )

    def latest_run_family(self, action: OperationAction) -> FleetRunDetail | None:
        matches = [detail for detail in self.latest_fleet_runs if detail.run.action is action]
        return max(
            matches,
            key=lambda detail: (detail.run.accepted_at, detail.run.fleet_run_id),
            default=None,
        )

    def _run_details(self) -> list[FleetRunDetail]:
        details = list(self.latest_fleet_runs)
        if self.active_fleet_run is not None and all(
            detail.run.fleet_run_id != self.active_fleet_run.run.fleet_run_id for detail in details
        ):
            details.append(self.active_fleet_run)
        return details
