"""Campaign decisions share the real Journal transaction and verified read cut."""

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
from typing import Literal

import pytest

from oh_goon.core.agent_campaign_settlement import regression_feedback_context, regression_recovery
from oh_goon.core.errors import JournalConflict
from oh_goon.core.models.agent_repair import (
    MAX_AGENT_CAMPAIGN_MESSAGE_CHARS,
    TRUNCATED_AGENT_CAMPAIGN_MESSAGE,
    AgentCampaignDecision,
    AgentCampaignIntent,
    AgentCampaignRunEvidence,
    AgentCampaignUnstartedSelection,
    AgentRegressionRecovery,
    AgentRepairBudget,
    AgentRepairContext,
    AgentRepairStopReason,
)
from oh_goon.core.models.agent_review import AgentAdjudicationRecord, AgentReviewRecord
from oh_goon.core.models.fleet_observation import AgentReviewObservation
from oh_goon.core.models.fleet_plan import ResolvedAgentStep, ResolvedCommandStep, ResolvedFleetPlan
from oh_goon.core.models.fleet_plan_evidence import (
    CompletedPlanStepExecution,
    FailedPlanStepExecution,
    FleetPlanResult,
    PlanEgressEvidence,
    PlanExecutionSupervisorEvidence,
    PlanOutputEvidence,
    PlanRetainedStreamOutputEvidence,
    PlanStepAttemptEvidence,
    PlanStepSettlementEvidence,
    PublishedPlanOutputEvidence,
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


def campaign(
    tmp_path: Path,
    *,
    initial_phase: str = "review",
    failure_driven: bool = False,
    maximum_runs: int = 2,
    write_roots: tuple[str, ...] = ("code.py",),
) -> tuple[Journal, RepositoryIdentity, AgentCampaignIntent]:
    journal = Journal(LocalDatabase(tmp_path / "state.db"))
    repository = RepositoryIdentity(
        repository_id=repository_id(tmp_path / "repo"), name="repo", path=tmp_path / "repo"
    )
    budget = AgentRepairBudget(
        max_attempts=3 if maximum_runs > 6 else 2, timeout_seconds=60, step_timeout_seconds=10
    )
    intent = AgentCampaignIntent(
        campaign_id="campaign",
        repository_id=repository.repository_id,
        original_execution_digest="original",
        budget=budget,
        maximum_runs=maximum_runs,
        initial_phase="repair" if failure_driven else initial_phase,
        initial_step_ids=(initial_phase, "agent") if failure_driven else (initial_phase,),
        deadline_at=utc_now() + timedelta(seconds=60),
        candidate_identity=None if failure_driven else "candidate",
        scope_identity=None if failure_driven else "scope",
        write_roots=() if failure_driven else write_roots,
    )
    journal.begin_agent_campaign(intent)
    return journal, repository, intent


def phase_target(
    journal: Journal,
    *,
    ordinal: int = 1,
    phase: str = "review",
    failure_driven: bool = False,
    candidate: str = "candidate",
    review_pass: int = 1,
    recoverable: bool = False,
    with_agent: bool = False,
    agent_context: str | None = None,
    scope: str = "scope",
) -> FleetPlanTarget:
    current = journal.agent_campaign("campaign")
    intent = current.intent
    assert intent is not None
    plan = ResolvedFleetPlan.create(
        name="phase",
        description="Journal boundary fixture",
        execution=MaintenanceExecutionPolicy(),
        steps=(
            ResolvedCommandStep.model_validate(
                {
                    "step_id": phase,
                    "argv": ("check",),
                    "timeout_seconds": 10,
                    **(
                        {
                            "on_failure": "continue",
                            "regression_failure": {
                                "exit_codes": (1,),
                                "feedback": "diagnostic",
                                "reason": "Verifier exit 1 is ordinary code failure",
                            },
                            "outputs": (
                                {
                                    "name": "diagnostic",
                                    "stream": "stdout",
                                    "max_bytes": 2048,
                                    "retain": True,
                                },
                            ),
                        }
                        if recoverable
                        else {}
                    ),
                },
            ),
        )
        + (
            (
                ResolvedAgentStep.model_validate(
                    {
                        "step_id": "agent",
                        "timeout_seconds": 10,
                        "workload": {"harness": "local-model-proposal", "model": "fixture"},
                        "delivery_sha256": "a" * 64,
                        "handoff_identity": "frozen",
                        "source_step_id": "agent",
                        "review_context": agent_context,
                    }
                ),
            )
            if failure_driven or with_agent
            else ()
        ),
    )
    context = AgentRepairContext.model_validate(
        {
            "repair_id": "campaign",
            "campaign_revision": current.latest_revision,
            "attempt": ordinal,
            "max_attempts": intent.maximum_runs,
            "previous_run_id": current.phases[-1].fleet_run_id if current.phases else None,
            "phase": None if failure_driven else phase,
            "review_pass": None if failure_driven else review_pass,
            "original_execution_digest": None if failure_driven else "original",
            "candidate_identity": None if failure_driven else candidate,
            "scope_identity": None if failure_driven else scope,
            "write_roots": () if failure_driven else intent.write_roots,
            "limits": FleetPlanRunLimits(deadline_at=intent.deadline_at, step_timeout_seconds=10),
        }
    )
    if failure_driven or with_agent:
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


def admit_phase(
    journal: Journal,
    repository: RepositoryIdentity,
    *,
    ordinal: int = 1,
    phase: str = "review",
    failure_driven: bool = False,
    candidate: str = "candidate",
    review_pass: int = 1,
    recoverable: bool = False,
    with_agent: bool = False,
    agent_context: str | None = None,
    scope: str = "scope",
) -> tuple[str, FleetPlanTarget]:
    run_id = "campaign" if ordinal == 1 else "second" if ordinal == 2 else f"phase-{ordinal}"
    target = phase_target(
        journal,
        ordinal=ordinal,
        phase=phase,
        failure_driven=failure_driven,
        candidate=candidate,
        review_pass=review_pass,
        recoverable=recoverable,
        with_agent=with_agent,
        agent_context=agent_context,
        scope=scope,
    )
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


def settle(
    journal: Journal,
    repository: RepositoryIdentity,
    run_id: str,
    target: FleetPlanTarget,
    *,
    exit_code: int = 0,
    settlement: PlanStepSettlementEvidence | None = None,
    attempts: bool = True,
    record: AgentReviewRecord | AgentAdjudicationRecord | None = None,
    worktree_files_after: tuple[str, ...] = (),
) -> None:
    assert target.agent_repair is not None
    selected = target.plan.steps[0]
    assert isinstance(selected, ResolvedCommandStep)
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
    retained = selected.regression_failure is not None
    diagnostic = b"regression failed\n"
    output = (
        PlanOutputEvidence(
            sha256=hashlib.sha256(diagnostic).hexdigest(), byte_count=len(diagnostic), line_count=1
        )
        if retained
        else PlanOutputEvidence(sha256="0" * 64, byte_count=0, line_count=0)
    )
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
                        worktree_files_after=worktree_files_after,
                        observations=(AgentReviewObservation(record=record),)
                        if record is not None
                        else (),
                        outputs=(
                            PublishedPlanOutputEvidence(
                                name="diagnostic",
                                sha256=output.sha256,
                                byte_count=output.byte_count,
                                retained_stream=PlanRetainedStreamOutputEvidence(
                                    text=diagnostic.decode().strip(),
                                    sha256=hashlib.sha256(diagnostic.strip()).hexdigest(),
                                    byte_count=len(diagnostic.strip()),
                                    source=output,
                                    physical_execution_id="physical-regression",
                                ),
                            ),
                        )
                        if retained
                        else (),
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
                                observed_returncode=exit_code,
                                physical_execution_id="physical-regression" if retained else None,
                                stdout_evidence=output,
                                stderr_evidence=output,
                                repository_state_before="before",
                                repository_state_after="after",
                                worktree_files_after=worktree_files_after,
                                egress=PlanEgressEvidence(mode="none"),
                                supervisor=PlanExecutionSupervisorEvidence(
                                    final_process_group_quiescent=True, forced_termination=False
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
    journal: Journal,
    *,
    stop: AgentRepairStopReason | None = None,
    decision_id: str = "advance",
    next_phase: str = "verification",
    passed_test_step_id: str | None = None,
    next_step_ids: tuple[str, ...] | None = None,
    candidate: str = "candidate",
    recovery: AgentRegressionRecovery | None = None,
    scope: str = "scope",
) -> AgentCampaignDecision:
    current = journal.agent_campaign("campaign")
    assert current.intent is not None
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
        remaining_runs=current.intent.maximum_runs - len(current.phases),
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
        candidate_identity=candidate,
        scope_identity=scope,
        regression_recovery=recovery,
    )


