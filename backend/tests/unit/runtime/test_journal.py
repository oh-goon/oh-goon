import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from pydantic import ValidationError

from oh_goon.core.errors import InvalidRunTransition, JournalConflict, RunNotFound
from oh_goon.core.models.explanation import ReasonCode
from oh_goon.core.models.operation import OperationReport
from oh_goon.core.models.repository import RepositoryIdentity
from oh_goon.core.product_identity import fleet_step_attempt_id, repository_attempt_operation_id
from oh_goon.core.vocabulary import (
    FleetRunEventType,
    FleetRunStatus,
    FleetRunTerminalIntent,
    OperationAction,
    ResultFreshness,
    Verdict,
)
from oh_goon.runtime.database import LocalDatabase
from oh_goon.runtime.journal import Journal
from tests.factories.capabilities import admit_test_run
from tests.factories.journal import (
    commit_operation,
    start_repository,
    start_run,
    terminalize_run,
)
from tests.factories.operation_reports import make_operation_report


def operation(
    identity: RepositoryIdentity,
    action: OperationAction,
    fleet_run_id: str | None,
    operation_id: str,
    verdict: Verdict = Verdict.READY,
) -> OperationReport:
    if fleet_run_id is not None:
        operation_id = repository_attempt_operation_id(fleet_run_id, identity.repository_id)
    return make_operation_report(
        identity,
        action,
        verdict=verdict,
        fleet_run_id=fleet_run_id,
        operation_id=operation_id,
        reason_code=(ReasonCode.NO_UPDATES if verdict is Verdict.UNCHANGED else ReasonCode.READY),
    )


def claim_effect(journal: Journal, run_id: str, identity: RepositoryIdentity) -> str:
    del journal
    return repository_attempt_operation_id(run_id, identity.repository_id)


def running_journal(
    tmp_path: Path, *, action: OperationAction = OperationAction.INIT
) -> tuple[Journal, RepositoryIdentity]:
    journal = Journal(LocalDatabase(tmp_path / "state.db"))
    identity = RepositoryIdentity(name="api", path=tmp_path / "api")
    admit_test_run(journal, "run-1", action, [identity])
    start_run(journal, "run-1")
    claim_effect(journal, "run-1", identity)
    return journal, identity


def test_execution_lifecycle_duplicate_is_true_noop_including_revision(tmp_path: Path) -> None:
    journal, identity = running_journal(tmp_path)
    operation_id = repository_attempt_operation_id("run-1", identity.repository_id)

    journal.record_execution_lifecycle(
        fleet_run_id="run-1",
        repository_id=identity.repository_id,
        operation_id=operation_id,
        event_type=FleetRunEventType.REPOSITORY_STARTED,
    )
    first = journal.run_events("run-1")
    revision = first.journal_revision

    journal.record_execution_lifecycle(
        fleet_run_id="run-1",
        repository_id=identity.repository_id,
        operation_id=operation_id,
        event_type=FleetRunEventType.REPOSITORY_STARTED,
    )
    replayed = journal.run_events("run-1")

    assert replayed.events == first.events
    assert replayed.journal_revision == revision


def test_execution_lifecycle_conflicting_replay_fails_without_mutation(tmp_path: Path) -> None:
    journal, identity = running_journal(tmp_path)
    operation_id = repository_attempt_operation_id("run-1", identity.repository_id)
    journal.record_execution_lifecycle(
        fleet_run_id="run-1",
        repository_id=identity.repository_id,
        operation_id=operation_id,
        event_type=FleetRunEventType.REPOSITORY_STARTED,
    )
    journal.record_execution_lifecycle(
        fleet_run_id="run-1",
        repository_id=identity.repository_id,
        operation_id=operation_id,
        event_type=FleetRunEventType.STEP_REACHED,
        step_id="build",
        step_order=1,
    )
    before = journal.run_events("run-1")

    with pytest.raises(JournalConflict, match="Conflicting lifecycle replay"):
        journal.record_execution_lifecycle(
            fleet_run_id="run-1",
            repository_id=identity.repository_id,
            operation_id=operation_id,
            event_type=FleetRunEventType.STEP_REACHED,
            step_id="build",
            step_order=2,
        )

    after = journal.run_events("run-1")
    assert after.events == before.events
    assert after.journal_revision == before.journal_revision


