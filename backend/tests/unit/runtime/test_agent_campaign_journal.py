"""Campaign decisions share the real Journal transaction and verified read cut."""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path

import pytest

from oh_goon.core.errors import JournalConflict
from oh_goon.core.models.agent_repair import (
    MAX_AGENT_CAMPAIGN_MESSAGE_CHARS,
    TRUNCATED_AGENT_CAMPAIGN_MESSAGE,
    AgentCampaignDecision,
    AgentCampaignIntent,
    AgentCampaignRunEvidence,
    AgentCampaignUnstartedSelection,
    AgentRepairBudget,
    AgentRepairContext,
    AgentRepairStopReason,
)
from oh_goon.core.models.fleet_plan import ResolvedAgentStep, ResolvedCommandStep, ResolvedFleetPlan
from oh_goon.core.models.fleet_plan_evidence import (
    CompletedPlanStepExecution,
    FailedPlanStepExecution,
    FleetPlanResult,
    PlanEgressEvidence,
    PlanExecutionSupervisorEvidence,
    PlanOutputEvidence,
    PlanStepAttemptEvidence,
    PlanStepSettlementEvidence,
    SkippedPlanStepExecution,
)
from oh_goon.core.models.fleet_plan_execution_policy import (
    FleetPlanRunLimits,
    MaintenanceExecutionPolicy,
)
from oh_goon.core.models.goon_plan_source import PlanFileSource
from oh_goon.core.models.operation import FleetPlanOperationEvidence
from oh_goon.core.models.repository import RepositoryIdentity
from oh_goon.core.models.run_admission import (
    DirectFleetExecutionAdmission,
    FleetRunAdmissionIntent,
    fleet_repository_scope,
)
from oh_goon.core.models.target import AgentStepDeliveryReference, FleetPlanTarget
from oh_goon.core.product_identity import repository_id
from oh_goon.core.time import utc_now
from oh_goon.core.vocabulary import (
    FleetRunEventType,
    FleetRunTerminalIntent,
    OperationAction,
    Verdict,
)
from oh_goon.runtime.database import LocalDatabase
from oh_goon.runtime.journal import Journal
from tests.factories.capabilities import capability_snapshot
from tests.factories.fleet_plan_targets import (
    structural_execution_identity,
    structural_fleet_plan_target,
)
from tests.factories.journal import (
    admit_run,
    commit_operation,
    start_repository,
    start_run,
    terminalize_run,
)
from tests.factories.operation_reports import make_operation_report
from tests.factories.run_admission import repository_admissions_for


def campaign(tmp_path: Path, *, initial_phase="review", failure_driven=False):
    journal = Journal(LocalDatabase(tmp_path / "state.db"))
    repository = RepositoryIdentity(
        repository_id=repository_id(tmp_path / "repo"), name="repo", path=tmp_path / "repo"
    )
    budget = AgentRepairBudget(max_attempts=2, timeout_seconds=60, step_timeout_seconds=10)
    intent = AgentCampaignIntent(
        campaign_id="campaign",
        repository_id=repository.repository_id,
        original_execution_digest="original",
        budget=budget,
        maximum_runs=2,
        initial_phase="repair" if failure_driven else initial_phase,
        initial_step_ids=(initial_phase, "agent") if failure_driven else (initial_phase,),
        deadline_at=utc_now() + timedelta(seconds=60),
        candidate_identity=None if failure_driven else "candidate",
        scope_identity=None if failure_driven else "scope",
        write_roots=() if failure_driven else ("code.py",),
    )
    journal.begin_agent_campaign(intent)
    return journal, repository, intent