def test_admission_and_exact_phase_linkage_rollback_together(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    journal, repository, _ = campaign(tmp_path)
    initial = journal.read_view().revision
    append = journal._facts.append_run_event

    def fail_after_phase(*_: object) -> None:
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


def test_committed_terminal_receipt_survives_restart_and_exact_retry(tmp_path: Path) -> None:
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
    assert receipt.campaign_journal is not None
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


def test_successful_child_without_controller_commit_remains_incomplete_and_cannot_resume(
    tmp_path: Path,
) -> None:
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


def test_competing_advancements_commit_only_one_successor(tmp_path: Path) -> None:
    journal, repository, _ = campaign(tmp_path)
    run_id, target = admit_phase(journal, repository)
    settle(journal, repository, run_id, target)
    first = decision(journal, decision_id="first")
    second = decision(journal, decision_id="second", next_phase="repair")

    def publish(value: AgentCampaignDecision) -> str:
        try:
            journal.commit_agent_campaign_decision(value)
        except JournalConflict:
            return "conflict"
        return "committed"

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(publish, (first, second))) == ["committed", "conflict"]
    records = journal.agent_campaign("campaign").records
    assert sum(isinstance(record.fact, AgentCampaignDecision) for record in records) == 1


def test_convergence_refuses_unresolved_work_and_accepted_cancel(tmp_path: Path) -> None:
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


