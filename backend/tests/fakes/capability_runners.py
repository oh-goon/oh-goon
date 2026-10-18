from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from oh_goon.core.capability_model import CapabilityRunnerKind
from oh_goon.core.models.operation import OperationExecution
from oh_goon.core.models.target import OperationTarget
from oh_goon.core.ports.capability_runner import CapabilityExecutionRequest
from oh_goon.core.vocabulary import EffectBoundaryKind


@dataclass(slots=True)
class _PreparedRunnerStub:
    runner: ExternalRunnerStub
    execution_input_id: str | None = None

    def execute(self, request: CapabilityExecutionRequest) -> OperationExecution:
        return self.runner.execute_request(request)

    def close(self) -> None:
        return None


class ExternalRunnerStub:
    """Test runner using the production prepare-then-execute contract."""

    def effect_boundary(self, _target: OperationTarget | None) -> EffectBoundaryKind:
        return EffectBoundaryKind.EXTERNAL

    def prepare(self, request: CapabilityExecutionRequest) -> _PreparedRunnerStub:
        return _PreparedRunnerStub(self)

    def execute_request(self, request: CapabilityExecutionRequest) -> OperationExecution:
        raise NotImplementedError


@dataclass(slots=True)
class CommandRunnerStub(ExternalRunnerStub):
    runner: Any

    def execute_request(self, request: CapabilityExecutionRequest) -> OperationExecution:
        kwargs: dict[str, object] = {
            "fleet_run_id": request.attempt.fleet_run_id,
            "cancel_requested": request.cancel_requested,
        }
        if request.attempt.target is not None:
            kwargs["target"] = request.attempt.target
        result = self.runner.run(request.attempt.repository.path, request.attempt.action, **kwargs)
        provenance = result.report.provenance.model_copy(
            update={"operation_id": request.attempt.operation_id}
        )
        report = result.report.model_copy(update={"provenance": provenance})
        return result.model_copy(update={"report": report})


def fixed_capability_runners(runner):
    return {kind: runner for kind in CapabilityRunnerKind}