def test_cancelled_step_preserves_original_receipt_and_rejects_other_process(
    tmp_path: Path,
) -> None:
    from oh_goon.core.models.fleet_plan_evidence import (
        PlanExecutionSupervisorEvidence,
        PlanStepCancellationEvidence,
        PlanStepSettlementEvidence,
    )

    journal, identity = running_journal(tmp_path)
    operation_id = start_repository(journal, "run-1", identity)
    observer = journal.execution_lifecycle_observer("run-1", identity.repository_id, operation_id)
    observer("native", 1, "STEP_REACHED")
    observer("native", 1, "STEP_STARTED")
    observer("native", 1, "STEP_ATTEMPT_CREATED", attempt_number=1)
    physical = {"attempt_number": 1, "physical_execution_id": "original-physical"}
    observer("native", 1, "PROCESS_STARTING", **physical)
    physical.update(pid=123, process_identity="original-process-birth")
    observer("native", 1, "PROCESS_STARTED", **physical)
    observer("native", 1, "PROCESS_EXITED", **physical, returncode=-15)
    observer("native", 1, "DESCENDANTS_QUIESCENT", **physical)
    receipt = PlanStepCancellationEvidence(
        settlement=PlanStepSettlementEvidence(
            outcome="CANCELLED",
            reason="CALLER_CANCELLED",
            cleanup="VERIFIED",
            observed_returncode=-15,
        ),
        physical_execution_id="original-physical",
        supervisor=PlanExecutionSupervisorEvidence(
            process_group_cleanup_requested=True,
            cleanup_reason="cancel",
            final_process_group_quiescent=True,
        ),
    )
    before = journal.run_events("run-1")
    with pytest.raises(InvalidRunTransition, match="original Step process"):
        observer(
            "native",
            1,
            "STEP_CANCELLED",
            cancellation=receipt.model_copy(update={"physical_execution_id": "other-process"}),
        )
    assert journal.run_events("run-1") == before
    with pytest.raises(InvalidRunTransition, match="observed exit must match"):
        observer(
            "native",
            1,
            "STEP_CANCELLED",
            cancellation=receipt.model_copy(
                update={
                    "settlement": receipt.settlement.model_copy(update={"observed_returncode": 99})
                }
            ),
        )
    assert journal.run_events("run-1") == before
    observer("native", 1, "STEP_CANCELLED", cancellation=receipt)
    reopened = Journal(LocalDatabase(tmp_path / "state.db"))
    assert reopened.run_events("run-1").events[-1].cancellation == receipt
    revision = reopened.run_events("run-1").journal_revision
    reopened.execution_lifecycle_observer("run-1", identity.repository_id, operation_id)(
        "native", 1, "STEP_CANCELLED", cancellation=receipt
    )
    assert reopened.run_events("run-1").journal_revision == revision


def test_incomplete_cancellation_cannot_publish_invented_verified_cleanup(tmp_path: Path) -> None:
    from oh_goon.core.models.fleet_plan_evidence import PlanStepCancellationEvidence
    from oh_goon.core.models.run_events import StepCancelledEvent

    receipt = {
        "settlement": {
            "outcome": "INCOMPLETE",
            "reason": "SUPERVISION_UNAVAILABLE",
            "cleanup": "UNAVAILABLE",
        }
    }
    baseline = PlanStepCancellationEvidence.model_validate(receipt)
    receipt["settlement"]["cleanup"] = "VERIFIED"
    with pytest.raises(ValidationError, match="Incomplete cancellation cannot claim verified"):
        PlanStepCancellationEvidence.model_validate(receipt)
    journal, identity = running_journal(tmp_path)
    operation_id = start_repository(journal, "run-1", identity)
    observer = journal.execution_lifecycle_observer("run-1", identity.repository_id, operation_id)
    observer("native", 1, "STEP_REACHED")
    before = journal.run_events("run-1")
    persisted = {
        "event_type": "STEP_CANCELLED",
        "sequence": before.next_sequence + 1,
        "fleet_run_id": "run-1",
        "repository_id": identity.repository_id,
        "operation_id": operation_id,
        "step_id": "native",
        "step_order": 1,
        "cancellation": receipt,
    }
    admitted_event = StepCancelledEvent.model_validate_json(
        json.dumps({**persisted, "cancellation": baseline.model_dump(mode="json")})
    )
    assert admitted_event.cancellation == baseline
    with pytest.raises(ValidationError, match="Incomplete cancellation cannot claim verified"):
        StepCancelledEvent.model_validate_json(json.dumps(persisted))
    with pytest.raises(ValidationError, match="Incomplete cancellation cannot claim verified"):
        observer("native", 1, "STEP_CANCELLED", cancellation=receipt)
    assert journal.run_events("run-1") == before
    observer("native", 1, "STEP_CANCELLED", cancellation=baseline)
    assert journal.run_events("run-1").events[-1].cancellation == baseline


