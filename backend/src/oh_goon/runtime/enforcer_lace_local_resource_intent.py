"""B12C/B12D local LACE reservation adapter; production uses Fleet session authority."""
from __future__ import annotations

from dataclasses import dataclass, asdict
from pathlib import Path
from typing import TYPE_CHECKING

from oh_goon.core.enforcer_lace_trust import ReceiptAuthenticator
from oh_goon.core.ports.local_lace_intent import LocalLaceIntentProof
from oh_goon.core.ports.local_lace_allocation import LocalLaceAllocationObservation
from oh_goon.integrations.fleet_plan_local_lace_intent import _identity, local_resource_identities
from oh_goon.runtime.enforcer_lace_journal import CausalJournalError, CausalRunJournal

if TYPE_CHECKING:
    from oh_goon.integrations.fleet_plan_process_isolation import IsolatedProcessRequest


@dataclass(frozen=True, slots=True)
class LocalLaceJournalReservation:
    """Single-use Step/attempt/command cut owned by authenticated causal Fleet Journal.

    References to the journal/key are passed from the original authority owner;
    they are never sourced from environment variables or the workload. This
    component is a seam, not an alternate admission writer or live composition.
    """

    journal: CausalRunJournal
    authenticator: ReceiptAuthenticator
    run_id: str
    step_id: str
    attempt_number: int
    command_ordinal: int
    attempt_root: Path
    control_epoch: int
    lease_identity: str
    execution_environment_identity: str
    backend_capability_manifest_identity: str
    backend_configuration_identity: str

    def reserve(self, request: IsolatedProcessRequest, backend: object) -> LocalLaceIntentProof:
        if (type(request.fleet_run_id) is not str or request.fleet_run_id != self.run_id
                or type(request.step_id) is not str or request.step_id != self.step_id
                or type(self.attempt_number) is not int
                or request.attempt.attempt_number != self.attempt_number
                or type(self.command_ordinal) is not int or self.command_ordinal < 1
                or request.command_ordinal != self.command_ordinal
                or request.attempt.paths.root != self.attempt_root
                or getattr(getattr(backend, "capabilities", None), "backend_id", None) != "bubblewrap"
                or backend.configuration_identity() != self.backend_configuration_identity):
            raise CausalJournalError("local LACE request differs from original Run/Step/attempt/backend")
        # Attempt identity is bound to physical attempt lineage and selected backend.
        # Every physical command gets a distinct ordinal within the attempt.
        # The authoritative journal fences duplicates, retries and replay.
        attempt_identity = _identity("physical-attempt", {
            "run_id": self.run_id, "step_id": self.step_id,
            "attempt_number": self.attempt_number,
            "command_ordinal": self.command_ordinal,
            "attempt_root": str(self.attempt_root),
            "backend_configuration_identity": self.backend_configuration_identity,
        })
        publish = (self.journal.publish_local_resource_intent_in
                   if self.journal.connection.in_transaction
                   else self.journal.publish_local_resource_intent)
        original_resources = local_resource_identities(request)
        seq = publish(
            self.run_id, epoch=self.control_epoch,
            lease_identity=self.lease_identity, auth=self.authenticator,
            step_id=self.step_id, attempt_identity=attempt_identity,
            execution_environment_identity=self.execution_environment_identity,
            backend_capability_manifest_identity=self.backend_capability_manifest_identity,
            resources=original_resources,
        )

        # Reverify the original admission and the exact committed event cut.
        events = self.journal.verify(self.run_id, auth=self.authenticator)
        if (seq != len(events) or events[-1]["kind"] != "RESOURCE_INTENT"
                or events[-1]["payload"].get("resources") != original_resources):
            raise CausalJournalError("local LACE reserved resources differ from original signed intent")
        return LocalLaceIntentProof(
            run_id=self.run_id, step_id=self.step_id,
            command_ordinal=self.command_ordinal, run_seq=seq,
            attempt_identity=attempt_identity,
            mounts_identity=original_resources["mounts"],
        )

    def require_effect_authority(self, proof: LocalLaceIntentProof) -> None:
        """Development journal-only adapter: verify original intent, no new authority.

        Production strict mode must use the Fleet session owner's additional
        signer/lease/cancellation fence, not this standalone journal adapter.
        """
        if (type(proof) is not LocalLaceIntentProof
                or (proof.run_id, proof.step_id, proof.command_ordinal)
                != (self.run_id, self.step_id, self.command_ordinal)
                or proof.attempt_identity != _identity("physical-attempt", {
                    "run_id": self.run_id, "step_id": self.step_id,
                    "attempt_number": self.attempt_number,
                    "command_ordinal": self.command_ordinal,
                    "attempt_root": str(self.attempt_root),
                    "backend_configuration_identity": self.backend_configuration_identity,
                })):
            raise CausalJournalError("development effect lacks original physical attempt")
        events = self.journal.verify(self.run_id, auth=self.authenticator)
        matches = [e for e in events if e["run_seq"] == proof.run_seq
                   and e["kind"] == "RESOURCE_INTENT"]
        if (len(matches) != 1
                or matches[0]["payload"].get("attempt_identity") != proof.attempt_identity
                or matches[0]["payload"].get("resources", {}).get("mounts") != proof.mounts_identity):
            raise CausalJournalError("development effect intent differs from original signed event")

    def observe(self, proof: LocalLaceIntentProof,
                observation: LocalLaceAllocationObservation) -> None:
        """Development reservation adapter; production uses original Fleet session.

        The original Fleet owner never invokes this standalone transaction
        adapter: it validates lease/cancellation through its session instead.
        """
        if (type(proof) is not LocalLaceIntentProof
                or proof.run_id != self.run_id or proof.step_id != self.step_id
                or proof.command_ordinal != self.command_ordinal
                or type(observation) is not LocalLaceAllocationObservation):
            raise CausalJournalError("local LACE allocation does not bind reserved command")
        def commit() -> None:
            self.journal.publish_local_allocation_observation_in(
                self.run_id, epoch=self.control_epoch, lease_identity=self.lease_identity,
                auth=self.authenticator, step_id=self.step_id,
                attempt_identity=proof.attempt_identity, intent_run_seq=proof.run_seq,
                observation=asdict(observation),
            )
        if self.journal.connection.in_transaction:
            commit()
        else:
            self.journal._transaction(commit)
