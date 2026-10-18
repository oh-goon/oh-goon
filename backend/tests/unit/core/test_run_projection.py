from datetime import timedelta
from pathlib import Path

import pytest

from oh_goon.core.errors import ErrorKind, InvalidRunTransition
from oh_goon.core.product_identity import repository_attempt_operation_id
from oh_goon.core.models.fleet_plan import ResolvedCommandStep, ResolvedFleetPlan
from oh_goon.core.models.fleet_plan_execution_policy import VerificationExecutionPolicy
from oh_goon.core.models.fleet_plan_step_contract import PlanStepCapabilities
from oh_goon.core.models.repository import RepositoryIdentity
from oh_goon.core.models.run import FleetRunFailure
from oh_goon.core.models.run_admission import (
    DirectFleetExecutionAdmission,
    fleet_repository_scope,
)
from oh_goon.core.models.run_events import (
    RepositoryCompletedEvent,
    RepositoryStartedEvent,
    RunAdmittedEvent,
    RunCancelledEvent,
    RunCancelRequestedEvent,
    RunCompletedEvent,
    RunFailedEvent,
    RunStartedEvent,
)
from oh_goon.core.run_projection import project_fleet_run_detail
from oh_goon.core.time import utc_now
from oh_goon.core.vocabulary import (
    FleetRunEventType,
    FleetRunFailureStage,
    FleetRunStatus,
    OperationAction,
)
from tests.factories.capabilities import capability_snapshot
from tests.factories.fleet_plan_targets import structural_fleet_plan_target
from tests.factories.operation_reports import make_operation_report
from tests.factories.run_admission import repository_admission_for


def _admitted(run_id: str, identities: list[RepositoryIdentity], *, generated_at):
    return RunAdmittedEvent(
        event_type=FleetRunEventType.RUN_ADMITTED,
        sequence=1,
        fleet_run_id=run_id,
        capability=capability_snapshot(OperationAction.INIT),
        repository_scope=fleet_repository_scope(None),
        targets=identities,
        execution=DirectFleetExecutionAdmission(),
        generated_at=generated_at,
        repository_admissions=(),
    )


def test_projection_derives_orchestration_state_from_run_facts_and_canonical_reports(
    tmp_path: Path,
) -> None:
    identity = RepositoryIdentity(name="api", path=tmp_path / "api")
    operation_id = repository_attempt_operation_id("fleet-projection", identity.repository_id)
    report = make_operation_report(
        identity,
        OperationAction.INIT,
        fleet_run_id="fleet-projection",
        operation_id=operation_id,
    )
    now = utc_now()
    events = [
        _admitted("fleet-projection", [identity], generated_at=now),
        RunStartedEvent(
            event_type=FleetRunEventType.RUN_STARTED,
            sequence=2,
            fleet_run_id="fleet-projection",
            generated_at=now,
        ),
        RepositoryStartedEvent(
            event_type=FleetRunEventType.REPOSITORY_STARTED,
            sequence=3,
            fleet_run_id="fleet-projection",
            repository_id=identity.repository_id,
            operation_id=operation_id,
            generated_at=now,
        ),
        RepositoryCompletedEvent(
            event_type=FleetRunEventType.REPOSITORY_COMPLETED,
            sequence=4,
            fleet_run_id="fleet-projection",
            operation_id=operation_id,
            generated_at=now,
        ),
        RunCompletedEvent(
            event_type=FleetRunEventType.RUN_COMPLETED,
            sequence=5,
            fleet_run_id="fleet-projection",
            generated_at=now,
        ),
    ]

    detail = project_fleet_run_detail(
        events, lambda candidate: report if candidate == operation_id else None
    )
    assert detail.run.status is FleetRunStatus.COMPLETED
    assert detail.run.progress.completed == 1
    assert detail.operations == (report,)
    assert "attempts" not in detail.model_dump()


def test_run_admitted_current_shape_requires_repository_admissions_field(tmp_path: Path) -> None:
    identity = RepositoryIdentity(name="api", path=tmp_path / "api")
    payload = _admitted("fleet-current-shape", [identity], generated_at=utc_now()).model_dump()
    payload.pop("repository_admissions")

    with pytest.raises(ValueError, match="repository_admissions"):
        RunAdmittedEvent.model_validate(payload)


