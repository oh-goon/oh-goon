from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

from oh_goon.core.capability_model import CapabilityRunnerKind, ResolvedCapabilityIntent
from oh_goon.core.errors import CapabilityUnavailable, StateConflict
from oh_goon.core.models.operation import OperationExecution
from oh_goon.core.ports.capability_runner import (
    CapabilityExecutionRequest,
    CapabilityRunner,
    PreparedRunnerExecution,
)
from oh_goon.core.vocabulary import EffectBoundaryKind
from oh_goon.runtime.fault_injection import FaultInjector, FaultPoint, NoFaultInjector


class CapabilityExecutor:
    """Prepare and execute one admitted capability through one concrete runner.

    Runners consume the durable admitted request directly. Preparation may freeze
    non-durable execution inputs, but canonical commit authority stays with
    RepositoryEffectExecution.
    """

    def __init__(
        self,
        runners: Mapping[CapabilityRunnerKind, CapabilityRunner],
        *,
        faults: FaultInjector | None = None,
    ) -> None:
        self._runners = MappingProxyType(dict(runners))
        self._faults = faults or NoFaultInjector()

    def effect_boundary(self, intent: ResolvedCapabilityIntent) -> EffectBoundaryKind:
        return self._runner(intent.definition.runner_kind).effect_boundary(intent.target)

    def prepare(self, request: CapabilityExecutionRequest) -> PreparedRunnerExecution:
        runner = self._runner(request.definition.runner_kind)
        if runner.effect_boundary(request.attempt.target) is not request.attempt.effect_boundary:
            raise StateConflict("Capability effect boundary changed after admission")
        return runner.prepare(request)

    def execute(
        self, prepared: PreparedRunnerExecution, request: CapabilityExecutionRequest
    ) -> OperationExecution:
        """Execute already-bound work; this method performs no durable mutation."""
        attempt = request.attempt
        self._faults.hit(FaultPoint.BEFORE_RUNNER)
        result = prepared.execute(request)
        self._faults.hit(FaultPoint.AFTER_RUNNER)
        if result.report.action is not attempt.action or result.report.target != attempt.target:
            raise StateConflict(
                "Capability runner report intent does not match the admitted capability intent"
            )
        if result.report.capability != attempt.capability:
            raise StateConflict(
                "Capability runner report capability provenance does not match admitted policy"
            )
        if result.report.provenance.operation_id != attempt.operation_id:
            raise StateConflict(
                "Capability runner report operation_id does not match the admitted effect identity"
            )
        if result.report.provenance.fleet_run_id != attempt.fleet_run_id:
            raise StateConflict(
                "Capability runner report fleet_run_id does not match the admitted effect owner"
            )
        if result.report.repository != attempt.repository:
            raise StateConflict(
                "Capability runner report repository does not match the admitted repository"
            )
        return result

    def _runner(self, kind: CapabilityRunnerKind) -> CapabilityRunner:
        runner = self._runners.get(kind)
        if runner is None:
            raise CapabilityUnavailable(f"No capability runner is registered for kind {kind.value}")
        return runner