def test_cancelled_command_rejects_native_claims_without_an_accepted_step_result(
    tmp_path: Path,
) -> None:
    from oh_goon.core.models.fleet_observation import NativeAgentObservation
    from oh_goon.core.models.fleet_plan import ResolvedCommandStep
    from oh_goon.core.models.fleet_plan_evidence import PlanStepCancellationEvidence
    from oh_goon.core.models.run_events import StepCancelledEvent
    from oh_goon.engine.errors import OperationCancelledError
    from oh_goon.integrations._fleet_plan_step_evidence import cancellation_evidence

    step = ResolvedCommandStep(step_id="command", argv=("work",))
    baseline = cancellation_evidence(step, OperationCancelledError("before launch"))
    foreign = NativeAgentObservation(
        agent="pi",
        implementation_identity="foreign-implementation",
        profile="foreign-profile",
        input_identity="foreign-input",
        occurrence="another-step",
        source_identity="foreign-source",
        role="proposal",
        native_event_count=0,
        settled=True,
        failed=False,
    )
    payload = baseline.model_dump(mode="json")
    payload["native_agent_observations"] = [foreign.model_dump(mode="json")]
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        PlanStepCancellationEvidence.model_validate(payload)
    journal, identity = running_journal(tmp_path)
    operation_id = start_repository(journal, "run-1", identity)
    observer = journal.execution_lifecycle_observer("run-1", identity.repository_id, operation_id)
    observer(step.step_id, 1, "STEP_REACHED")
    before = journal.run_events("run-1")
    persisted = {
        "event_type": "STEP_CANCELLED",
        "sequence": before.next_sequence + 1,
        "fleet_run_id": "run-1",
        "repository_id": identity.repository_id,
        "operation_id": operation_id,
        "step_id": step.step_id,
        "step_order": 1,
        "cancellation": baseline.model_dump(mode="json"),
    }
    assert StepCancelledEvent.model_validate_json(json.dumps(persisted)).cancellation == baseline
    persisted["cancellation"] = payload
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        StepCancelledEvent.model_validate_json(json.dumps(persisted))
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        observer(step.step_id, 1, "STEP_CANCELLED", cancellation=payload)
    assert journal.run_events("run-1") == before
    observer(step.step_id, 1, "STEP_CANCELLED", cancellation=baseline)
    reopened = Journal(LocalDatabase(tmp_path / "state.db"))
    assert reopened.run_events("run-1").events[-1].cancellation == baseline


def test_execution_lifecycle_rejects_valid_attempt_under_wrong_repository(tmp_path: Path) -> None:
    journal = Journal(LocalDatabase(tmp_path / "wrong-parent.db"))
    first = RepositoryIdentity(name="first", path=tmp_path / "first")
    second = RepositoryIdentity(name="second", path=tmp_path / "second")
    admit_test_run(journal, "run-scope", OperationAction.INIT, [first, second])
    start_run(journal, "run-scope")
    operation_id = repository_attempt_operation_id("run-scope", first.repository_id)
    before = journal.run_events("run-scope")

    with pytest.raises(InvalidRunTransition, match="canonical"):
        journal.record_execution_lifecycle(
            fleet_run_id="run-scope",
            repository_id=second.repository_id,
            operation_id=operation_id,
            event_type=FleetRunEventType.REPOSITORY_STARTED,
        )

    after = journal.run_events("run-scope")
    assert after.events == before.events
    assert after.journal_revision == before.journal_revision