def test_changed_settled_evidence_and_unselected_phase_rejected_before_publication(
    tmp_path: Path,
) -> None:
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


def test_controller_diagnostics_are_redacted_before_canonical_persistence(tmp_path: Path) -> None:
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


def test_receipt_declares_full_verified_cut_after_unrelated_later_fact(tmp_path: Path) -> None:
    journal, _, intent = campaign(tmp_path)
    terminal = decision(journal, stop=AgentRepairStopReason.CANCELLED)
    committed = journal.commit_agent_campaign_decision(terminal)
    journal.begin_agent_campaign(intent.model_copy(update={"campaign_id": "unrelated"}))
    receipt = journal.agent_campaign_result("campaign")
    assert receipt.campaign_journal is not None
    assert receipt.journal_revision == journal.read_view().revision
    assert receipt.campaign_journal.journal_revision == receipt.journal_revision
    assert receipt.campaign_journal.latest_revision == committed.latest_revision
    assert receipt.campaign_journal.latest_revision < receipt.journal_revision


def test_convergence_requires_final_verification_and_its_exact_candidate_scope(
    tmp_path: Path,
) -> None:
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


def _recovery_review(
    candidate: str,
    *,
    clean: bool = False,
    scope: str = "scope",
    reviewed_paths: tuple[str, ...] = ("repo/code.py",),
) -> AgentReviewRecord:
    return AgentReviewRecord.model_validate(
        {
            "reviewer": "review",
            "candidate_identity": candidate,
            "scope_identity": scope,
            "skill_identity": "skill",
            "verdict": "CLEAN" if clean else "FINDING",
            "summary": "Review the exact failed candidate",
            "reviewed_paths": reviewed_paths,
            "findings": ()
            if clean
            else (
                {
                    "finding_id": "defect",
                    "explanation": "The native regression proves a defect",
                    "evidence": ({"path": "repo/code.py", "sha256": "a" * 64, "line": 1},),
                },
            ),
        }
    )


def _recovery_adjudication() -> AgentAdjudicationRecord:
    return AgentAdjudicationRecord.model_validate(
        {
            "candidate_identity": "candidate",
            "scope_identity": "scope",
            "dispositions": (
                {
                    "reviewer": "review",
                    "finding_id": "defect",
                    "decision": "repair",
                    "rationale": "Confirmed native failure",
                },
            ),
            "queue": (
                {
                    "queue_id": "repair-defect",
                    "findings": ({"reviewer": "review", "finding_id": "defect"},),
                    "rationale": "Repair the current failed candidate",
                    "regression_expectation": "The native check passes",
                },
            ),
        }
    )


