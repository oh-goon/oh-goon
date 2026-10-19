from dataclasses import replace
from pathlib import Path

import pytest

from oh_goon.core.capability_registry import resolve_capability_intent
from oh_goon.core.effect_attempt_transitions import begin_effect_attempt
from oh_goon.core.errors import OperationNotFound, OwnershipLost
from oh_goon.core.models.effect_origin import DirectEffectOrigin
from oh_goon.core.models.operation import OperationExecution
from oh_goon.core.ports.capability_runner import CapabilityExecutionRequest
from oh_goon.core.time import utc_now
from oh_goon.core.vocabulary import EffectAttemptOwner, EffectBoundaryKind, OperationAction
from oh_goon.runtime.database import LocalDatabase
from oh_goon.runtime.journal import Journal
from oh_goon.services.capability_executor import CapabilityExecutor
from tests.factories.operation_reports import make_operation_report
from tests.factories.repository_state import baseline_for
from tests.factories.runtime_stack import direct_operation_stack
from tests.fakes.capability_runners import (
    CommandRunnerStub,
    ExternalRunnerStub,
    fixed_capability_runners,
)


def _capability_request(
    repository: Path,
    intent,
    *,
    operation_id: str,
) -> CapabilityExecutionRequest:
    attempt = begin_effect_attempt(
        repository,
        intent.definition.durable_snapshot(),
        origin=DirectEffectOrigin(kind=EffectAttemptOwner.DIRECT),
        target=intent.target,
        baseline=baseline_for(repository),
        effect_boundary=EffectBoundaryKind.EXTERNAL,
        started_at=utc_now(),
        operation_id=operation_id,
    )
    return CapabilityExecutionRequest(
        attempt=attempt,
        definition=intent.definition,
        cancel_requested=lambda: False,
    )


class FakeRunner:
    def run(
        self,
        repository: Path,
        action: OperationAction,
        *,
        fleet_run_id: str | None = None,
        cancel_requested: object = None,
        target: object = None,
    ) -> OperationExecution:
        del cancel_requested, target
        report = make_operation_report(repository, action, fleet_run_id=fleet_run_id)
        return OperationExecution(stdout="ok", stderr="", report=report)


def test_direct_operation_service_commits_canonical_report_and_retires_attempt(
    tmp_path: Path,
) -> None:
    database = LocalDatabase(tmp_path / "state.db")
    journal = Journal(database)
    operations, attempts, _, _ = direct_operation_stack(
        database,
        journal,
        CommandRunnerStub(FakeRunner()),
    )
    repository = tmp_path / "repo"

    result = operations.run(repository, OperationAction.PLAN)

    operation_id = result.report.provenance.operation_id
    assert result.report.provenance.fleet_run_id is None
    assert journal.require_operation(operation_id) == result.report
    with pytest.raises(OperationNotFound):
        attempts.get(operation_id)
    current = journal.current_capability(repository, "plan")
    assert current is not None and current.report == result.report


def test_capability_executor_is_pure_adapter_execution_and_does_not_commit(tmp_path: Path) -> None:
    database = LocalDatabase(tmp_path / "state.db")
    journal = Journal(database)
    executor = CapabilityExecutor(fixed_capability_runners(CommandRunnerStub(FakeRunner())))
    repository = tmp_path / "repo"
    intent = resolve_capability_intent(OperationAction.PLAN)

    request = _capability_request(repository, intent, operation_id="op-adapter-only")
    prepared = executor.prepare(request)
    try:
        result = executor.execute(prepared, request)
    finally:
        prepared.close()

    assert result.report.provenance.operation_id == "op-adapter-only"
    assert journal.operation("op-adapter-only") is None


def test_direct_owner_loss_after_adapter_prevents_canonical_commit(tmp_path: Path) -> None:
    database = LocalDatabase(tmp_path / "lost-owner.db")
    journal = Journal(database)
    captured: list[str] = []

    class LeaseDestroyingAdapter(ExternalRunnerStub):
        def execute_request(self, request: CapabilityExecutionRequest) -> OperationExecution:
            captured.append(request.attempt.operation_id)
            report = make_operation_report(
                request.attempt.repository,
                request.attempt.action,
                fleet_run_id=request.attempt.fleet_run_id,
                operation_id=request.attempt.operation_id,
                target=request.attempt.target,
            )
            with database.transaction(write=True) as connection:
                connection.execute("DELETE FROM execution_lease WHERE singleton = 1")
            return OperationExecution(stdout="ok", stderr="", report=report)

    operations, attempts, _, _ = direct_operation_stack(
        database,
        journal,
        LeaseDestroyingAdapter(),
    )
    repository = tmp_path / "repo"

    with pytest.raises(OwnershipLost):
        operations.run(repository, OperationAction.PLAN)

    assert len(captured) == 1
    assert journal.operation(captured[0]) is None
    assert attempts.get(captured[0]).status.value == "EFFECT_STARTED"


