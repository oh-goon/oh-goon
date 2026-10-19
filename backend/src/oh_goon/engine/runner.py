from __future__ import annotations

from oh_goon.core.errors import CapabilityUnavailable
from oh_goon.core.models.operation import OperationExecution
from oh_goon.core.models.target import OperationTarget
from oh_goon.core.ports.capability_runner import CapabilityExecutionRequest
from oh_goon.core.vocabulary import EffectBoundaryKind, OperationAction
from oh_goon.engine.git import GitService
from oh_goon.engine.operation_execution_context import OperationContext
from oh_goon.engine.operations import init, plan, review, update, verify

DEFAULT_OPERATION_TIMEOUT_SECONDS = 3600.0


class EngineRunner:
    """Native typed implementation for core repository operations."""

    def __init__(
        self,
        git: GitService,
        timeout_seconds: float = DEFAULT_OPERATION_TIMEOUT_SECONDS,
    ) -> None:
        self._timeout_seconds = timeout_seconds
        self._git = git

    def effect_boundary(self, target: OperationTarget | None) -> EffectBoundaryKind:
        del target
        return EffectBoundaryKind.EXTERNAL

    def prepare(self, request: CapabilityExecutionRequest) -> EngineRunner:
        target = request.attempt.target
        if target is not None:
            raise CapabilityUnavailable("Engine capability does not accept a target")
        return self

    @property
    def execution_input_id(self) -> None:
        return None

    def close(self) -> None:
        return None

    def execute(self, request: CapabilityExecutionRequest) -> OperationExecution:
        attempt = request.attempt
        context = OperationContext(
            attempt=attempt,
            timeout_seconds=self._timeout_seconds,
            cancel_requested=request.cancel_requested,
            git=self._git,
        )
        action = attempt.action
        match action:
            case OperationAction.INIT | OperationAction.STATUS:
                return init.run(context)
            case OperationAction.PLAN:
                return plan.run(context)
            case OperationAction.UPDATE:
                return update.run(context)
            case OperationAction.VERIFY:
                return verify.run(context)
            case OperationAction.PREPARE_REVIEW:
                return review.run(context)
        raise ValueError(f"Native engine does not implement action: {action.value}")