@pytest.mark.parametrize("added_path", (False, True))
def test_recovered_regression_decision_preserves_failed_run_and_reopens_convergence(
    tmp_path: Path,
    added_path: bool,
) -> None:
    final_scope = "scope-after-created-path" if added_path else "scope"
    journal, repository, _ = campaign(
        tmp_path,
        initial_phase="regression",
        maximum_runs=7,
        write_roots=("code.py", "added.py") if added_path else ("code.py",),
    )
    phases = (
        ("regression", "candidate", "scope", 1, 1, None),
        ("review", "candidate", "scope", 2, 0, _recovery_review("candidate")),
        ("adjudicate", "candidate", "scope", 2, 0, _recovery_adjudication()),
        ("repair", "candidate", "scope", 2, 0, None),
        ("regression", "after", final_scope, 2, 0, None),
        (
            "review",
            "after",
            final_scope,
            3,
            0,
            _recovery_review(
                "after",
                clean=True,
                scope=final_scope,
                reviewed_paths=("repo/code.py", "repo/added.py")
                if added_path
                else ("repo/code.py",),
            ),
        ),
        ("verification", "after", final_scope, 3, 0, None),
    )
    for index, (phase, candidate, scope, review_pass, code, record) in enumerate(phases):
        run_id, target = admit_phase(
            journal,
            repository,
            ordinal=index + 1,
            phase=phase,
            candidate=candidate,
            scope=scope,
            review_pass=review_pass,
            recoverable=index == 0,
        )
        settle(
            journal,
            repository,
            run_id,
            target,
            exit_code=code,
            record=record,
            worktree_files_after=("added.py",) if added_path and phase == "repair" else (),
        )
        if index + 1 < len(phases):
            marker = regression_recovery(journal.read_view().required_run_detail(run_id))
            journal.commit_agent_campaign_decision(
                decision(
                    journal,
                    decision_id=f"advance-{index}",
                    next_phase=phases[index + 1][0],
                    candidate=phases[index + 1][1],
                    scope=phases[index + 1][2],
                    recovery=marker,
                )
            )
    terminal = decision(
        journal,
        stop=AgentRepairStopReason.CONVERGED,
        decision_id="converged",
        candidate="after",
        scope=final_scope,
    )
    journal.commit_agent_campaign_decision(terminal)
    original = journal.read_view().required_run_detail("campaign")
    assert original.outcome.value != "SUCCESS"
    original_evidence = original.operations[0].evidence
    assert isinstance(original_evidence, FleetPlanOperationEvidence)
    assert original_evidence.plan is not None
    assert original_evidence.plan.steps[0].settlement.outcome == "FAILED"
    reopened = Journal(LocalDatabase(tmp_path / "state.db"))
    result = reopened.agent_campaign_result("campaign")
    assert result.converged and result.admitted_run_ids == (
        "campaign",
        "second",
        "phase-3",
        "phase-4",
        "phase-5",
        "phase-6",
        "phase-7",
    )
    assert reopened.read_view().required_run_detail("campaign") == original
    if added_path:
        assert result.campaign_journal is not None
        assert result.campaign_journal.phases[3].context.scope_identity == "scope"
        assert result.campaign_journal.phases[4].context.scope_identity == final_scope
        assert result.campaign_journal.terminal_decision is not None
        assert result.campaign_journal.terminal_decision.scope_identity == final_scope