def test_step_attempt_creation_is_attempt_bound_and_contiguous(tmp_path: Path) -> None:
    journal, identity = running_journal(tmp_path)
    operation_id = start_repository(journal, "run-1", identity)
    for event_type in (FleetRunEventType.STEP_REACHED, FleetRunEventType.STEP_STARTED):
        journal.record_execution_lifecycle(
            fleet_run_id="run-1",
            repository_id=identity.repository_id,
            operation_id=operation_id,
            event_type=event_type,
            step_id="build",
            step_order=1,
        )

    journal.record_execution_lifecycle(
        fleet_run_id="run-1",
        repository_id=identity.repository_id,
        operation_id=operation_id,
        event_type=FleetRunEventType.STEP_ATTEMPT_CREATED,
        step_id="build",
        step_order=1,
        attempt_number=1,
    )
    first = journal.run_events("run-1")
    created = first.events[-1]
    assert created.event_type is FleetRunEventType.STEP_ATTEMPT_CREATED
    assert created.step_attempt_id == fleet_step_attempt_id(operation_id, "build", 1)

    with pytest.raises(InvalidRunTransition, match="contiguously"):
        journal.record_execution_lifecycle(
            fleet_run_id="run-1",
            repository_id=identity.repository_id,
            operation_id=operation_id,
            event_type=FleetRunEventType.STEP_ATTEMPT_CREATED,
            step_id="build",
            step_order=1,
            attempt_number=3,
        )

    after = journal.run_events("run-1")
    assert after.events == first.events
    assert after.journal_revision == first.journal_revision


def test_concurrent_lifecycle_writers_allocate_one_contiguous_sequence_and_survive_reopen(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "concurrent-lifecycle.db"
    journal = Journal(LocalDatabase(database_path))
    identity = RepositoryIdentity(name="api", path=tmp_path / "api")
    admit_test_run(journal, "run-concurrent", OperationAction.INIT, [identity])
    start_run(journal, "run-concurrent")
    operation_id = start_repository(journal, "run-concurrent", identity)
    steps = tuple((f"step-{index}", index) for index in range(1, 17))

    def write_step(item: tuple[str, int]) -> None:
        step_id, step_order = item
        journal.record_execution_lifecycle(
            fleet_run_id="run-concurrent",
            repository_id=identity.repository_id,
            operation_id=operation_id,
            event_type=FleetRunEventType.STEP_REACHED,
            step_id=step_id,
            step_order=step_order,
        )

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(write_step, steps))

    batch = journal.run_events("run-concurrent")
    assert [event.sequence for event in batch.events] == list(range(1, len(batch.events) + 1))
    reached = [
        event for event in batch.events if event.event_type is FleetRunEventType.STEP_REACHED
    ]
    assert len(reached) == len(steps)
    assert {event.step_id for event in reached} == {step_id for step_id, _ in steps}

    reopened = Journal(LocalDatabase(database_path))
    reopened_batch = reopened.run_events("run-concurrent")
    assert reopened_batch.events == batch.events
    assert [
        event.sequence for event in reopened.run_events("run-concurrent", after=7).events
    ] == list(range(8, len(batch.events) + 1))


def test_fleet_operation_commit_appends_report_and_completion_as_one_fact(tmp_path: Path) -> None:
    journal, identity = running_journal(tmp_path)
    report = operation(identity, OperationAction.INIT, "run-1", "op-1")
    operation_id = report.provenance.operation_id

    start_repository(journal, "run-1", identity)
    commit_operation(journal, report)
    state = terminalize_run(journal, "run-1", FleetRunTerminalIntent.COMPLETE)

    assert state.status is FleetRunStatus.COMPLETED
    assert journal.require_operation(operation_id) == report
    detail = journal.read_view().run_detail("run-1")
    assert detail is not None and detail.operations == (report,)
    batch = journal.run_events("run-1")
    assert batch.journal_revision == journal.read_view().revision
    assert [event.event_type for event in batch.events] == [
        FleetRunEventType.RUN_ADMITTED,
        FleetRunEventType.RUN_STARTED,
        FleetRunEventType.REPOSITORY_STARTED,
        FleetRunEventType.REPOSITORY_COMPLETED,
        FleetRunEventType.RUN_COMPLETED,
    ]
    assert batch.events[3].operation_id == operation_id


