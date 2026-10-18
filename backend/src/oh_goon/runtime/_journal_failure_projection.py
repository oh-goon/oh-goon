from __future__ import annotations

from dataclasses import dataclass

from oh_goon.core.errors import InvalidRequest
from oh_goon.core.fleet_plan_projection import atomic_goon_by_step, fleet_plan_logical_slot
from oh_goon.core.models.fleet_failures import FleetFailureGroup, FleetFailureRepositoryItem
from oh_goon.core.models.game_tape import GameTapeRepositoryExecution
from oh_goon.core.models.operation import OperationReport
from oh_goon.core.models.target import FleetPlanTarget
from oh_goon.core.product_identity import stable_id
from oh_goon.core.vocabulary import GameTapeStatus


@dataclass(frozen=True, slots=True)
class CurrentFleetFailure:
    item: FleetFailureRepositoryItem
    source_kind: str
    signature_id: str | None


@dataclass(slots=True)
class FailureGroupAccumulator:
    first: CurrentFleetFailure
    repository_ids: set[str]
    signature_ids: set[str]

    @classmethod
    def start(cls, failure: CurrentFleetFailure) -> "FailureGroupAccumulator":
        signatures = {failure.signature_id} if failure.signature_id is not None else set()
        return cls(failure, {failure.item.repository.repository_id}, signatures)

    def add(self, failure: CurrentFleetFailure) -> None:
        self.repository_ids.add(failure.item.repository.repository_id)
        if failure.signature_id is not None:
            self.signature_ids.add(failure.signature_id)

    def project(self, group_id: str) -> FleetFailureGroup:
        item = self.first.item
        return FleetFailureGroup(
            group_id=group_id,
            plan_id=item.plan_id,
            plan_name=item.plan_name,
            source_kind=self.first.source_kind,
            goon_name=item.goon_name,
            step_id=item.step_id,
            affected_repositories=len(self.repository_ids),
            signature_count=len(self.signature_ids),
        )


def _failure_group_id(target: FleetPlanTarget, goon_name: str | None, step_id: str | None) -> str:
    return stable_id(
        "fleet-failure-group",
        {
            "logical_slot": fleet_plan_logical_slot(target),
            "plan_id": target.plan.plan_id,
            "goon_name": goon_name,
            "step_id": step_id,
        },
    )


def current_failure(
    *,
    fleet_run_id: str,
    report: OperationReport,
    repository: GameTapeRepositoryExecution | None,
) -> CurrentFleetFailure | None:
    target = report.target
    if not isinstance(target, FleetPlanTarget):
        return None
    if repository is None or repository.status is not GameTapeStatus.FAILED:
        return None
    failed_step = next(
        (step for step in repository.steps if step.status is GameTapeStatus.FAILED),
        None,
    )
    step_id = failed_step.step_id if failed_step is not None else None
    goon = atomic_goon_by_step(target.plan).get(step_id) if step_id is not None else None
    goon_name = goon.name if goon is not None else None
    failure = failed_step.failure if failed_step is not None else None
    group_id = _failure_group_id(target, goon_name, step_id)
    return CurrentFleetFailure(
        source_kind=target.source.kind,
        signature_id=failure.signature_id if failure is not None else None,
        item=FleetFailureRepositoryItem(
            repository=repository.repository,
            fleet_run_id=fleet_run_id,
            group_id=group_id,
            plan_id=target.plan.plan_id,
            plan_name=target.plan.name,
            goon_name=goon_name,
            step_id=step_id,
            signature_id=failure.signature_id if failure is not None else None,
            failure_code=(
                failure.reason_code
                if failure is not None and failure.reason_code is not None
                else report.outcome.reason_code.value
            ),
            failure_message=failed_step.reason if failed_step is not None else None,
        ),
    )


def group_from_failures(group_id: str, failures: list[CurrentFleetFailure]) -> FleetFailureGroup:
    if not failures:
        raise InvalidRequest(f"Unknown current failure group: {group_id}")
    first = failures[0]
    item = first.item
    return FleetFailureGroup(
        group_id=group_id,
        plan_id=item.plan_id,
        plan_name=item.plan_name,
        source_kind=first.source_kind,
        goon_name=item.goon_name,
        step_id=item.step_id,
        affected_repositories=len({failure.item.repository.repository_id for failure in failures}),
        signature_count=len(
            {failure.signature_id for failure in failures if failure.signature_id is not None}
        ),
    )