def phase_target(journal: Journal, *, ordinal=1, phase="review", failure_driven=False):
    current = journal.agent_campaign("campaign")
    intent = current.intent
    assert intent is not None
    plan = ResolvedFleetPlan.create(
        name="phase",
        description="Journal boundary fixture",
        execution=MaintenanceExecutionPolicy(),
        steps=(ResolvedCommandStep(step_id=phase, argv=("check",), timeout_seconds=10),)
        + (
            (
                ResolvedAgentStep(
                    step_id="agent",
                    timeout_seconds=10,
                    workload={"harness": "local-model-proposal", "model": "fixture"},
                    delivery_sha256="a" * 64,
                    handoff_identity="frozen",
                    source_step_id="agent",
                ),
            )
            if failure_driven
            else ()
        ),
    )
    context = AgentRepairContext(
        repair_id="campaign",
        campaign_revision=current.latest_revision,
        attempt=ordinal,
        max_attempts=2,
        previous_run_id="campaign" if ordinal == 2 else None,
        phase=None if failure_driven else phase,
        review_pass=None if failure_driven else 1,
        original_execution_digest=None if failure_driven else "original",
        candidate_identity=None if failure_driven else "candidate",
        scope_identity=None if failure_driven else "scope",
        write_roots=() if failure_driven else ("code.py",),
        limits=FleetPlanRunLimits(deadline_at=intent.deadline_at, step_timeout_seconds=10),
    )
    if failure_driven:
        return FleetPlanTarget(
            kind="fleet_plan",
            plan=plan,
            source=PlanFileSource(sha256="0" * 64),
            identity=structural_execution_identity(plan),
            agent_repair=context,
            agent_deliveries=(
                AgentStepDeliveryReference(
                    step_id="agent",
                    handoff_identity="frozen",
                    delivery_sha256="a" * 64,
                    reference_id="retained-fixture",
                ),
            ),
        )
    base = structural_fleet_plan_target(plan)
    return FleetPlanTarget(**{**base.model_dump(), "agent_repair": context})


def admit_phase(journal, repository, *, ordinal=1, phase="review", failure_driven=False):
    run_id = "campaign" if ordinal == 1 else "second"
    target = phase_target(journal, ordinal=ordinal, phase=phase, failure_driven=failure_driven)
    intent = FleetRunAdmissionIntent(
        target=target,
        capability=capability_snapshot(OperationAction.EXECUTE_PLAN, target),
        execution=DirectFleetExecutionAdmission(),
        repository_admissions=repository_admissions_for(target, (repository,)),
    )
    admit_run(
        journal, run_id, intent, fleet_repository_scope({repository.repository_id}), [repository]
    )
    return run_id, target


def settle(journal, repository, run_id, target, *, exit_code=0, settlement=None, attempts=True):
    start_run(journal, run_id)
    operation_id = start_repository(journal, run_id, repository)
    for event_type in (
        FleetRunEventType.STEP_REACHED,
        FleetRunEventType.STEP_STARTED,
        FleetRunEventType.STEP_SUCCEEDED if exit_code == 0 else FleetRunEventType.STEP_FAILED,
    ):
        journal.record_execution_lifecycle(
            fleet_run_id=run_id,
            repository_id=repository.repository_id,
            operation_id=operation_id,
            event_type=event_type,
            step_id=target.plan.steps[0].step_id,
            step_order=1,
            returncode=exit_code
            if event_type in {FleetRunEventType.STEP_SUCCEEDED, FleetRunEventType.STEP_FAILED}
            else None,
        )
    if target.agent_repair.phase is None:
        for event_type in (FleetRunEventType.STEP_REACHED, FleetRunEventType.STEP_SKIPPED):
            journal.record_execution_lifecycle(
                fleet_run_id=run_id,
                repository_id=repository.repository_id,
                operation_id=operation_id,
                event_type=event_type,
                step_id="agent",
                step_order=2,
            )
    output = PlanOutputEvidence(sha256="0" * 64, byte_count=0, line_count=0)
    step_type = CompletedPlanStepExecution if exit_code == 0 else FailedPlanStepExecution
    report = make_operation_report(
        repository,
        OperationAction.EXECUTE_PLAN,
        target=target,
        fleet_run_id=run_id,
        operation_id=operation_id,
        returncode=exit_code,
        verdict=Verdict.READY if exit_code == 0 else Verdict.FAILED,
        evidence=FleetPlanOperationEvidence(
            kind="FLEET_PLAN",
            plan=FleetPlanResult(
                plan_id=target.plan.plan_id,
                steps=(
                    step_type(
                        step_id=target.plan.steps[0].step_id,
                        kind="command",
                        argv=("check",),
                        stdout_evidence=output,
                        stderr_evidence=output,
                        returncode=exit_code,
                        repository_state_before="before",
                        repository_state_after="after",
                        settlement=settlement
                        if settlement is not None
                        else PlanStepSettlementEvidence(
                            outcome="SUCCESS" if exit_code == 0 else "FAILED",
                            cleanup="VERIFIED",
                            reason="ACCEPTED_EXIT" if exit_code == 0 else "FAILED_EXIT",
                            observed_returncode=exit_code,
                        ),
                        attempts=(
                            PlanStepAttemptEvidence(
                                attempt=1,
                                returncode=exit_code,
                                stdout_evidence=output,
                                stderr_evidence=output,
                                repository_state_before="before",
                                repository_state_after="after",
                                egress=PlanEgressEvidence(mode="none"),
                                supervisor=PlanExecutionSupervisorEvidence(
                                    final_process_group_quiescent=True
                                ),
                            ),
                        )
                        if attempts
                        else (),
                    ),
                )
                + (
                    (
                        SkippedPlanStepExecution(
                            step_id="agent",
                            kind="agent",
                            reason="Condition not selected",
                            stdout_evidence=output,
                            stderr_evidence=output,
                            repository_state_before="before",
                            repository_state_after="after",
                            settlement=PlanStepSettlementEvidence(
                                outcome="SKIPPED", cleanup="UNAVAILABLE", reason="NOT_STARTED"
                            ),
                        ),
                    )
                    if target.agent_repair.phase is None
                    else ()
                ),
            ),
        ),
    )
    commit_operation(journal, report)
    terminalize_run(journal, run_id, FleetRunTerminalIntent.COMPLETE)


