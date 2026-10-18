from __future__ import annotations

from oh_goon.core.capability_model import ResolvedCapabilityIntent
from oh_goon.core.capability_registry import resolve_capability_intent
from oh_goon.core.models.capability import CapabilitySnapshot
from oh_goon.core.models.repository import RepositoryIdentity
from oh_goon.core.models.run_admission import (
    DirectFleetExecutionAdmission,
    FleetRunAdmissionIntent,
    fleet_repository_scope,
)
from oh_goon.core.models.target import OperationTarget
from oh_goon.core.vocabulary import OperationAction
from oh_goon.runtime.journal import Journal
from tests.factories.journal import admit_run


def resolved_intent(
    action: OperationAction,
    target: OperationTarget | None = None,
) -> ResolvedCapabilityIntent:
    return resolve_capability_intent(action, target)


def capability_snapshot(
    action: OperationAction,
    target: OperationTarget | None = None,
) -> CapabilitySnapshot:
    return resolved_intent(action, target).definition.durable_snapshot()


def admit_test_run(
    journal: Journal,
    fleet_run_id: str,
    action: OperationAction,
    targets: list[RepositoryIdentity],
    target: OperationTarget | None = None,
):
    """Admit a test run using the same bound capability contract as production."""
    intent = resolved_intent(action, target)
    admitted = FleetRunAdmissionIntent(
        target=intent.target,
        capability=intent.definition.durable_snapshot(),
        execution=DirectFleetExecutionAdmission(),
        repository_admissions=(),
    )
    return admit_run(journal, fleet_run_id, admitted, fleet_repository_scope(None), targets)


def capability_request(
    repository,
    action: OperationAction,
    target: OperationTarget | None = None,
    *,
    operation_id: str = "op-test",
    fleet_run_id: str | None = None,
    effect_boundary=None,
    cancel_requested=None,
    isolation_backend=None,
):
    """Build one production-shaped admitted capability request for direct runner tests."""
    from oh_goon.core.effect_attempt_transitions import begin_effect_attempt
    from oh_goon.core.models.effect_origin import DirectEffectOrigin, FleetEffectOrigin
    from oh_goon.core.ports.capability_runner import CapabilityExecutionRequest
    from oh_goon.core.time import utc_now
    from oh_goon.core.vocabulary import EffectAttemptOwner, EffectBoundaryKind
    from tests.factories.repository_state import baseline_for

    intent = resolved_intent(action, target)
    origin = (
        FleetEffectOrigin(kind=EffectAttemptOwner.FLEET, fleet_run_id=fleet_run_id)
        if fleet_run_id is not None
        else DirectEffectOrigin(kind=EffectAttemptOwner.DIRECT)
    )
    attempt = begin_effect_attempt(
        repository,
        intent.definition.durable_snapshot(),
        origin=origin,
        target=intent.target,
        baseline=baseline_for(repository),
        effect_boundary=effect_boundary or EffectBoundaryKind.EXTERNAL,
        started_at=utc_now(),
        operation_id=operation_id,
    )
    return CapabilityExecutionRequest(
        attempt=attempt,
        definition=intent.definition,
        cancel_requested=cancel_requested or (lambda: False),
        isolation_backend=isolation_backend,
    )


def execute_capability_runner(runner, request):
    """Exercise a concrete runner through its production prepare/execute lifetime."""
    prepared = runner.prepare(request)
    try:
        return prepared.execute(request)
    finally:
        prepared.close()