def test_failed_fleet_commit_persists_neither_report_nor_completion_event(tmp_path: Path) -> None:
    journal, identity = running_journal(tmp_path)
    wrong = operation(identity, OperationAction.UPDATE, "run-1", "op-wrong")
    before = journal.run_events("run-1")

    with pytest.raises(InvalidRunTransition, match="capability snapshot"):
        commit_operation(journal, wrong)

    assert journal.operation("op-wrong") is None
    after = journal.run_events("run-1")
    assert after.events == before.events
    assert after.run.progress.completed == 0


@pytest.mark.parametrize("settlement_kind", ("missing", "timeout"))
def test_copied_invalid_step_settlement_is_rejected_before_journal_mutation(
    tmp_path: Path, settlement_kind: str
) -> None:
    from oh_goon.core.models.fleet_plan import ResolvedCommandStep, ResolvedFleetPlan
    from oh_goon.core.models.fleet_plan_evidence import FleetPlanResult, PlanStepSettlementEvidence
    from oh_goon.core.models.fleet_plan_execution_policy import VerificationExecutionPolicy
    from oh_goon.core.models.operation import FleetPlanOperationEvidence
    from oh_goon.core.models.run_admission import (
        DirectFleetExecutionAdmission,
        FleetRunAdmissionIntent,
        fleet_repository_scope,
    )
    from oh_goon.engine.command import CommandResult, CommandSupervisorReceipt
    from oh_goon.integrations._fleet_plan_step_evidence import step_evidence
    from oh_goon.integrations._fleet_plan_step_outcome import from_command
    from tests.factories.capabilities import capability_snapshot
    from tests.factories.fleet_plan_targets import structural_fleet_plan_target
    from tests.factories.journal import admit_run
    from tests.factories.run_admission import repository_admissions_for

    database_path = tmp_path / "state.db"
    database = LocalDatabase(database_path)
    journal = Journal(database)
    repository = RepositoryIdentity.model_validate({"path": tmp_path / "repo"})
    command = ResolvedCommandStep(step_id="verification", argv=("native-verifier",))
    plan = ResolvedFleetPlan.create(
        name="verified", description="", execution=VerificationExecutionPolicy(), steps=(command,)
    )
    target = structural_fleet_plan_target(plan)
    completed = step_evidence(
        step=command,
        outcome=from_command(
            CommandResult(
                argv=command.argv,
                returncode=0,
                stdout="Independent verification passed",
                stderr="",
                supervisor=CommandSupervisorReceipt(final_process_group_quiescent=True),
            )
        ),
        repository_state_before="before",
        repository_state_after="after",
        worktree_files_after=(),
        attempts=(),
        artifacts=(),
    )
    assert completed.status == "COMPLETED" and completed.settlement.outcome == "SUCCESS"

    def prepare_report(run_id: str) -> OperationReport:
        intent = FleetRunAdmissionIntent(
            target=target,
            capability=capability_snapshot(OperationAction.EXECUTE_PLAN, target),
            execution=DirectFleetExecutionAdmission(),
            repository_admissions=repository_admissions_for(target, (repository,)),
        )
        admit_run(
            journal,
            run_id,
            intent,
            fleet_repository_scope({repository.repository_id}),
            [repository],
        )
        start_run(journal, run_id)
        operation_id = start_repository(journal, run_id, repository)
        observer = journal.execution_lifecycle_observer(
            run_id, repository.repository_id, operation_id
        )
        observer(command.step_id, 1, "STEP_REACHED")
        observer(command.step_id, 1, "STEP_STARTED")
        observer(command.step_id, 1, "STEP_SUCCEEDED", returncode=0)
        return make_operation_report(
            repository,
            OperationAction.EXECUTE_PLAN,
            target=target,
            fleet_run_id=run_id,
            operation_id=operation_id,
            evidence=FleetPlanOperationEvidence(
                kind="FLEET_PLAN", plan=FleetPlanResult(plan_id=plan.plan_id, steps=(completed,))
            ),
        )

    valid = prepare_report("valid")
    commit_operation(journal, valid)
    accepted = journal.read_view()
    committed = accepted.require_operation(valid.provenance.operation_id)
    assert committed == valid and committed is not valid
    assert committed.evidence is not valid.evidence
    commit_operation(journal, valid)
    assert journal.read_view().revision == accepted.revision
    terminalize_run(journal, "valid", FleetRunTerminalIntent.COMPLETE)
    assert (
        Journal(LocalDatabase(database_path)).require_operation(valid.provenance.operation_id)
        == valid
    )

    pending = prepare_report("copied")
    assert isinstance(pending.evidence, FleetPlanOperationEvidence)
    settlement = (
        PlanStepSettlementEvidence()
        if settlement_kind == "missing"
        else PlanStepSettlementEvidence(
            outcome="TIMED_OUT",
            cleanup="VERIFIED",
            reason="DEADLINE_EXPIRED",
            observed_returncode=-15,
        )
    )
    copied_step = completed.model_copy(update={"settlement": settlement})
    copied_result = pending.evidence.plan.model_copy(update={"steps": (copied_step,)})
    copied_evidence = pending.evidence.model_copy(update={"plan": copied_result})
    copied_report = pending.model_copy(update={"evidence": copied_evidence})
    before = journal.read_view()
    before_events = journal.run_events("copied")
    with database.transaction(write=False) as connection:
        before_rows = connection.execute("SELECT COUNT(*) FROM journal_entries").fetchone()[0]
    with pytest.raises(ValidationError, match="accepted SUCCESS settlement"):
        commit_operation(journal, copied_report)
    with database.transaction(write=False) as connection:
        assert (
            connection.execute("SELECT COUNT(*) FROM journal_entries").fetchone()[0] == before_rows
        )
    assert journal.read_view().revision == before.revision
    assert journal.operation(pending.provenance.operation_id) is None
    assert journal.run_events("copied") == before_events
    reopened = Journal(LocalDatabase(database_path))
    assert reopened.read_view().revision == before.revision
    assert reopened.require_operation(valid.provenance.operation_id) == valid
    assert reopened.operation(pending.provenance.operation_id) is None
    assert reopened.run_events("copied") == before_events