def decision(
    journal,
    *,
    stop=None,
    decision_id="advance",
    next_phase="verification",
    passed_test_step_id=None,
    next_step_ids=None,
):
    current = journal.agent_campaign("campaign")
    view = journal.read_view()
    evidence = []
    unresolved = []
    for phase in current.phases:
        detail = view.required_run_detail(phase.fleet_run_id)
        if detail.run.status.terminal:
            evidence.append(
                AgentCampaignRunEvidence(
                    fleet_run_id=phase.fleet_run_id,
                    terminal_sequence=view.run_events(phase.fleet_run_id).events[-1].sequence,
                    operation_ids=tuple(item.provenance.operation_id for item in detail.operations),
                )
            )
        else:
            unresolved.append(phase.fleet_run_id)
    return AgentCampaignDecision(
        campaign_id="campaign",
        expected_revision=current.latest_revision,
        decision_id=decision_id,
        phase_ordinal=len(current.phases),
        remaining_runs=2 - len(current.phases),
        consumed_runs=tuple(evidence),
        unresolved_run_ids=tuple(unresolved),
        stop_reason=stop,
        passed_test_step_id=passed_test_step_id,
        next_phase=next_phase if stop is None else None,
        next_step_ids=(
            next_step_ids
            if next_step_ids is not None
            else (
                (next_phase, "agent")
                if current.phases and current.phases[-1].context.phase is None
                else (next_phase,)
            )
        )
        if stop is None
        else (),
        candidate_identity="candidate",
        scope_identity="scope",
    )


def test_admission_and_exact_phase_linkage_rollback_together(tmp_path, monkeypatch):
    journal, repository, _ = campaign(tmp_path)
    initial = journal.read_view().revision
    append = journal._facts.append_run_event

    def fail_after_phase(*_):
        raise OSError("disk full during Run admission")

    monkeypatch.setattr(journal._facts, "append_run_event", fail_after_phase)
    with pytest.raises(OSError, match="disk full"):
        admit_phase(journal, repository)
    assert journal.read_view().revision == initial
    assert journal.agent_campaign("campaign").phases == ()
    assert journal.read_view().run_detail("campaign") is None
    monkeypatch.setattr(journal._facts, "append_run_event", append)
    run_id, target = admit_phase(journal, repository)
    phase = journal.agent_campaign("campaign").phases[0]
    assert (phase.fleet_run_id, phase.context, phase.selected_step_ids) == (
        run_id,
        target.agent_repair,
        ("review",),
    )


def test_committed_terminal_receipt_survives_restart_and_exact_retry(tmp_path):
    journal, repository, intent = campaign(tmp_path)
    run_id, target = admit_phase(journal, repository)
    settle(journal, repository, run_id, target)
    advance = decision(journal)
    journal.commit_agent_campaign_decision(advance)
    run_id, target = admit_phase(journal, repository, ordinal=2, phase="verification")
    settle(journal, repository, run_id, target)
    terminal = decision(journal, stop=AgentRepairStopReason.CONVERGED, decision_id="terminal")
    journal.commit_agent_campaign_decision(terminal)
    receipt = journal.agent_campaign_result("campaign")
    assert receipt.converged and receipt.committed_decision_id == "terminal"
    assert receipt.admitted_run_ids == ("campaign", "second")
    reopened = Journal(LocalDatabase(tmp_path / "state.db"))
    before = reopened.read_view().revision
    assert reopened.agent_campaign_result("campaign") == receipt
    assert (
        reopened.commit_agent_campaign_decision(terminal).latest_revision
        == receipt.journal_revision
    )
    assert reopened.begin_agent_campaign(intent).terminal_decision == terminal
    assert reopened.read_view().revision == before