@pytest.mark.parametrize("forgery", ("missing", "operation", "physical", "candidate", "scope"))
def test_regression_recovery_is_accepted_before_successor_effect_only_for_exact_result(
    tmp_path: Path, forgery: str
) -> None:
    journal, repository, _ = campaign(tmp_path, initial_phase="regression")
    run_id, target = admit_phase(journal, repository, phase="regression", recoverable=True)
    settle(journal, repository, run_id, target, exit_code=1)
    marker = regression_recovery(journal.read_view().required_run_detail(run_id))
    assert marker is not None
    if forgery == "missing":
        marker = None
    elif forgery == "physical":
        marker = marker.model_copy(
            update={
                "feedback": (
                    marker.feedback[0].model_copy(
                        update={"physical_execution_id": "foreign-process"}
                    ),
                )
            }
        )
    else:
        key = {
            "operation": "operation_id",
            "candidate": "candidate_identity",
            "scope": "scope_identity",
        }[forgery]
        marker = marker.model_copy(update={key: "foreign"})
    before = journal.read_view().revision
    with pytest.raises(JournalConflict, match="safely settled regression recovery"):
        journal.commit_agent_campaign_decision(
            decision(journal, next_phase="review", recovery=marker)
        )
    assert journal.read_view().revision == before
    assert journal.read_view().run_detail("second") is None


@pytest.mark.parametrize("forgery", ("missing", "text", "candidate", "run"))
def test_new_agent_phase_cannot_replace_committed_regression_feedback(
    tmp_path: Path, forgery: str
) -> None:
    journal, repository, _ = campaign(tmp_path, initial_phase="regression")
    run_id, target = admit_phase(journal, repository, phase="regression", recoverable=True)
    settle(journal, repository, run_id, target, exit_code=1)
    detail = journal.read_view().required_run_detail(run_id)
    marker = regression_recovery(detail)
    assert marker is not None
    feedback = regression_feedback_context(detail, marker)
    journal.commit_agent_campaign_decision(
        decision(journal, next_phase="review", next_step_ids=("review", "agent"), recovery=marker)
    )
    correct = json.dumps({"regression_feedback": feedback})
    changed = json.loads(correct)
    if forgery == "missing":
        changed = {}
    elif forgery == "text":
        changed["regression_feedback"]["feedback"][0]["text"] = "different failure"
    elif forgery == "candidate":
        changed["regression_feedback"]["candidate_identity"] = "foreign-candidate"
    else:
        changed["regression_feedback"]["fleet_run_id"] = "foreign-run"
    before = journal.read_view().revision
    with pytest.raises(JournalConflict, match="regression feedback"):
        admit_phase(
            journal,
            repository,
            ordinal=2,
            phase="review",
            review_pass=2,
            with_agent=True,
            agent_context=json.dumps(changed),
        )
    assert journal.read_view().revision == before
    assert journal.read_view().run_detail("second") is None
    admitted, _ = admit_phase(
        journal,
        repository,
        ordinal=2,
        phase="review",
        review_pass=2,
        with_agent=True,
        agent_context=correct,
    )
    assert admitted == "second"


def test_blocking_unstarted_selection_must_preserve_original_phase_membership(
    tmp_path: Path,
) -> None:
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
    committed = journal.agent_campaign("campaign").terminal_decision
    assert committed is not None
    assert committed.unstarted_selection == selected


def test_redaction_expansion_retains_bounded_idempotent_canonical_diagnostic(
    tmp_path: Path,
) -> None:
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
    assert retained is not None
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
def test_test_passed_publication_requires_its_accepted_native_test(
    tmp_path: Path, contradiction: str
) -> None:
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
    tmp_path: Path, prior_cleanup: Literal["VERIFIED", "UNVERIFIED"]
) -> None:
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
    assert receipt.campaign_journal is not None
    assert receipt.campaign_journal.terminal_decision is not None
    assert receipt.campaign_journal.terminal_decision.passed_test_step_id == "test"
    original_evidence = receipt.attempts[0].operations[0].evidence
    assert isinstance(original_evidence, FleetPlanOperationEvidence)
    assert original_evidence.plan is not None
    assert original_evidence.plan.steps[0].settlement.outcome == "FAILED"


def test_test_passed_reference_is_mandatory_only_for_that_decision(tmp_path: Path) -> None:
    journal, _, _ = campaign(tmp_path)
    with pytest.raises(ValueError, match="accepted native test reference"):
        decision(journal, stop=AgentRepairStopReason.TEST_PASSED)
    with pytest.raises(ValueError, match="accepted native test reference"):
        decision(journal, stop=AgentRepairStopReason.CANCELLED, passed_test_step_id="test")


def test_convergence_consumes_canonical_service_completion_before_publication(
    tmp_path: Path,
) -> None:
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
