from __future__ import annotations

from enum import StrEnum

from pydantic import Field, model_validator

from oh_goon.core.models.base import ImmutableModel
from oh_goon.core.models.game_tape_runtime_authority import RuntimeAuthorityEvidence, RuntimeAuthorityEvidenceKind


class RuntimeFault(StrEnum):
    CLAIM_CANCEL_RACE = "CLAIM_CANCEL_RACE"
    COMPLETION_TIMEOUT_RACE = "COMPLETION_TIMEOUT_RACE"
    CHECKPOINT_LEASE_EXPIRY = "CHECKPOINT_LEASE_EXPIRY"
    CONTROLLER_RESTART = "CONTROLLER_RESTART"
    CONTROLLER_SUSPEND = "CONTROLLER_SUSPEND"
    SQLITE_FAILURE = "SQLITE_FAILURE"
    STUBBORN_PROCESS_TREE = "STUBBORN_PROCESS_TREE"
    OUT_OF_MEMORY = "OUT_OF_MEMORY"
    PAGINATION_MUTATION = "PAGINATION_MUTATION"
    OLD_MESSAGE_REDELIVERY = "OLD_MESSAGE_REDELIVERY"
    EXTERNAL_SIDE_EFFECT_UNCERTAIN = "EXTERNAL_SIDE_EFFECT_UNCERTAIN"


class FaultCampaignDisposition(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    INCOMPLETE = "INCOMPLETE"


_EXPECTED_EVIDENCE_KINDS: dict[RuntimeFault, frozenset[RuntimeAuthorityEvidenceKind]] = {
    RuntimeFault.CLAIM_CANCEL_RACE: frozenset({RuntimeAuthorityEvidenceKind.RACE}),
    RuntimeFault.COMPLETION_TIMEOUT_RACE: frozenset({RuntimeAuthorityEvidenceKind.RACE, RuntimeAuthorityEvidenceKind.WATCHDOG}),
    RuntimeFault.CHECKPOINT_LEASE_EXPIRY: frozenset({RuntimeAuthorityEvidenceKind.RACE, RuntimeAuthorityEvidenceKind.RECOVERY}),
    RuntimeFault.CONTROLLER_RESTART: frozenset({RuntimeAuthorityEvidenceKind.RECOVERY}),
    RuntimeFault.CONTROLLER_SUSPEND: frozenset({RuntimeAuthorityEvidenceKind.RECOVERY}),
    RuntimeFault.SQLITE_FAILURE: frozenset({RuntimeAuthorityEvidenceKind.RECOVERY}),
    RuntimeFault.STUBBORN_PROCESS_TREE: frozenset({RuntimeAuthorityEvidenceKind.TERMINATION}),
    RuntimeFault.OUT_OF_MEMORY: frozenset({RuntimeAuthorityEvidenceKind.TERMINATION}),
    RuntimeFault.PAGINATION_MUTATION: frozenset({RuntimeAuthorityEvidenceKind.RECOVERY}),
    RuntimeFault.OLD_MESSAGE_REDELIVERY: frozenset({RuntimeAuthorityEvidenceKind.RACE, RuntimeAuthorityEvidenceKind.RECOVERY}),
    RuntimeFault.EXTERNAL_SIDE_EFFECT_UNCERTAIN: frozenset({RuntimeAuthorityEvidenceKind.SIDE_EFFECT, RuntimeAuthorityEvidenceKind.RECOVERY}),
}


class FaultCampaignResult(ImmutableModel):
    """One observed fault result; FAIL and INCOMPLETE are first-class evidence."""

    fault: RuntimeFault
    execution_id: str = Field(min_length=1)
    generation: int = Field(ge=1)
    disposition: FaultCampaignDisposition
    semantic_outcome: str = Field(min_length=1, max_length=120)
    evidence: tuple[RuntimeAuthorityEvidence, ...] = Field(default_factory=tuple, max_length=32)
    semantics_proven: bool
    evidence_proven: bool

    @model_validator(mode="after")
    def _validate_observed_result(self) -> "FaultCampaignResult":
        if any(row.execution_id != self.execution_id or row.generation != self.generation for row in self.evidence):
            raise ValueError("fault evidence belongs to another execution generation")
        if self.evidence_proven and not self.evidence:
            raise ValueError("evidence_proven requires concrete bounded authority evidence")
        if self.evidence_proven:
            expected = _EXPECTED_EVIDENCE_KINDS[self.fault]
            if not any(row.kind in expected for row in self.evidence):
                raise ValueError("fault evidence does not contain an authority kind relevant to this fault")
        proofs_complete = self.semantics_proven and self.evidence_proven
        if self.disposition is FaultCampaignDisposition.PASS and not proofs_complete:
            raise ValueError("PASS requires both semantic and evidence proof")
        if self.disposition is FaultCampaignDisposition.INCOMPLETE and proofs_complete:
            raise ValueError("INCOMPLETE cannot carry complete proof")
        return self

    @property
    def passed(self) -> bool:
        return self.disposition is FaultCampaignDisposition.PASS


class SchedulerRuntimeFaultCampaign(ImmutableModel):
    """Closed-world receipt preserving every observed PASS/FAIL/INCOMPLETE result."""

    results: tuple[FaultCampaignResult, ...]

    @model_validator(mode="after")
    def _complete_unique_campaign(self) -> "SchedulerRuntimeFaultCampaign":
        observed = [result.fault for result in self.results]
        if len(observed) != len(set(observed)):
            raise ValueError("fault campaign contains duplicate fault results")
        missing = set(RuntimeFault) - set(observed)
        if missing:
            names = ", ".join(sorted(fault.value for fault in missing))
            raise ValueError(f"fault campaign is incomplete: {names}")
        return self

    @property
    def disposition(self) -> FaultCampaignDisposition:
        if any(result.disposition is FaultCampaignDisposition.FAIL for result in self.results):
            return FaultCampaignDisposition.FAIL
        if any(result.disposition is FaultCampaignDisposition.INCOMPLETE for result in self.results):
            return FaultCampaignDisposition.INCOMPLETE
        return FaultCampaignDisposition.PASS

    @property
    def passed(self) -> bool:
        return self.disposition is FaultCampaignDisposition.PASS