def test_projection_preserves_repository_plan_admission_evidence(tmp_path: Path) -> None:
    identity = RepositoryIdentity(name="api", path=tmp_path / "api")
    plan = ResolvedFleetPlan.create(
        name="verification",
        description="",
        execution=VerificationExecutionPolicy(),
        steps=(
            ResolvedCommandStep(
                timeout_seconds=60,
                step_id="check",
                argv=("true",),
                capabilities=PlanStepCapabilities(),
            ),
        ),
    )
    target = structural_fleet_plan_target(plan)
    admitted = repository_admission_for(
        repository_id=identity.repository_id,
        plan_id=plan.plan_id,
    )
    now = utc_now()

    detail = project_fleet_run_detail(
        [
            RunAdmittedEvent(
                event_type=FleetRunEventType.RUN_ADMITTED,
                sequence=1,
                fleet_run_id="fleet-admission-evidence",
                target=target,
                capability=capability_snapshot(OperationAction.EXECUTE_PLAN, target),
                repository_scope=fleet_repository_scope(None),
                targets=(identity,),
                execution=DirectFleetExecutionAdmission(),
                repository_admissions=(admitted,),
                generated_at=now,
            )
        ],
        lambda _: None,
    )

    assert detail.repository_admissions == (admitted,)
    projected = detail.model_dump(mode="json")["repository_admissions"][0]
    assert projected["semantic_preview_id"] == admitted.semantic_preview_id
    assert projected["source_authority"] == admitted.source_authority.model_dump(mode="json")
    assert projected["admission_id"] == admitted.admission_id


def test_projection_rejects_non_contiguous_history(tmp_path: Path) -> None:
    identity = RepositoryIdentity(name="api", path=tmp_path / "api")
    now = utc_now()
    admitted = _admitted("fleet-bad", [identity], generated_at=now)
    with pytest.raises(InvalidRunTransition, match="contiguous"):
        project_fleet_run_detail(
            [
                admitted,
                RunStartedEvent(
                    event_type=FleetRunEventType.RUN_STARTED,
                    sequence=3,
                    fleet_run_id="fleet-bad",
                    generated_at=now,
                ),
            ],
            lambda _: None,
        )


def test_projection_uses_sequence_when_wall_clock_moves_backward(tmp_path: Path) -> None:
    identity = RepositoryIdentity(name="api", path=tmp_path / "api")
    now = utc_now()
    detail = project_fleet_run_detail(
        [
            _admitted("fleet-clock", [identity], generated_at=now),
            RunStartedEvent(
                event_type=FleetRunEventType.RUN_STARTED,
                sequence=2,
                fleet_run_id="fleet-clock",
                generated_at=now - timedelta(seconds=1),
            ),
        ],
        lambda _: None,
    )

    assert detail.run.status is FleetRunStatus.RUNNING
    assert detail.run.started_at == now - timedelta(seconds=1)


def test_projection_rejects_event_after_terminal_state(tmp_path: Path) -> None:
    identity = RepositoryIdentity(name="api", path=tmp_path / "api")
    now = utc_now()
    failure = FleetRunFailure(
        code="TEST_FAILURE",
        kind=ErrorKind.INTERNAL,
        stage=FleetRunFailureStage.EXECUTION,
        message="boom",
    )
    events = [
        _admitted("fleet-terminal", [identity], generated_at=now),
        RunFailedEvent(
            event_type=FleetRunEventType.RUN_FAILED,
            sequence=2,
            fleet_run_id="fleet-terminal",
            failure=failure,
            generated_at=now,
        ),
        RunFailedEvent(
            event_type=FleetRunEventType.RUN_FAILED,
            sequence=3,
            fleet_run_id="fleet-terminal",
            failure=failure,
            generated_at=now,
        ),
    ]
    with pytest.raises(InvalidRunTransition, match="terminal"):
        project_fleet_run_detail(events, lambda _: None)


def test_lifecycle_event_rejects_cross_repository_attempt_binding(tmp_path: Path) -> None:
    first = RepositoryIdentity(name="first", path=tmp_path / "first")
    second = RepositoryIdentity(name="second", path=tmp_path / "second")
    now = utc_now()
    operation_id = repository_attempt_operation_id("fleet-scope", first.repository_id)
    events = [
        _admitted("fleet-scope", [first, second], generated_at=now),
        RunStartedEvent(
            event_type=FleetRunEventType.RUN_STARTED,
            sequence=2,
            fleet_run_id="fleet-scope",
            generated_at=now,
        ),
        RepositoryStartedEvent(
            event_type=FleetRunEventType.REPOSITORY_STARTED,
            sequence=3,
            fleet_run_id="fleet-scope",
            repository_id=second.repository_id,
            operation_id=operation_id,
            generated_at=now,
        ),
    ]

    with pytest.raises(InvalidRunTransition, match="canonical"):
        project_fleet_run_detail(events, lambda _: None)