def test_successful_child_without_controller_commit_remains_incomplete_and_cannot_resume(tmp_path):
    journal, repository, _ = campaign(tmp_path)
    run_id, target = admit_phase(journal, repository)
    settle(journal, repository, run_id, target)
    pending = decision(journal)
    reopened = Journal(LocalDatabase(tmp_path / "state.db"))
    assert reopened.read_view().required_run_detail(run_id).outcome.value == "SUCCESS"
    assert reopened.agent_campaign("campaign").terminal_decision is None
    with pytest.raises(JournalConflict, match="missing or incomplete"):
        reopened.agent_campaign_result("campaign")
    with pytest.raises(JournalConflict, match="prior controller session"):
        reopened.commit_agent_campaign_decision(pending)


def test_competing_advancements_commit_only_one_successor(tmp_path):
    journal, repository, _ = campaign(tmp_path)
    run_id, target = admit_phase(journal, repository)
    settle(journal, repository, run_id, target)
    first = decision(journal, decision_id="first")
    second = decision(journal, decision_id="second", next_phase="repair")

    def publish(value):
        try:
            journal.commit_agent_campaign_decision(value)
        except JournalConflict:
            return "conflict"
        return "committed"

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(publish, (first, second))) == ["committed", "conflict"]
    records = journal.agent_campaign("campaign").records
    assert sum(isinstance(record.fact, AgentCampaignDecision) for record in records) == 1


def test_convergence_refuses_unresolved_work_and_accepted_cancel(tmp_path):
    journal, repository, _ = campaign(tmp_path)
    run_id, _ = admit_phase(journal, repository)
    with pytest.raises(JournalConflict, match="unresolved work"):
        journal.commit_agent_campaign_decision(
            decision(journal, stop=AgentRepairStopReason.CONVERGED)
        )
    terminalize_run(journal, run_id, FleetRunTerminalIntent.CANCEL)
    with pytest.raises(JournalConflict, match="accepted stop"):
        journal.commit_agent_campaign_decision(
            decision(journal, stop=AgentRepairStopReason.CONVERGED)
        )
    cancelled = decision(journal, stop=AgentRepairStopReason.CANCELLED)
    journal.commit_agent_campaign_decision(cancelled)
    assert journal.agent_campaign_result("campaign").stop_reason is AgentRepairStopReason.CANCELLED


def test_changed_settled_evidence_and_unselected_phase_rejected_before_publication(tmp_path):
    journal, repository, _ = campaign(tmp_path)
    run_id, target = admit_phase(journal, repository)
    settle(journal, repository, run_id, target)
    original = decision(journal)
    wrong = original.model_copy(update={"consumed_runs": ()})
    before = journal.read_view().revision
    with pytest.raises(JournalConflict, match="exact settled"):
        journal.commit_agent_campaign_decision(wrong)
    assert journal.read_view().revision == before
    journal.commit_agent_campaign_decision(original)
    with pytest.raises(JournalConflict, match="exact committed advancement"):
        admit_phase(journal, repository, ordinal=2, phase="repair")
    assert journal.read_view().run_detail("second") is None


def test_controller_diagnostics_are_redacted_before_canonical_persistence(tmp_path):
    journal, _, _ = campaign(tmp_path)
    canary = "campaign-secret-canary-0f9b"
    terminal = decision(journal, stop=AgentRepairStopReason.EXECUTION_UNRESOLVED)
    terminal = terminal.model_copy(
        update={"message": f"request failed Bearer {canary}; password={canary}"}
    )
    committed = journal.commit_agent_campaign_decision(terminal)
    assert canary not in committed.model_dump_json()
    receipt = journal.agent_campaign_result("campaign")
    assert canary not in receipt.message
    assert "REDACTED" in receipt.message
    with journal._database.transaction() as connection:
        payload = connection.execute(
            "SELECT effect_json FROM journal_entries WHERE kind = 'CAMPAIGN' ORDER BY revision DESC LIMIT 1"
        ).fetchone()[0]
    assert canary not in payload


