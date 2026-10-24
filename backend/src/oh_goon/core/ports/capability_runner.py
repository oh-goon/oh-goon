from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from oh_goon.core.capability_model import CapabilityDefinition, CapabilityRunnerKind
from oh_goon.core.models.effect_attempt import ClaimedRepositoryEffectAttempt
from oh_goon.core.models.fleet_plan_evidence import PlanIsolationBackendEvidence
from oh_goon.core.models.operation import OperationExecution
from oh_goon.core.models.run import FleetRunDetail
from oh_goon.core.models.run_admission import PlanRepositoryAdmissionEvidence
from oh_goon.core.models.target import OperationTarget
from oh_goon.core.models.termination import TerminationSignal
from oh_goon.core.ports.local_lace_intent import LocalLaceIntentReservation
from oh_goon.core.vocabulary import EffectBoundaryKind


@dataclass(frozen=True, slots=True)
class CapabilityExecutionRequest:
    """One durable CLAIMED effect plus the executable definition bound to that admission."""

    attempt: ClaimedRepositoryEffectAttempt
    definition: CapabilityDefinition
    cancel_requested: Callable[[], bool] | None
    termination_requested: Callable[[], TerminationSignal | None] | None = None
    isolation_backend: PlanIsolationBackendEvidence | None = None
    repository_admission: PlanRepositoryAdmissionEvidence | None = None
    local_lace_intent_for: Callable[..., LocalLaceIntentReservation] | None = None
    admitted_run: FleetRunDetail | None = None

    def __post_init__(self) -> None:
        if self.attempt.capability != self.definition.durable_snapshot():
            raise ValueError("Executable capability policy differs from durable effect admission")
        if (
            self.isolation_backend is not None
            and self.definition.runner_kind is not CapabilityRunnerKind.FLEET_PLAN
        ):
            raise ValueError("Process-isolation admission evidence is only valid for Fleet plans")
        if (
            self.repository_admission is not None
            and self.definition.runner_kind is not CapabilityRunnerKind.FLEET_PLAN
        ):
            raise ValueError("Repository/Plan admission evidence is only valid for Fleet plans")
        if (
            self.repository_admission is not None
            and self.repository_admission.repository_id != self.attempt.repository.repository_id
        ):
            raise ValueError(
                "Repository/Plan admission evidence must match the execution repository"
            )
        if self.admitted_run is not None:
            if self.definition.runner_kind is not CapabilityRunnerKind.FLEET_PLAN:
                raise ValueError(
                    "Admitted Fleet Run authority is valid only for Fleet Plan execution"
                )
            if self.attempt.fleet_run_id != self.admitted_run.run.fleet_run_id:
                raise ValueError("Fleet worker Run identity must match the admitted Run")
            if self.attempt.target != self.admitted_run.run.target:
                raise ValueError("Fleet worker Plan target must match the admitted Run")


class PreparedRunnerExecution(Protocol):
    """Bound non-durable execution resources owned until runner settlement."""

    @property
    def execution_input_id(self) -> str | None: ...

    def execute(self, request: CapabilityExecutionRequest) -> OperationExecution: ...

    def close(self) -> None: ...


class CapabilityRunner(Protocol):
    """Typed preparation/execution port for one resolved capability."""

    def effect_boundary(self, target: OperationTarget | None) -> EffectBoundaryKind: ...

    def prepare(self, request: CapabilityExecutionRequest) -> PreparedRunnerExecution: ...