def test_repository_completion_rejects_wrong_run_lineage_and_duplicate_operation(
    tmp_path: Path,
) -> None:
    identity = RepositoryIdentity(name="api", path=tmp_path / "api")
    operation_id = repository_attempt_operation_id("fleet-detail", identity.repository_id)
    wrong = make_operation_report(
        identity, OperationAction.INIT, fleet_run_id="other", operation_id=operation_id
    )
    now = utc_now()
    prefix = [
        _admitted("fleet-detail", [identity], generated_at=now),
        RunStartedEvent(
            event_type=FleetRunEventType.RUN_STARTED,
            sequence=2,
            fleet_run_id="fleet-detail",
            generated_at=now,
        ),
        RepositoryStartedEvent(
            event_type=FleetRunEventType.REPOSITORY_STARTED,
            sequence=3,
            fleet_run_id="fleet-detail",
            repository_id=identity.repository_id,
            operation_id=operation_id,
            generated_at=now,
        ),
        RepositoryCompletedEvent(
            event_type=FleetRunEventType.REPOSITORY_COMPLETED,
            sequence=4,
            fleet_run_id="fleet-detail",
            operation_id=operation_id,
            generated_at=now,
        ),
    ]
    with pytest.raises(InvalidRunTransition, match="fleet_run_id does not match"):
        project_fleet_run_detail(prefix, lambda _: wrong)

    report = make_operation_report(
        identity, OperationAction.INIT, fleet_run_id="fleet-detail", operation_id=operation_id
    )
    duplicate = RepositoryCompletedEvent(
        event_type=FleetRunEventType.REPOSITORY_COMPLETED,
        sequence=5,
        fleet_run_id="fleet-detail",
        operation_id=operation_id,
        generated_at=now,
    )
    with pytest.raises(InvalidRunTransition, match="Operation already recorded"):
        project_fleet_run_detail([*prefix, duplicate], lambda _: report)


def test_repository_completion_rejects_noncanonical_operation_identity(tmp_path: Path) -> None:
    identity = RepositoryIdentity(name="api", path=tmp_path / "api")
    now = utc_now()
    events = [
        _admitted("fleet-noncanonical", [identity], generated_at=now),
        RunStartedEvent(
            event_type=FleetRunEventType.RUN_STARTED,
            sequence=2,
            fleet_run_id="fleet-noncanonical",
            generated_at=now,
        ),
        RepositoryCompletedEvent(
            event_type=FleetRunEventType.REPOSITORY_COMPLETED,
            sequence=3,
            fleet_run_id="fleet-noncanonical",
            operation_id="op-not-canonical",
            generated_at=now,
        ),
    ]
    report = make_operation_report(
        identity,
        OperationAction.INIT,
        fleet_run_id="fleet-noncanonical",
        operation_id="op-not-canonical",
    )

    with pytest.raises(InvalidRunTransition, match="canonical"):
        project_fleet_run_detail(events, lambda _: report)


def test_run_cannot_complete_until_every_captured_target_has_canonical_report(
    tmp_path: Path,
) -> None:
    first = RepositoryIdentity(name="first", path=tmp_path / "first")
    second = RepositoryIdentity(name="second", path=tmp_path / "second")
    now = utc_now()
    events = [
        _admitted("fleet-incomplete", [first, second], generated_at=now),
        RunStartedEvent(
            event_type=FleetRunEventType.RUN_STARTED,
            sequence=2,
            fleet_run_id="fleet-incomplete",
            generated_at=now,
        ),
        RunCompletedEvent(
            event_type=FleetRunEventType.RUN_COMPLETED,
            sequence=3,
            fleet_run_id="fleet-incomplete",
            generated_at=now,
        ),
    ]
    with pytest.raises(InvalidRunTransition, match="every captured target"):
        project_fleet_run_detail(events, lambda _: None)