def test_receipt_declares_full_verified_cut_after_unrelated_later_fact(tmp_path):
    journal, _, intent = campaign(tmp_path)
    terminal = decision(journal, stop=AgentRepairStopReason.CANCELLED)
    committed = journal.commit_agent_campaign_decision(terminal)
    journal.begin_agent_campaign(intent.model_copy(update={"campaign_id": "unrelated"}))
    receipt = journal.agent_campaign_result("campaign")
    assert receipt.journal_revision == journal.read_view().revision
    assert receipt.campaign_journal.journal_revision == receipt.journal_revision
    assert receipt.campaign_journal.latest_revision == committed.latest_revision
    assert receipt.campaign_journal.latest_revision < receipt.journal_revision


def test_convergence_requires_final_verification_and_its_exact_candidate_scope(tmp_path):
    journal, repository, _ = campaign(tmp_path)
    run_id, target = admit_phase(journal, repository)
    settle(journal, repository, run_id, target)
    premature = decision(journal, stop=AgentRepairStopReason.CONVERGED)
    with pytest.raises(JournalConflict, match="final settled verification"):
        journal.commit_agent_campaign_decision(premature)
    journal.commit_agent_campaign_decision(decision(journal))
    run_id, target = admit_phase(journal, repository, ordinal=2, phase="verification")
    settle(journal, repository, run_id, target)
    terminal = decision(journal, stop=AgentRepairStopReason.CONVERGED, decision_id="converge")
    before = journal.read_view().revision
    for change in (
        {"candidate_identity": "stale-candidate"},
        {"scope_identity": "different-scope"},
    ):
        with pytest.raises(JournalConflict, match="terminal candidate/scope"):
            journal.commit_agent_campaign_decision(terminal.model_copy(update=change))
        assert journal.read_view().revision == before
    journal.commit_agent_campaign_decision(terminal)
    assert journal.agent_campaign_result("campaign").converged


def test_blocking_unstarted_selection_must_preserve_original_phase_membership(tmp_path):
    journal, _, _ = campaign(tmp_path)
    terminal = decision(journal, stop=AgentRepairStopReason.EXECUTION_UNRESOLVED)
    selected = AgentCampaignUnstartedSelection(
        selected_run_id="campaign",
        phase_ordinal=1,
        phase="repair",
        selected_step_ids=("unselected",),
    )
    before = journal.read_view().revision
    with pytest.raises(JournalConflict, match="original selected phase/Step membership"):
        journal.commit_agent_campaign_decision(
            terminal.model_copy(update={"unstarted_selection": selected})
        )
    assert journal.read_view().revision == before
    selected = selected.model_copy(update={"phase": "review", "selected_step_ids": ("review",)})
    terminal = terminal.model_copy(update={"unstarted_selection": selected})
    journal.commit_agent_campaign_decision(terminal)
    assert journal.agent_campaign("campaign").terminal_decision.unstarted_selection == selected