def test_fleet_report_cannot_be_committed_without_owning_run(tmp_path: Path) -> None:
    journal = Journal(LocalDatabase(tmp_path / "state.db"))
    identity = RepositoryIdentity(name="api", path=tmp_path / "api")
    report = operation(identity, OperationAction.INIT, "missing-run", "op-orphan")

    with pytest.raises(RunNotFound, match="Unknown fleet run"):
        commit_operation(journal, report)

    assert journal.operation("op-orphan") is None


def test_direct_operation_is_one_report_fact_without_run_event(tmp_path: Path) -> None:
    journal = Journal(LocalDatabase(tmp_path / "state.db"))
    identity = RepositoryIdentity(name="api", path=tmp_path / "api")
    report = operation(identity, OperationAction.PLAN, None, "op-direct")

    commit_operation(journal, report)

    assert journal.require_operation("op-direct") == report
    assert journal.history(identity.path) == [report]


def test_run_targets_are_immutable_and_own_progress_total(tmp_path: Path) -> None:
    journal = Journal(LocalDatabase(tmp_path / "state.db"))
    first = RepositoryIdentity(name="first", path=tmp_path / "first")
    second = RepositoryIdentity(name="second", path=tmp_path / "second")
    state = admit_test_run(journal, "run-targets", OperationAction.INIT, [first, second])

    assert state.progress.total == 2
    with pytest.raises(Exception, match="already"):
        admit_test_run(journal, "run-targets", OperationAction.INIT, [first])