def test_cancellation_remains_first_class_orchestration_state(tmp_path: Path) -> None:
    identity = RepositoryIdentity(name="api", path=tmp_path / "api")
    now = utc_now()
    events = [
        _admitted("fleet-cancel", [identity], generated_at=now),
        RunStartedEvent(
            event_type=FleetRunEventType.RUN_STARTED,
            sequence=2,
            fleet_run_id="fleet-cancel",
            generated_at=now,
        ),
        RunCancelRequestedEvent(
            event_type=FleetRunEventType.RUN_CANCEL_REQUESTED,
            sequence=3,
            fleet_run_id="fleet-cancel",
            generated_at=now,
        ),
        RunCancelledEvent(
            event_type=FleetRunEventType.RUN_CANCELLED,
            sequence=4,
            fleet_run_id="fleet-cancel",
            generated_at=now,
        ),
    ]
    state = project_fleet_run_detail(events, lambda _: None).run
    assert state.status is FleetRunStatus.CANCELLED
    assert state.cancel_requested_at == now
    assert state.finished_at == now


def test_projection_preserves_non_manual_source_across_lifecycle(tmp_path: Path) -> None:
    from oh_goon.core.models.run_admission import DirectFleetExecutionAdmission, FleetRunAdmissionIntent, fleet_repository_scope
    from oh_goon.core.vocabulary import FleetRunSource, FleetRunTerminalIntent
    from oh_goon.runtime.database import LocalDatabase
    from oh_goon.runtime.journal import Journal
    from tests.factories.capabilities import resolved_intent
    from tests.factories.journal import admit_run, start_run, terminalize_run

    journal = Journal(LocalDatabase(tmp_path / "source-projection.db"))
    repository = RepositoryIdentity(path=tmp_path / "repo")
    intent = resolved_intent(OperationAction.INIT)
    admitted = FleetRunAdmissionIntent(
        source=FleetRunSource.CICD,
        target=intent.target,
        capability=intent.definition.durable_snapshot(),
        execution=DirectFleetExecutionAdmission(),
        repository_admissions=(),
    )
    queued = admit_run(journal, "cicd-run", admitted, fleet_repository_scope(None), [repository])
    assert queued.source is FleetRunSource.CICD

    running = start_run(journal, "cicd-run")
    assert running.source is FleetRunSource.CICD

    failed = terminalize_run(
        journal,
        "cicd-run",
        FleetRunTerminalIntent.FAIL,
        FleetRunFailure.recovery("fixture failure"),
    )
    assert failed.source is FleetRunSource.CICD
    assert journal.read_view().required_run_detail("cicd-run").run.source is FleetRunSource.CICD


def test_projection_preserves_scheduling_authority_receipt(tmp_path: Path) -> None:
    from oh_goon.core.models.scheduling import PriorityScope, SchedulingEnvelope, SchedulingPriority

    identity = RepositoryIdentity(name="api", path=tmp_path / "api")
    now = utc_now()
    scheduling = SchedulingEnvelope(
        run_id="fleet-scheduled",
        requested_priority=SchedulingPriority.HIGH,
        admitted_priority=SchedulingPriority.HIGH,
        priority_scope=PriorityScope.DOMAIN,
        priority_decision_reason="LOCAL_V1_AUTHORIZED",
        queue_seq=41,
        scheduling_revision=3,
        created_at=now,
    )
    admitted = _admitted("fleet-scheduled", [identity], generated_at=now).model_copy(
        update={"scheduling": scheduling}
    )

    detail = project_fleet_run_detail([admitted], lambda _: None)

    assert detail.scheduling == scheduling
    assert detail.scheduling.queue_seq == 41
    assert detail.scheduling.scheduling_revision == 3


def test_scheduling_binding_rejects_priority_drift_on_idempotent_replay(tmp_path: Path) -> None:
    from oh_goon.core.errors import JournalConflict
    from oh_goon.core.models.scheduling import SchedulingEnvelope, SchedulingPriority
    from oh_goon.core.run_identity import require_fleet_run_scheduling_binding

    identity = RepositoryIdentity(name="api", path=tmp_path / "api")
    now = utc_now()
    admitted = _admitted("fleet-priority-binding", [identity], generated_at=now).model_copy(
        update={
            "scheduling": SchedulingEnvelope(
                run_id="fleet-priority-binding",
                requested_priority=SchedulingPriority.HIGH,
                admitted_priority=SchedulingPriority.HIGH,
                queue_seq=9,
                created_at=now,
            )
        }
    )
    detail = project_fleet_run_detail([admitted], lambda _: None)

    require_fleet_run_scheduling_binding(detail, SchedulingPriority.HIGH)
    with pytest.raises(JournalConflict, match="already bound to requested priority HIGH"):
        require_fleet_run_scheduling_binding(detail, SchedulingPriority.NORMAL)