@pytest.mark.parametrize(
    ("mismatch", "message"),
    [
        ("operation", "operation_id"),
        ("fleet", "fleet_run_id"),
        ("repository", "repository"),
    ],
)
def test_capability_executor_rejects_report_outside_admitted_execution_envelope(
    tmp_path: Path,
    mismatch: str,
    message: str,
) -> None:
    from oh_goon.core.errors import StateConflict

    repository = tmp_path / "repo"
    other_repository = tmp_path / "other"
    intent = resolve_capability_intent(OperationAction.PLAN)

    class InvalidEnvelopeAdapter(ExternalRunnerStub):
        def execute_request(self, request: CapabilityExecutionRequest) -> OperationExecution:
            report = make_operation_report(
                other_repository if mismatch == "repository" else request.attempt.repository.path,
                request.attempt.action,
                fleet_run_id=(
                    "fleet-wrong" if mismatch == "fleet" else request.attempt.fleet_run_id
                ),
                operation_id=(
                    "op-wrong" if mismatch == "operation" else request.attempt.operation_id
                ),
                target=request.attempt.target,
            )
            return OperationExecution(stdout="", stderr="", report=report)

    executor = CapabilityExecutor(fixed_capability_runners(InvalidEnvelopeAdapter()))

    with pytest.raises(StateConflict, match=message):
        request = _capability_request(repository, intent, operation_id="op-admitted")
        prepared = executor.prepare(request)
        try:
            executor.execute(prepared, request)
        finally:
            prepared.close()


def test_effect_boundary_is_revalidated_before_runner_preparation(tmp_path: Path) -> None:
    from oh_goon.core.errors import StateConflict

    repository = tmp_path / "repo"
    intent = resolve_capability_intent(OperationAction.PLAN)
    prepared_called = False

    class DriftedBoundaryRunner(ExternalRunnerStub):
        def effect_boundary(self, _target) -> EffectBoundaryKind:
            return EffectBoundaryKind.CONTAINED

        def prepare(self, request: CapabilityExecutionRequest):
            nonlocal prepared_called
            prepared_called = True
            return super().prepare(request)

    executor = CapabilityExecutor(fixed_capability_runners(DriftedBoundaryRunner()))
    request = _capability_request(repository, intent, operation_id="op-boundary-drift")

    with pytest.raises(StateConflict, match="effect boundary changed after admission"):
        executor.prepare(request)

    assert prepared_called is False


def test_non_fleet_request_rejects_isolation_admission_evidence(tmp_path: Path) -> None:
    from oh_goon.core.models.fleet_plan_evidence import PlanIsolationBackendEvidence

    repository = tmp_path / "repo"
    intent = resolve_capability_intent(OperationAction.PLAN)
    request = _capability_request(repository, intent, operation_id="op-no-isolation")
    backend = PlanIsolationBackendEvidence(
        backend_id="unexpected",
        configuration_id="unexpected-config",
        kernel_boundary="shared_host_kernel",
        remote_execution=False,
        experimental=False,
    )

    with pytest.raises(ValueError, match="only valid for Fleet plans"):
        CapabilityExecutionRequest(
            attempt=request.attempt,
            definition=request.definition,
            cancel_requested=request.cancel_requested,
            isolation_backend=backend,
        )


def test_direct_effect_boundary_follows_capability_policy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from oh_goon.core.errors import CapabilityUnavailable

    database = LocalDatabase(tmp_path / "direct-policy.db")
    journal = Journal(database)
    operations, _, _, _ = direct_operation_stack(
        database,
        journal,
        CommandRunnerStub(FakeRunner()),
    )
    intent = resolve_capability_intent(OperationAction.PLAN)
    blocked = replace(
        intent,
        definition=replace(
            intent.definition,
            label="Relabeled capability",
            description="Incidental presentation metadata changed.",
            direct_supported=False,
        ),
    )
    monkeypatch.setattr(
        "oh_goon.services.direct_effect_admission.resolve_capability_intent",
        lambda *_args, **_kwargs: blocked,
    )

    with pytest.raises(
        CapabilityUnavailable, match="not executable through the Direct effect boundary"
    ):
        operations.run(tmp_path / "repo", OperationAction.PLAN)