def test_run_cannot_complete_before_every_target_has_committed_result(tmp_path: Path) -> None:
    journal = Journal(LocalDatabase(tmp_path / "state.db"))
    first = RepositoryIdentity(name="first", path=tmp_path / "first")
    second = RepositoryIdentity(name="second", path=tmp_path / "second")
    admit_test_run(journal, "run-incomplete", OperationAction.INIT, [first, second])
    start_run(journal, "run-incomplete")
    claim_effect(journal, "run-incomplete", first)
    start_repository(journal, "run-incomplete", first)
    commit_operation(journal, operation(first, OperationAction.INIT, "run-incomplete", "op-first"))

    with pytest.raises(InvalidRunTransition, match="every captured target"):
        terminalize_run(journal, "run-incomplete", FleetRunTerminalIntent.COMPLETE)


def test_journal_has_no_in_memory_fact_store(tmp_path: Path) -> None:
    journal, identity = running_journal(tmp_path)
    start_repository(journal, "run-1", identity)
    commit_operation(journal, operation(identity, OperationAction.INIT, "run-1", "op-1"))

    assert not hasattr(journal, "_entries")
    assert not hasattr(journal, "_records_by_operation")
    assert not hasattr(journal, "_timeline_by_repository")
    assert not hasattr(journal, "_event_streams")
    assert not hasattr(journal, "_runs")
    assert journal.read_view().revision == 4


def test_read_view_is_pinned_to_one_journal_revision(tmp_path: Path) -> None:
    journal = Journal(LocalDatabase(tmp_path / "state.db"))
    identity = RepositoryIdentity(name="api", path=tmp_path / "api")
    commit_operation(journal, operation(identity, OperationAction.INIT, None, "op-init"))

    view = journal.read_view()
    commit_operation(
        journal, operation(identity, OperationAction.UPDATE, None, "op-update", Verdict.UNCHANGED)
    )

    pinned_init = view.current_capability(identity.path, "initialize")
    live_init = journal.current_capability(identity.path, "initialize")
    assert pinned_init is not None and pinned_init.freshness is ResultFreshness.CURRENT
    assert live_init is not None and live_init.freshness is ResultFreshness.STALE
    assert view.revision == 1
    assert journal.read_view().revision == 2


def test_pinned_run_event_batch_keeps_its_journal_watermark_after_later_commits(
    tmp_path: Path,
) -> None:
    journal, identity = running_journal(tmp_path)
    pinned = journal.read_view()
    before = pinned.run_events("run-1")

    start_repository(journal, "run-1", identity)
    commit_operation(journal, operation(identity, OperationAction.INIT, "run-1", "op-later"))
    terminalize_run(journal, "run-1", FleetRunTerminalIntent.COMPLETE)

    still_pinned = pinned.run_events("run-1")
    live = journal.run_events("run-1")
    assert still_pinned.journal_revision == before.journal_revision == pinned.revision
    assert still_pinned.run.status is FleetRunStatus.RUNNING
    assert live.journal_revision > still_pinned.journal_revision
    assert live.run.status is FleetRunStatus.COMPLETED


def test_journal_history_survives_restart_but_previous_session_result_is_stale(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "persistent.db"
    first_database = LocalDatabase(database_path)
    first = Journal(first_database)
    identity = RepositoryIdentity(name="api", path=tmp_path / "api")
    commit_operation(first, operation(identity, OperationAction.INIT, None, "op-persisted"))
    assert (
        first.current_capability(identity.path, "initialize").freshness is ResultFreshness.CURRENT
    )  # type: ignore[union-attr]

    restarted = Journal(LocalDatabase(database_path))
    current = restarted.current_capability(identity.path, "initialize")

    assert current is not None
    assert current.report.provenance.operation_id == "op-persisted"
    assert current.freshness is ResultFreshness.STALE
    assert [item.provenance.operation_id for item in restarted.history(identity.path)] == [
        "op-persisted"
    ]


def _complete_run(
    journal: Journal,
    identity: RepositoryIdentity,
    *,
    run_id: str,
    action: OperationAction,
    operation_id: str,
    verdict: Verdict = Verdict.READY,
) -> None:
    admit_test_run(journal, run_id, action, [identity])
    start_run(journal, run_id)
    claim_effect(journal, run_id, identity)
    start_repository(journal, run_id, identity)
    commit_operation(journal, operation(identity, action, run_id, operation_id, verdict))
    terminalize_run(journal, run_id, FleetRunTerminalIntent.COMPLETE)