def test_redaction_expansion_retains_bounded_idempotent_canonical_diagnostic(tmp_path):
    journal, _, _ = campaign(tmp_path)
    message = "token=x " * (MAX_AGENT_CAMPAIGN_MESSAGE_CHARS // len("token=x "))
    assert len(message) <= MAX_AGENT_CAMPAIGN_MESSAGE_CHARS
    terminal = AgentCampaignDecision.model_validate(
        {
            **decision(journal, stop=AgentRepairStopReason.CANCELLED).model_dump(),
            "message": message,
        }
    )
    committed = journal.commit_agent_campaign_decision(terminal)
    retained = committed.terminal_decision
    assert retained.message == TRUNCATED_AGENT_CAMPAIGN_MESSAGE
    assert len(retained.message) <= MAX_AGENT_CAMPAIGN_MESSAGE_CHARS
    assert "token=x" not in committed.model_dump_json()
    before = journal.read_view().revision
    assert journal.commit_agent_campaign_decision(terminal) == committed
    assert journal.read_view().revision == before
    reopened = Journal(LocalDatabase(tmp_path / "state.db"))
    assert reopened.agent_campaign_result("campaign").message == TRUNCATED_AGENT_CAMPAIGN_MESSAGE
    assert reopened.commit_agent_campaign_decision(terminal).terminal_decision == retained
    assert reopened.read_view().revision == before


@pytest.mark.parametrize(
    "contradiction",
    [
        "failed",
        "missing_attempt",
        "different_step",
        "phase_campaign",
    ],
)
def test_test_passed_publication_requires_its_accepted_native_test(tmp_path, contradiction):
    initial_phase = "test" if contradiction != "phase_campaign" else "review"
    journal, repository, _ = campaign(
        tmp_path, initial_phase=initial_phase, failure_driven=contradiction != "phase_campaign"
    )
    run_id, target = admit_phase(
        journal, repository, phase=initial_phase, failure_driven=contradiction != "phase_campaign"
    )
    settle(
        journal,
        repository,
        run_id,
        target,
        exit_code=17 if contradiction == "failed" else 0,
        attempts=contradiction != "missing_attempt",
    )
    terminal = decision(
        journal,
        stop=AgentRepairStopReason.TEST_PASSED,
        passed_test_step_id="different" if contradiction == "different_step" else initial_phase,
    )
    if contradiction != "phase_campaign":
        terminal = terminal.model_copy(update={"candidate_identity": None, "scope_identity": None})
    before = journal.read_view().revision
    with pytest.raises(JournalConflict, match="exact accepted native test"):
        journal.commit_agent_campaign_decision(terminal)
    assert journal.read_view().revision == before
    assert journal.agent_campaign("campaign").terminal_decision is None


@pytest.mark.parametrize("prior_cleanup", ["VERIFIED", "UNVERIFIED"])
def test_test_passed_reference_preserves_prior_failure_and_requires_all_cleanup(
    tmp_path, prior_cleanup
):
    journal, repository, _ = campaign(tmp_path, initial_phase="test", failure_driven=True)
    run_id, target = admit_phase(journal, repository, phase="test", failure_driven=True)
    settle(
        journal,
        repository,
        run_id,
        target,
        exit_code=17,
        settlement=PlanStepSettlementEvidence(
            outcome="INCOMPLETE", cleanup="UNVERIFIED", reason="CLEANUP_UNVERIFIED"
        )
        if prior_cleanup == "UNVERIFIED"
        else None,
    )
    journal.commit_agent_campaign_decision(
        decision(journal, next_phase="repair", next_step_ids=("test", "agent")).model_copy(
            update={"candidate_identity": None, "scope_identity": None}
        )
    )
    run_id, target = admit_phase(journal, repository, ordinal=2, phase="test", failure_driven=True)
    settle(journal, repository, run_id, target)
    terminal = decision(
        journal,
        stop=AgentRepairStopReason.TEST_PASSED,
        passed_test_step_id="test",
        decision_id="terminal",
    ).model_copy(update={"candidate_identity": None, "scope_identity": None})
    if prior_cleanup == "UNVERIFIED":
        before = journal.read_view().revision
        with pytest.raises(JournalConflict, match="settled cleanup"):
            journal.commit_agent_campaign_decision(terminal)
        assert journal.read_view().revision == before
        assert journal.agent_campaign("campaign").terminal_decision is None
        return
    journal.commit_agent_campaign_decision(terminal)
    reopened = Journal(LocalDatabase(tmp_path / "state.db"))
    receipt = reopened.agent_campaign_result("campaign")
    assert receipt.test_passed
    assert receipt.campaign_journal.terminal_decision.passed_test_step_id == "test"
    assert receipt.attempts[0].operations[0].evidence.plan.steps[0].settlement.outcome == "FAILED"


def test_test_passed_reference_is_mandatory_only_for_that_decision(tmp_path):
    journal, _, _ = campaign(tmp_path)
    with pytest.raises(ValueError, match="accepted native test reference"):
        decision(journal, stop=AgentRepairStopReason.TEST_PASSED)
    with pytest.raises(ValueError, match="accepted native test reference"):
        decision(journal, stop=AgentRepairStopReason.CANCELLED, passed_test_step_id="test")


def test_convergence_consumes_canonical_service_completion_before_publication(tmp_path):
    journal, repository, _ = campaign(tmp_path)
    run_id, target = admit_phase(journal, repository)
    settlement = PlanStepSettlementEvidence(
        outcome="SUCCESS",
        cleanup="VERIFIED",
        reason="ACCEPTED_SERVICE_COMPLETION",
        observed_returncode=-15,
    )
    settle(journal, repository, run_id, target, settlement=settlement)
    journal.commit_agent_campaign_decision(decision(journal))
    run_id, target = admit_phase(journal, repository, ordinal=2, phase="verification")
    settle(journal, repository, run_id, target)
    terminal = decision(journal, stop=AgentRepairStopReason.CONVERGED, decision_id="converge")
    journal.commit_agent_campaign_decision(terminal)
    assert journal.agent_campaign_result("campaign").converged
