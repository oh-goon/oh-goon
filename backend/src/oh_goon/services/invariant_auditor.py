from __future__ import annotations

import sqlite3
from collections import Counter
from typing import TypedDict

from oh_goon.core.effect_attempts import LIVE_OWNED_EFFECT_STATUSES
from oh_goon.core.errors import (
    ExecutionMaterializationNotFound,
    ExecutionMaterializationStale,
    JournalCorruption,
)
from oh_goon.core.models.effect_attempt import (
    RepositoryEffectAttempt,
)
from oh_goon.core.models.recovery import (
    InspectLeaseAction,
    InspectLeaseFinding,
    InspectRepositoryEffectAction,
    InspectRepositoryEffectFinding,
    InvariantAudit,
    InvariantFinding,
    InvariantSeverity,
    PlainInvariantFinding,
    ReconcileEffectAttemptAction,
    ReconcileEffectAttemptFinding,
    RepairAction,
    RepairPlan,
    RepositoryEffectQuarantine,
    RestartRecoveryAction,
    RestartRecoveryFinding,
)
from oh_goon.core.models.run_admission import DirectFleetExecutionAdmission
from oh_goon.core.models.schedule_execution import ScheduleRunRecord
from oh_goon.core.models.target import FleetPlanTarget
from oh_goon.core.product_identity import validate_repository_attempt_operation_id
from oh_goon.core.vocabulary import EffectAttemptOwner
from oh_goon.runtime.database import LocalDatabase
from oh_goon.runtime.durable_revision import read_durable_revision
from oh_goon.runtime.effect_attempts import RepositoryEffectAttemptStore
from oh_goon.runtime.execution import ExecutionCoordinator, ExecutionLease
from oh_goon.runtime.execution_materializations import ExecutionMaterializationAuthority
from oh_goon.runtime.journal import Journal
from oh_goon.runtime.journal_view import JournalReadView
from oh_goon.runtime.safety_cut import (
    CandidateEffectQuarantine,
    ConsistentRuntimeSafetyCut,
    JournalCorruptRuntimeSafetyCut,
    RuntimeSafetyCut,
)
from oh_goon.runtime.schedule_executions import ScheduleExecutionStore
from oh_goon.services.effect_quarantine import effect_quarantines


class _SafetyCutFields(TypedDict):
    durable_revision: int
    active_leases: tuple[ExecutionLease, ...]
    retained_leases: tuple[ExecutionLease, ...]
    effect_attempts: tuple[RepositoryEffectAttempt, ...]
    active_schedules: tuple[ScheduleRunRecord, ...]


