"""B12C2: original Fleet execution lease owns each local physical intent cut.

This is not an issuer, independent journal, or physical containment witness.
The immutable authority is captured by FleetRunLifecycle, revalidated on the
actual worker's session-owned SQLite transaction immediately before allocation.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Callable
import sqlite3

from oh_goon.core.enforcer_lace_trust import ReceiptAuthenticator
from oh_goon.core.ports.local_lace_intent import LocalLaceIntentProof
from oh_goon.core.ports.local_lace_allocation import LocalLaceAllocationObservation
from oh_goon.runtime.enforcer_lace_journal import CausalRunJournal, CausalJournalError
from oh_goon.runtime.enforcer_lace_local_resource_intent import LocalLaceJournalReservation
from oh_goon.runtime.execution import ExecutionSession


@dataclass(frozen=True, slots=True)
class OriginalFleetLocalLaceReservation:
    session: ExecutionSession
    validate_authority_in: Callable[[sqlite3.Connection], None]
    authenticator: ReceiptAuthenticator
    run_id: str
    step_id: str
    attempt_number: int
    command_ordinal: int
    attempt_root: Path
    execution_environment_identity: str
    backend_capability_manifest_identity: str
    backend_configuration_identity: str
    # Fast-path routing hint only; the original signed Run remains authoritative.
    agent_workload: bool = False
    # Original Fleet event projection verifies the operational Step-attempt
    # lineage in this same session-owned cut. Never trust the Agent or Plan
    # to supply a digest for this comparison.
    agent_attempt_identity_in: Callable[[sqlite3.Connection], str] | None = None

    def reserve(self, request: object, backend: object) -> LocalLaceIntentProof:
        def original_cut(connection: sqlite3.Connection) -> LocalLaceIntentProof:
            self.validate_authority_in(connection)
            if self.session.cancel_requested_in(connection):
                raise CausalJournalError("Fleet cancellation fences local physical resources")
            journal = CausalRunJournal(connection)
            row = journal._row(self.run_id)
            # The causal journal's epoch and lease must belong to THIS execution
            # session, not merely be current for an unrelated worker.
            if row[2] != self.session.original_fleet_lease_token():
                raise CausalJournalError("causal control lease differs from original Fleet owner")
            # SKILL-3C2D1: inspect the exact attempted native tool effect at
            # the original Fleet-owned SQLite cut, before RESOURCE_INTENT and
            # before any cgroup/network/mount allocation. This never grants
            # Agent execution: original Agent RESOURCE_INTENT still refuses.
            if self.agent_workload:
                if self.agent_attempt_identity_in is None:
                    raise CausalJournalError("original Agent operational attempt authority missing")
                operational_attempt_identity = self.agent_attempt_identity_in(connection)
                grant_seq = journal.issue_original_agent_tool_launch_grant_in(
                    self.run_id, epoch=row[1], lease_identity=row[2],
                    auth=self.authenticator, step_id=self.step_id,
                    operational_attempt_identity=operational_attempt_identity,
                    operational_attempt_root=self.attempt_root,
                    attempt_number=self.attempt_number,
                    command_ordinal=self.command_ordinal,
                    argv=request.argv,
                    mounted_tool_names=tuple(tool.name for tool in request.attempt.host_tools),
                    actual_egress=request.attempt.egress.model_dump(mode="json"),
                    read_only_workspace=request.read_only_workspace is not None,
                )
                stage_mount = journal.original_agent_source_mount_for_grant_in(
                    self.run_id, auth=self.authenticator, grant_seq=grant_seq,
                    step_id=self.step_id,
                    operational_attempt_identity=operational_attempt_identity,
                    operational_attempt_root=self.attempt_root,
                    command_ordinal=self.command_ordinal,
                )
                if getattr(request, 'agent_source_mount', None) != stage_mount:
                    raise CausalJournalError('Agent native mount is not original Enforcer-granted stage')
            reservation = LocalLaceJournalReservation(
                journal=journal, authenticator=self.authenticator,
                run_id=self.run_id, step_id=self.step_id,
                attempt_number=self.attempt_number,
                command_ordinal=self.command_ordinal,
                attempt_root=self.attempt_root,
                control_epoch=row[1], lease_identity=self.session.original_fleet_lease_token(),
                execution_environment_identity=self.execution_environment_identity,
                backend_capability_manifest_identity=self.backend_capability_manifest_identity,
                backend_configuration_identity=self.backend_configuration_identity,
            )
            proof = reservation.reserve(request, backend)
            self.validate_authority_in(connection)
            return proof

        # This is the only write authority: a single original Fleet session mutation
        # handles path/lease/cancellation proof, causal append and commit together.
        return self.session.mutate(original_cut)

    def require_effect_authority(self, proof: LocalLaceIntentProof) -> None:
        """Repeat the original admitted trust/lease/intent fence before native effects.

        A prior reservation is not a durable effect permit after signer revocation.
        This is a bounded pre-effect check, NOT a kernel-atomic custody guarantee.
        """
        if (type(proof) is not LocalLaceIntentProof
                or (proof.run_id, proof.step_id, proof.command_ordinal)
                != (self.run_id, self.step_id, self.command_ordinal)
                or type(proof.run_seq) is not int or proof.run_seq < 2
                or type(proof.attempt_identity) is not str
                or not proof.attempt_identity.startswith("sha256:")
                or len(proof.attempt_identity) != 71
                or type(proof.mounts_identity) is not str
                or not proof.mounts_identity.startswith("sha256:")
                or len(proof.mounts_identity) != 71):
            raise CausalJournalError("physical effect proof differs from original reservation")

        def original_cut(connection: sqlite3.Connection) -> None:
            self.validate_authority_in(connection)
            if self.session.cancel_requested_in(connection):
                raise CausalJournalError("Fleet cancellation fences new physical effects")
            journal = CausalRunJournal(connection)
            row = journal._row(self.run_id)
            if row[2] != self.session.original_fleet_lease_token():
                raise CausalJournalError("physical effect lost original Fleet lease")
            events = journal.verify(self.run_id, auth=self.authenticator)
            claimed = [event for event in events
                       if event["run_seq"] == proof.run_seq and event["kind"] == "RESOURCE_INTENT"]
            if (len(claimed) != 1
                    or claimed[0]["payload"].get("step_id") != self.step_id
                    or claimed[0]["payload"].get("attempt_identity") != proof.attempt_identity
                    or claimed[0]["payload"].get("resources", {}).get("mounts") != proof.mounts_identity):
                raise CausalJournalError("physical effect lacks exact signed original intent")
            self.validate_authority_in(connection)

        self.session.mutate(original_cut)

    def observe(self, proof: LocalLaceIntentProof,
                observation: LocalLaceAllocationObservation) -> None:
        """Fenced original-session durable observation of allocated physical owners."""
        if (type(proof) is not LocalLaceIntentProof
                or proof.run_id != self.run_id or proof.step_id != self.step_id
                or proof.command_ordinal != self.command_ordinal
                or type(observation) is not LocalLaceAllocationObservation):
            raise CausalJournalError("physical observation does not bind trusted command intent")
        def original_cut(connection: sqlite3.Connection) -> None:
            self.validate_authority_in(connection)
            if self.session.cancel_requested_in(connection):
                raise CausalJournalError("Fleet cancellation fences physical allocation observation")
            journal = CausalRunJournal(connection)
            row = journal._row(self.run_id)
            if row[2] != self.session.original_fleet_lease_token():
                raise CausalJournalError("physical observation control lease differs from original Fleet owner")
            journal.publish_local_allocation_observation_in(
                self.run_id, epoch=row[1], lease_identity=row[2],
                auth=self.authenticator, step_id=self.step_id,
                attempt_identity=proof.attempt_identity, intent_run_seq=proof.run_seq,
                observation=asdict(observation),
            )
            self.validate_authority_in(connection)
        self.session.mutate(original_cut)


@dataclass(frozen=True, slots=True)
class OriginalFleetLocalLaceRecoveryOwner:
    """Recovery-only claim through the current original Fleet session owner.

    This does not perform native mutations; later reapers must obtain a fresh
    epoch-fenced transaction AND prove actual kernel resource absence.
    """
    session: ExecutionSession
    validate_authority_in: Callable[[sqlite3.Connection], None]
    authenticator: ReceiptAuthenticator
    run_id: str
    admitted_executor_generation: int | None = None
    admitted_issuer_key_id: str | None = None

    def claim(self, *, intent_run_seq: int) -> int:
        def original_cut(connection: sqlite3.Connection) -> int:
            self.validate_authority_in(connection)
            journal = CausalRunJournal(connection)
            row = journal._row(self.run_id)
            if row[2] != self.session.original_fleet_lease_token():
                raise CausalJournalError("recovery control lease differs from original Fleet owner")
            # Repeat the full original issuer/generation/intent proof in the
            # SAME write cut as the new claim. A worklist read is not a TOCTOU
            # substitute for admission at the effect-adjacent claim boundary.
            if self.admitted_executor_generation is not None or self.admitted_issuer_key_id is not None:
                from oh_goon.runtime.enforcer_lace_takeover_preflight import (
                    require_original_takeover_lineage_in,
                )
                require_original_takeover_lineage_in(
                    journal, run_id=self.run_id, intent_run_seq=intent_run_seq,
                    epoch=row[1], lease_identity=row[2], auth=self.authenticator,
                    admitted_executor_generation=self.admitted_executor_generation,
                    admitted_issuer_key_id=self.admitted_issuer_key_id,
                )
            result = journal.claim_local_resource_recovery_in(
                self.run_id, epoch=row[1], lease_identity=row[2],
                auth=self.authenticator, intent_run_seq=intent_run_seq,
            )
            self.validate_authority_in(connection)
            return result
        return (self.session.mutate_recovery if getattr(self.session, "recovery_only", False) else self.session.mutate)(original_cut)

    def rediscovery_subject(self, *, intent_run_seq: int):
        """Read the exact current-epoch recovery claim in the original Fleet write cut."""
        from oh_goon.runtime.enforcer_lace_orphan_subject import subject_in
        def original_cut(connection: sqlite3.Connection):
            self.validate_authority_in(connection)
            journal = CausalRunJournal(connection)
            row = journal._row(self.run_id)
            if row[2] != self.session.original_fleet_lease_token():
                raise CausalJournalError("orphan subject control lease differs from original Fleet owner")
            result = subject_in(journal, self.run_id, epoch=row[1], lease_identity=row[2],
                                auth=self.authenticator, intent_run_seq=intent_run_seq)
            self.validate_authority_in(connection)
            return result
        return (self.session.mutate_recovery if getattr(self.session, "recovery_only", False) else self.session.mutate)(original_cut)

    def record_rediscovery(self, subject, inventory) -> int:
        """Bind native read-only inventory to the still-current original recovery claim."""
        from oh_goon.runtime.enforcer_lace_orphan_subject import inventory_in
        def original_cut(connection: sqlite3.Connection) -> int:
            self.validate_authority_in(connection)
            journal = CausalRunJournal(connection)
            row = journal._row(self.run_id)
            if row[2] != self.session.original_fleet_lease_token():
                raise CausalJournalError("orphan observation control lease differs from original Fleet owner")
            result = inventory_in(journal, subject, inventory, epoch=row[1],
                                  lease_identity=row[2], auth=self.authenticator)
            self.validate_authority_in(connection)
            return result
        return (self.session.mutate_recovery if getattr(self.session, "recovery_only", False) else self.session.mutate)(original_cut)

    def prepare_cleanup(self, subject):
        """Durably bind the original observed inventory; no native effect permission."""
        from oh_goon.runtime.enforcer_lace_cleanup_attempt import prepare_cleanup_in
        def original_cut(connection: sqlite3.Connection):
            self.validate_authority_in(connection)
            if self.session.cancel_requested_in(connection):
                raise CausalJournalError("Fleet cancellation fences recovery preparation")
            journal = CausalRunJournal(connection)
            row = journal._row(self.run_id)
            if row[2] != self.session.original_fleet_lease_token():
                raise CausalJournalError("cleanup lease differs from original Fleet owner")
            if getattr(subject, "run_id", None) != self.run_id:
                raise CausalJournalError("foreign Run cleanup subject")
            ticket = prepare_cleanup_in(journal, subject, epoch=row[1],
                                        lease_identity=row[2], auth=self.authenticator)
            self.validate_authority_in(connection)
            return ticket
        return (self.session.mutate_recovery if getattr(self.session, "recovery_only", False) else self.session.mutate)(original_cut)

    def record_cleanup_blocker(self, ticket, *, failure_code: str) -> int:
        """A current original owner may record failure, never terminal absence."""
        from oh_goon.runtime.enforcer_lace_cleanup_attempt import abort_cleanup_in
        def original_cut(connection: sqlite3.Connection) -> int:
            self.validate_authority_in(connection)
            if self.session.cancel_requested_in(connection):
                raise CausalJournalError("Fleet cancellation fences recovery failure authority")
            journal = CausalRunJournal(connection)
            row = journal._row(self.run_id)
            if row[2] != self.session.original_fleet_lease_token():
                raise CausalJournalError("cleanup blocker lease differs from original Fleet owner")
            if getattr(ticket, "run_id", None) != self.run_id:
                raise CausalJournalError("foreign Run cleanup ticket")
            result = abort_cleanup_in(journal, ticket, epoch=row[1],
                                      lease_identity=row[2], auth=self.authenticator,
                                      failure_code=failure_code)
            self.validate_authority_in(connection)
            return result
        return (self.session.mutate_recovery if getattr(self.session, "recovery_only", False) else self.session.mutate)(original_cut)

    def execute_native_cleanup(self, ticket, native_action):
        """One-shot fenced native action. Caller MUST hold both native owner locks.

        C1 tickets are not executable by themselves. The original session first
        commits an irreversible STARTED fact; the action runs only inside a new
        original BEGIN IMMEDIATE after revalidating the live control authority.
        """
        from oh_goon.runtime.enforcer_lace_native_cleanup import (
            arm_native_cleanup_in, execute_native_cleanup_in,
        )

        def cut(action):
            def transaction(connection):
                self.validate_authority_in(connection)
                if self.session.cancel_requested_in(connection):
                    raise CausalJournalError("Fleet cancellation fences native recovery")
                journal = CausalRunJournal(connection)
                row = journal._row(self.run_id)
                if row[2] != self.session.original_fleet_lease_token():
                    raise CausalJournalError("native recovery original Fleet lease mismatch")
                if getattr(ticket, "run_id", None) != self.run_id:
                    raise CausalJournalError("foreign Run native cleanup ticket")
                value = action(journal, row[1], row[2])
                self.validate_authority_in(connection)
                return value
            return (self.session.mutate_recovery if getattr(self.session, "recovery_only", False) else self.session.mutate)(transaction)

        started = cut(lambda journal, epoch, lease: arm_native_cleanup_in(
            journal, ticket, epoch=epoch, lease_identity=lease,
            auth=self.authenticator))
        return cut(lambda journal, epoch, lease: execute_native_cleanup_in(
            journal, ticket, started_run_seq=started, epoch=epoch,
            lease_identity=lease, auth=self.authenticator,
            native_action=native_action))

    def reconcile_crash(self, *, intent_run_seq: int, native_observer):
        """Original recovery-coordinator entrypoint; no native action permitted.

        The native observer is a trusted read-only integration injected by the
        coordinator. Epoch/lease/subject are validated again after observation
        within the original Fleet-owned commit cut.
        """
        from oh_goon.runtime.enforcer_lace_crash_reconciliation import reconcile_crash_in
        subject = self.rediscovery_subject(intent_run_seq=intent_run_seq)
        evidence = native_observer(subject)

        def original_cut(connection: sqlite3.Connection) -> int:
            self.validate_authority_in(connection)
            if self.session.cancel_requested_in(connection):
                raise CausalJournalError("Fleet cancellation fences crash reconciliation")
            journal = CausalRunJournal(connection)
            row = journal._row(self.run_id)
            if row[2] != self.session.original_fleet_lease_token():
                raise CausalJournalError("crash reconciliation lease differs from original Fleet owner")
            result = reconcile_crash_in(
                journal, run_id=self.run_id, intent_run_seq=intent_run_seq,
                epoch=row[1], lease_identity=row[2], auth=self.authenticator,
                observation=evidence)
            self.validate_authority_in(connection)
            return result
        return (self.session.mutate_recovery if getattr(self.session, "recovery_only", False) else self.session.mutate)(original_cut)

    def sample_recovery_physical_unqualified(self, *, intent_run_seq: int, scope, mount_intent, proc_root: Path = Path("/proc")):
        """One original owner records the bounded, UNQUALIFIED shared observation.

        The original journal authenticates claim, allocation, cgroup identity,
        current process birth and network names. Mount targets remain a caller
        scope hint, not original admitted evidence; ALL facts are unqualified.
        The shared observer is called ONCE, outside both Fleet write cuts. The
        second existing cut appends a non-authorizing observation commitment
        ONLY after rechecking the exact predecessor head and control lease.
        Journal custody is not independent kernel-observer custody.
        """
        from oh_goon.core.ports.physical_observation import OriginalRecoveryPhysicalSample
        from oh_goon.integrations import linux_physical_observer
        from oh_goon.runtime.enforcer_lace_original_physical_sampling import (
            require_original_physical_sampling_in, require_original_sample_kernel_identity,
            original_sample_position,
        )
        if (type(self.admitted_executor_generation) is not int
                or type(self.admitted_issuer_key_id) is not str
                or not self.admitted_issuer_key_id):
            raise CausalJournalError("provisioned original Fleet sampling lineage required")

        def validate_cut(connection):
            self.validate_authority_in(connection)
            if self.session.cancel_requested_in(connection):
                raise CausalJournalError("Fleet cancellation fences kernel sampling")
            journal = CausalRunJournal(connection)
            row = journal._row(self.run_id)
            if row[2] != self.session.original_fleet_lease_token():
                raise CausalJournalError("kernel sampling original Fleet lease differs")
            result = require_original_physical_sampling_in(
                journal, run_id=self.run_id, intent_run_seq=intent_run_seq,
                epoch=row[1], lease_identity=row[2], auth=self.authenticator,
                executor_generation=self.admitted_executor_generation,
                issuer_key_id=self.admitted_issuer_key_id, scope=scope,
                mount_intent=mount_intent,
            )
            self.validate_authority_in(connection)
            return result

        mutate = self.session.mutate_recovery if getattr(self.session, "recovery_only", False) else self.session.mutate
        before = mutate(validate_cut)
        # No CLI and no parallel observer: one shared Linux reader only.
        facts = linux_physical_observer.observe_kernel(scope, proc_root=proc_root)
        require_original_sample_kernel_identity(before, facts)
        sample = OriginalRecoveryPhysicalSample(
            self.run_id, intent_run_seq, before.claim_run_seq, before.control_epoch,
            before.subject_identity, scope, facts, before.envelope,
            mount_intent_identity=before.mount_intent_identity,
        )
        def record_cut(connection):
            after = validate_cut(connection)
            if before != after:
                raise CausalJournalError("original Fleet physical scope/journal changed during scan")
            from oh_goon.core.enforcer_lace_trust import canonical_bytes, digest
            journal = CausalRunJournal(connection)
            events = journal.verify(self.run_id, auth=self.authenticator)
            sample_round, previous_sample_run_seq, cleanup_started_run_seq, cleanup_recorded_run_seq = (
                original_sample_position(
                    events, claim_run_seq=before.claim_run_seq,
                    intent_run_seq=intent_run_seq, subject_identity=before.subject_identity,
                )
            )
            scoped = {**asdict(scope), "mount_targets": list(scope.mount_targets)}
            payload = {
                "schema": "oh-goon/original-unqualified-physical-sample/v2",
                "sample_round": sample_round,
                "previous_sample_run_seq": previous_sample_run_seq,
                "cleanup_started_run_seq": cleanup_started_run_seq,
                "cleanup_recorded_run_seq": cleanup_recorded_run_seq,
                "run_id": self.run_id, "intent_run_seq": intent_run_seq,
                "claim_run_seq": before.claim_run_seq, "control_epoch": before.control_epoch,
                "subject_identity": before.subject_identity,
                "envelope_identity": before.envelope.identity,
                "mount_intent_identity": before.mount_intent_identity,
                "scope": scoped,
                "scope_identity": digest("oh-goon/b27/original-observation-scope/v1", canonical_bytes(scoped)),
                "snapshot_identity": facts.identity,
                "sample_identity": sample.causal_observation_identity,
                "observed": [
                    {"component": name, "state": fact.state, "detail": fact.detail}
                    for name, fact in facts.components
                ],
                "classification": sample.classification,
                "observer_custody": "UNVERIFIED",
                "coverage": "INCOMPLETE_NATIVE_RESOURCE_SCOPE",
                "effect_permission": "NONE",
                "physical_absence_proven": False,
                "quiescence_proven": False,
                "teardown_proven": False,
            }
            journal._append_locked(self.run_id, "RESOURCE_PHYSICAL_SAMPLE_RECORDED", payload)
            self.validate_authority_in(connection)
        mutate(record_cut)
        return sample

    def assess_recovery_physical_convergence_unqualified(self, *, intent_run_seq: int,
                                                        scope, mount_intent):
        """Read-only, claim-fenced P3 assessment. No observer, effects or gate PASS."""
        from oh_goon.runtime.enforcer_lace_original_physical_sampling import (
            require_original_physical_sampling_in,
            assess_original_physical_convergence_unqualified,
        )

        def original_cut(connection):
            self.validate_authority_in(connection)
            journal = CausalRunJournal(connection)
            row = journal._row(self.run_id)
            if row[2] != self.session.original_fleet_lease_token():
                raise CausalJournalError("physical convergence original Fleet lease differs")
            binding = require_original_physical_sampling_in(
                journal, run_id=self.run_id, intent_run_seq=intent_run_seq,
                epoch=row[1], lease_identity=row[2], auth=self.authenticator,
                executor_generation=self.admitted_executor_generation,
                issuer_key_id=self.admitted_issuer_key_id, scope=scope,
                mount_intent=mount_intent,
            )
            result = assess_original_physical_convergence_unqualified(
                journal.verify(self.run_id, auth=self.authenticator), binding=binding,
            )
            self.validate_authority_in(connection)
            return result

        mutate = self.session.mutate_recovery if getattr(self.session, "recovery_only", False) else self.session.mutate
        return mutate(original_cut)

    def pending_takeover_recovery(self):
        """Bounded original-session worklist; NEVER grants native cleanup."""
        from oh_goon.runtime.enforcer_lace_takeover_recovery import pending_takeover_in
        def original_cut(connection: sqlite3.Connection):
            self.validate_authority_in(connection)
            if self.session.cancel_requested_in(connection):
                raise CausalJournalError("Fleet cancellation fences takeover worklist")
            journal = CausalRunJournal(connection)
            row = journal._row(self.run_id)
            if row[2] != self.session.original_fleet_lease_token():
                raise CausalJournalError("takeover lease differs from original Fleet owner")
            obligations = pending_takeover_in(journal, run_id=self.run_id, epoch=row[1],
                lease_identity=row[2], auth=self.authenticator)
            if self.admitted_executor_generation is not None or self.admitted_issuer_key_id is not None:
                from oh_goon.runtime.enforcer_lace_takeover_preflight import (
                    require_original_takeover_lineage_in,
                )
                for obligation in obligations:
                    require_original_takeover_lineage_in(
                        journal, run_id=self.run_id, intent_run_seq=obligation.intent_run_seq,
                        epoch=row[1], lease_identity=row[2], auth=self.authenticator,
                        admitted_executor_generation=self.admitted_executor_generation,
                        admitted_issuer_key_id=self.admitted_issuer_key_id,
                    )
            self.validate_authority_in(connection)
            return obligations
        return (self.session.mutate_recovery if getattr(self.session, "recovery_only", False) else self.session.mutate)(original_cut)

    def resume_reconciled_takeover(self, *, intent_run_seq: int, native_observer):
        """One renewed claim and fresh read-only native reconciliation.

        Requires an already transferred, live original Fleet session. The prior
        reconciliation is verified BEFORE a new claim. The new claim commits
        separately, so a crash between claim and observation remains an explicit
        unresolved obligation, never permission to retry native effects.
        """
        if not callable(native_observer) or type(intent_run_seq) is not int:
            raise CausalJournalError("exact takeover intent and trusted native observer required")
        matches = [item for item in self.pending_takeover_recovery()
                   if item.intent_run_seq == intent_run_seq]
        if len(matches) != 1:
            raise CausalJournalError("no exact authenticated predecessor reconciliation")
        claim_run_seq = self.claim(intent_run_seq=intent_run_seq)
        # All journal callbacks verify the original current lease and epoch again.
        # No C1/C2 cleanup ticket is accepted or replayed here.
        reconciliation_run_seq = self.reconcile_crash(
            intent_run_seq=intent_run_seq, native_observer=native_observer)
        return (claim_run_seq, reconciliation_run_seq)