class SystemInvariantAuditor:
    """Classify one transactionally consistent durable safety cut."""

    def __init__(
        self,
        database: LocalDatabase,
        journal: Journal,
        execution: ExecutionCoordinator,
        effect_attempts: RepositoryEffectAttemptStore,
        schedules: ScheduleExecutionStore,
    ) -> None:
        self._database = database
        self._journal = journal
        self._execution = execution
        self._attempts = effect_attempts
        self._schedules = schedules

    def current_durable_revision(self) -> int:
        return self._database.durable_revision()

    def read_cut(self) -> RuntimeSafetyCut:
        with self._database.transaction() as connection:
            return self.read_cut_in(connection)

    def read_cut_in(self, connection: sqlite3.Connection) -> RuntimeSafetyCut:
        durable_revision = read_durable_revision(connection)
        common: _SafetyCutFields = dict(
            durable_revision=durable_revision,
            active_leases=self._execution.active_leases_in(connection),
            retained_leases=self._execution.retained_leases_in(connection),
            effect_attempts=tuple(self._attempts.all_in(connection)),
            active_schedules=tuple(self._schedules.active_in(connection)),
        )
        try:
            journal = self._journal.read_view_from_connection(connection)
        except JournalCorruption as exc:
            return JournalCorruptRuntimeSafetyCut(**common, journal_error=str(exc))
        return ConsistentRuntimeSafetyCut(
            **common,
            journal=journal,
            candidate_effect_quarantines=tuple(
                scope
                for attempt in common["effect_attempts"]
                if (scope := self._candidate_effect_quarantine_in(connection, journal, attempt))
                is not None
            ),
        )

    @staticmethod
    def _candidate_effect_quarantine_in(
        connection: sqlite3.Connection, journal: JournalReadView, attempt: RepositoryEffectAttempt
    ) -> CandidateEffectQuarantine | None:
        if (
            not attempt.quarantines_repository
            or attempt.fleet_run_id is None
            or attempt.execution_input_id is None
            or not isinstance(attempt.target, FleetPlanTarget)
        ):
            return None
        candidate = attempt.target.candidate_workspace
        if candidate is None or candidate.parallel_work_id is None:
            return None
        original = journal.run_detail(attempt.fleet_run_id)
        if (
            original is None
            or not isinstance(original.execution, DirectFleetExecutionAdmission)
            or original.execution.concurrency is None
            or original.run.target != attempt.target
            or candidate.fleet_run_id != attempt.fleet_run_id
            or candidate.workspace.repository_id != attempt.repository.repository_id
        ):
            return None
        try:
            materialization = ExecutionMaterializationAuthority.materialization_in(
                connection, attempt.execution_input_id
            )
        except (ExecutionMaterializationNotFound, ExecutionMaterializationStale, ValueError):
            return None
        if (
            materialization.writer_scope_kind != "native_namespace_scope"
            or materialization.attempt_id != attempt.operation_id
            or materialization.workspace_id != candidate.workspace.workspace_id
            or materialization.input_checkpoint_id != candidate.checkpoint.checkpoint_id
            or materialization.input_artifact_ids
            != (candidate.source.authority_id, attempt.target.execution_digest)
            or materialization.workspace_device != candidate.workspace_device
            or materialization.workspace_inode != candidate.workspace_inode
            or materialization.workspace_mode != candidate.workspace_mode
        ):
            return None
        return CandidateEffectQuarantine(
            operation_id=attempt.operation_id,
            parallel_work_id=candidate.parallel_work_id,
            workspace_id=candidate.workspace.workspace_id,
        )

    def audit(self, cut: RuntimeSafetyCut | None = None) -> InvariantAudit:
        cut = cut or self.read_cut()
        if isinstance(cut, JournalCorruptRuntimeSafetyCut):
            return InvariantAudit(
                durable_revision=cut.durable_revision,
                findings=(
                    PlainInvariantFinding(
                        finding_type="PLAIN",
                        code="JOURNAL_INTEGRITY_FAILED",
                        severity=InvariantSeverity.ERROR,
                        message=cut.journal_error,
                    ),
                ),
            )
        findings: list[InvariantFinding] = []
        findings.extend(self._execution_findings(cut))
        findings.extend(self._attempt_findings(cut))
        findings.extend(self._quarantine_findings(cut))
        findings.extend(self._schedule_findings(cut))
        return InvariantAudit(
            durable_revision=cut.durable_revision,
            journal_revision=cut.journal.revision,
            findings=tuple(findings),
        )

    def repair_plan(self, audit: InvariantAudit) -> RepairPlan:
        actions = [
            action
            for finding in audit.findings
            if (action := repair_action_for(finding)) is not None
        ]
        return RepairPlan(findings=audit.findings, actions=tuple(actions))

    @staticmethod
    def quarantines(cut: RuntimeSafetyCut) -> list[RepositoryEffectQuarantine]:
        return list(effect_quarantines(cut.effect_attempts))

    def _execution_findings(self, cut: ConsistentRuntimeSafetyCut) -> list[InvariantFinding]:
        findings: list[InvariantFinding] = []
        budgets: set[str] = set()
        concurrent = 0
        for lease in cut.retained_leases:
            if lease.kind != "FLEET":
                continue
            detail = cut.journal.run_detail(lease.fleet_run_id or "")
            if detail is None:
                findings.append(
                    InspectLeaseFinding(
                        finding_type="INSPECT_LEASE",
                        code="LEASE_WITHOUT_FLEET_RUN",
                        severity=InvariantSeverity.ERROR,
                        message=f"Execution lease references unknown FleetRun {lease.fleet_run_id}",
                        target=lease.fleet_run_id or lease.token,
                    )
                )
                continue
            admission = (
                detail.execution.concurrency
                if isinstance(detail.execution, DirectFleetExecutionAdmission)
                else None
            )
            if lease.concurrency_identity is not None:
                if admission is None or admission.identity != lease.concurrency_identity:
                    findings.append(
                        InspectLeaseFinding(
                            finding_type="INSPECT_LEASE",
                            code="CONCURRENT_RESERVATION_DIFFERS_FROM_ADMISSION",
                            severity=InvariantSeverity.ERROR,
                            message="Concurrent resource reservation differs from original Run admission",
                            target=detail.run.fleet_run_id,
                        )
                    )
                else:
                    concurrent += 1
                    budgets.add(admission.budget_identity)
            reserved = lease.custody is not None and lease.custody.phase == "RESOURCE_RESERVED"
            if reserved and not detail.run.status.terminal:
                findings.append(
                    InspectLeaseFinding(
                        finding_type="INSPECT_LEASE",
                        code="RESOURCE_RESERVATION_WITHOUT_TERMINAL_RUN",
                        severity=InvariantSeverity.ERROR,
                        message="Closing native resource occupancy requires a terminal original Run",
                        target=detail.run.fleet_run_id,
                    )
                )
            elif detail.run.status.terminal and lease in cut.active_leases:
                findings.append(
                    InspectLeaseFinding(
                        finding_type="INSPECT_LEASE",
                        code="TERMINAL_RUN_HAS_ACTIVE_LEASE",
                        severity=InvariantSeverity.ERROR,
                        message=f"Terminal FleetRun {detail.run.fleet_run_id} still owns an active execution lease",
                        target=detail.run.fleet_run_id,
                    )
                )
        if len(cut.retained_leases) > 1 and (
            concurrent != len(cut.retained_leases) or len(budgets) != 1
        ):
            findings.append(
                PlainInvariantFinding(
                    finding_type="PLAIN",
                    code="INCOMPATIBLE_CONCURRENT_EXECUTION_RESERVATIONS",
                    severity=InvariantSeverity.ERROR,
                    message="Multiple retained execution reservations lack one original concurrent budget",
                )
            )
        for active_run in cut.journal.active_runs():
            if not any(
                lease.kind == "FLEET" and lease.fleet_run_id == active_run.fleet_run_id
                for lease in cut.active_leases
            ):
                findings.append(
                    RestartRecoveryFinding(
                        finding_type="RESTART_RECOVERY",
                        code="ACTIVE_RUN_WITHOUT_LEASE",
                        severity=InvariantSeverity.ERROR,
                        message=f"Active FleetRun {active_run.fleet_run_id} has no active execution lease",
                        fleet_run_id=active_run.fleet_run_id,
                    )
                )
        for lease in cut.active_leases:
            if lease.kind != "DIRECT":
                continue
            owned = [
                attempt
                for attempt in cut.effect_attempts
                if _live_attempt_owned_by_lease(attempt, lease)
                and attempt.status in LIVE_OWNED_EFFECT_STATUSES
            ]
            if len(owned) != 1:
                findings.append(
                    InspectLeaseFinding(
                        finding_type="INSPECT_LEASE",
                        code="DIRECT_LEASE_WITHOUT_EXACT_EFFECT_ATTEMPT",
                        severity=InvariantSeverity.ERROR,
                        message=f"Direct execution lease must own exactly one CLAIMED/EFFECT_STARTED attempt; observed {len(owned)}",
                        target=lease.token,
                    )
                )
        return findings

    @staticmethod
    def _attempt_findings(cut: ConsistentRuntimeSafetyCut) -> list[InvariantFinding]:
        findings: list[InvariantFinding] = []
        live_fleet_repositories: Counter[tuple[str, str]] = Counter()
        for attempt in cut.effect_attempts:
            report = cut.journal.operation(attempt.operation_id)
            if report is not None and report.resource_settlement == "UNRESOLVED":
                findings.append(
                    InspectRepositoryEffectFinding(
                        finding_type="INSPECT_REPOSITORY_EFFECT",
                        code="ORIGINAL_RESOURCE_CLEANUP_UNRESOLVED",
                        severity=InvariantSeverity.ERROR,
                        message="Canonical operation retains unresolved original process/resource cleanup",
                        operation_id=attempt.operation_id,
                    )
                )
            if attempt.status in LIVE_OWNED_EFFECT_STATUSES and not any(
                _live_attempt_owned_by_lease(attempt, lease) for lease in cut.active_leases
            ):
                findings.append(
                    ReconcileEffectAttemptFinding(
                        finding_type="RECONCILE_EFFECT_ATTEMPT",
                        code="UNOWNED_EFFECT_ATTEMPT",
                        severity=InvariantSeverity.ERROR,
                        message=(
                            f"Effect attempt {attempt.operation_id} is {attempt.status.value} "
                            "without its live execution owner"
                        ),
                        operation_id=attempt.operation_id,
                    )
                )
            if attempt.source is not EffectAttemptOwner.FLEET or attempt.fleet_run_id is None:
                continue

            detail = cut.journal.run_detail(attempt.fleet_run_id)
            target = (
                next(
                    (
                        item
                        for item in detail.run.targets
                        if item.repository_id == attempt.repository.repository_id
                    ),
                    None,
                )
                if detail is not None
                else None
            )
            if detail is None or target is None or target != attempt.repository:
                findings.append(
                    InspectRepositoryEffectFinding(
                        finding_type="INSPECT_REPOSITORY_EFFECT",
                        code="FLEET_EFFECT_ATTEMPT_OUTSIDE_CAPTURE",
                        severity=InvariantSeverity.ERROR,
                        message=(
                            f"Fleet effect attempt {attempt.operation_id} does not match an exact "
                            f"captured target in FleetRun {attempt.fleet_run_id}"
                        ),
                        operation_id=attempt.operation_id,
                    )
                )
            else:
                try:
                    validate_repository_attempt_operation_id(
                        attempt.fleet_run_id,
                        attempt.repository.repository_id,
                        attempt.operation_id,
                    )
                except ValueError:
                    findings.append(
                        InspectRepositoryEffectFinding(
                            finding_type="INSPECT_REPOSITORY_EFFECT",
                            code="FLEET_EFFECT_ATTEMPT_NONCANONICAL_ID",
                            severity=InvariantSeverity.ERROR,
                            message=(
                                f"Fleet effect attempt {attempt.operation_id} does not carry the "
                                "canonical run/repository operation identity"
                            ),
                            operation_id=attempt.operation_id,
                        )
                    )
            if attempt.status in LIVE_OWNED_EFFECT_STATUSES:
                key = (attempt.fleet_run_id, attempt.repository.repository_id)
                live_fleet_repositories[key] += 1

        for (fleet_run_id, repository_id), count in live_fleet_repositories.items():
            if count > 1:
                findings.append(
                    PlainInvariantFinding(
                        finding_type="PLAIN",
                        code="MULTIPLE_LIVE_FLEET_REPOSITORY_ATTEMPTS",
                        severity=InvariantSeverity.ERROR,
                        message=(
                            f"FleetRun {fleet_run_id} has {count} live effect attempts for "
                            f"captured repository {repository_id}"
                        ),
                    )
                )
        return findings

    @staticmethod
    def _quarantine_findings(cut: ConsistentRuntimeSafetyCut) -> list[InvariantFinding]:
        candidate_workspaces = {
            scope.operation_id: scope.workspace_id for scope in cut.candidate_effect_quarantines
        }
        findings: list[InvariantFinding] = []
        for item in effect_quarantines(cut.effect_attempts):
            workspace_id = candidate_workspaces.get(item.operation_id)
            subject = (
                f"Candidate workspace {workspace_id}"
                if workspace_id is not None
                else f"Repository {item.repository.repository_id}"
            )
            findings.append(
                InspectRepositoryEffectFinding(
                    finding_type="INSPECT_REPOSITORY_EFFECT",
                    code="REPOSITORY_EFFECT_QUARANTINED",
                    severity=InvariantSeverity.WARNING,
                    message=(
                        f"{subject} remains quarantined by {item.origin.kind.value.lower()} "
                        f"effect {item.operation_id} ({item.status.value})"
                    ),
                    operation_id=item.operation_id,
                )
            )
        return findings

    @staticmethod
    def _schedule_findings(cut: RuntimeSafetyCut) -> list[InvariantFinding]:
        counts = Counter(item.schedule_id for item in cut.active_schedules)
        return [
            PlainInvariantFinding(
                finding_type="PLAIN",
                code="MULTIPLE_ACTIVE_SCHEDULE_EXECUTIONS",
                severity=InvariantSeverity.ERROR,
                message=f"Schedule {schedule_id} has {count} active executions",
            )
            for schedule_id, count in counts.items()
            if count > 1
        ]


def repair_action_for(finding: InvariantFinding) -> RepairAction | None:
    if isinstance(finding, ReconcileEffectAttemptFinding):
        return ReconcileEffectAttemptAction(
            action="reconcile-effect-attempt", target=finding.operation_id
        )
    if isinstance(finding, InspectRepositoryEffectFinding):
        return InspectRepositoryEffectAction(
            action="inspect-repository-effect", target=finding.operation_id
        )
    if isinstance(finding, RestartRecoveryFinding):
        return RestartRecoveryAction(action="restart-recovery", target=finding.fleet_run_id)
    if isinstance(finding, InspectLeaseFinding):
        return InspectLeaseAction(action="inspect-lease", target=finding.target)
    return None


def _live_attempt_owned_by_lease(
    attempt: RepositoryEffectAttempt,
    lease: ExecutionLease | None,
) -> bool:
    if lease is None:
        return False
    if attempt.source is EffectAttemptOwner.DIRECT:
        return lease.kind == "DIRECT"
    return lease.kind == "FLEET" and lease.fleet_run_id == attempt.fleet_run_id
