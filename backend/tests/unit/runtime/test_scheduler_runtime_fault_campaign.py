import pytest

from oh_goon.core.models.game_tape_runtime_authority import (
    RuntimeAuthorityEvidence,
    RuntimeAuthorityEvidenceKind,
)
from oh_goon.runtime.fault_campaign import (
    FaultCampaignDisposition,
    FaultCampaignResult,
    RuntimeFault,
    SchedulerRuntimeFaultCampaign,
)


def _kind(fault: RuntimeFault) -> RuntimeAuthorityEvidenceKind:
    return {
        RuntimeFault.STUBBORN_PROCESS_TREE: RuntimeAuthorityEvidenceKind.TERMINATION,
        RuntimeFault.OUT_OF_MEMORY: RuntimeAuthorityEvidenceKind.TERMINATION,
        RuntimeFault.PAGINATION_MUTATION: RuntimeAuthorityEvidenceKind.RECOVERY,
        RuntimeFault.SQLITE_FAILURE: RuntimeAuthorityEvidenceKind.RECOVERY,
        RuntimeFault.CONTROLLER_RESTART: RuntimeAuthorityEvidenceKind.RECOVERY,
        RuntimeFault.CONTROLLER_SUSPEND: RuntimeAuthorityEvidenceKind.RECOVERY,
        RuntimeFault.EXTERNAL_SIDE_EFFECT_UNCERTAIN: RuntimeAuthorityEvidenceKind.SIDE_EFFECT,
    }.get(fault, RuntimeAuthorityEvidenceKind.RACE)


def _evidence(
    fault: RuntimeFault, *, kind: RuntimeAuthorityEvidenceKind | None = None
) -> RuntimeAuthorityEvidence:
    return RuntimeAuthorityEvidence(
        kind=kind or _kind(fault),
        authority_identity=f"authority:{fault.value}",
        execution_id="exec-1",
        generation=3,
        disposition="PROVEN",
        reason=fault.value.lower(),
    )


def _result(
    fault: RuntimeFault,
    *,
    disposition: FaultCampaignDisposition = FaultCampaignDisposition.PASS,
    semantics: bool = True,
    evidence: bool = True,
) -> FaultCampaignResult:
    rows = (_evidence(fault),) if evidence else ()
    return FaultCampaignResult(
        fault=fault,
        execution_id="exec-1",
        generation=3,
        disposition=disposition,
        semantic_outcome="observed-native-or-deterministic-outcome",
        evidence=rows,
        semantics_proven=semantics,
        evidence_proven=evidence,
    )


def test_full_fault_campaign_requires_execution_semantics_and_evidence() -> None:
    campaign = SchedulerRuntimeFaultCampaign(
        results=tuple(_result(fault) for fault in RuntimeFault)
    )
    assert campaign.passed
    assert campaign.disposition is FaultCampaignDisposition.PASS


def test_campaign_preserves_fail_instead_of_collapsing_it_to_boolean() -> None:
    results = [_result(fault) for fault in RuntimeFault]
    results[0] = _result(results[0].fault, disposition=FaultCampaignDisposition.FAIL)
    campaign = SchedulerRuntimeFaultCampaign(results=tuple(results))
    assert campaign.disposition is FaultCampaignDisposition.FAIL
    assert not campaign.passed


def test_campaign_preserves_incomplete_when_required_proof_is_missing() -> None:
    results = [_result(fault) for fault in RuntimeFault]
    results[0] = _result(
        results[0].fault, disposition=FaultCampaignDisposition.INCOMPLETE, evidence=False
    )
    campaign = SchedulerRuntimeFaultCampaign(results=tuple(results))
    assert campaign.disposition is FaultCampaignDisposition.INCOMPLETE
    assert not campaign.passed


def test_fail_takes_precedence_over_incomplete_at_campaign_level() -> None:
    results = [_result(fault) for fault in RuntimeFault]
    results[0] = _result(
        results[0].fault, disposition=FaultCampaignDisposition.INCOMPLETE, evidence=False
    )
    results[1] = _result(results[1].fault, disposition=FaultCampaignDisposition.FAIL)
    assert (
        SchedulerRuntimeFaultCampaign(results=tuple(results)).disposition
        is FaultCampaignDisposition.FAIL
    )


def test_pass_cannot_be_claimed_without_both_proofs() -> None:
    with pytest.raises(ValueError, match="PASS requires"):
        _result(RuntimeFault.CLAIM_CANCEL_RACE, evidence=False)


def test_incomplete_cannot_hide_complete_proof() -> None:
    with pytest.raises(ValueError, match="INCOMPLETE cannot"):
        _result(RuntimeFault.CLAIM_CANCEL_RACE, disposition=FaultCampaignDisposition.INCOMPLETE)


def test_evidence_proven_requires_concrete_evidence() -> None:
    with pytest.raises(ValueError, match="requires concrete"):
        FaultCampaignResult(
            fault=RuntimeFault.SQLITE_FAILURE,
            execution_id="exec-1",
            generation=3,
            disposition=FaultCampaignDisposition.FAIL,
            semantic_outcome="storage-failed",
            evidence=(),
            semantics_proven=True,
            evidence_proven=True,
        )


def test_fault_evidence_must_include_relevant_authority_family() -> None:
    wrong = _evidence(
        RuntimeFault.STUBBORN_PROCESS_TREE, kind=RuntimeAuthorityEvidenceKind.SIDE_EFFECT
    )
    with pytest.raises(ValueError, match="authority kind relevant"):
        FaultCampaignResult(
            fault=RuntimeFault.STUBBORN_PROCESS_TREE,
            execution_id="exec-1",
            generation=3,
            disposition=FaultCampaignDisposition.FAIL,
            semantic_outcome="survivor",
            evidence=(wrong,),
            semantics_proven=True,
            evidence_proven=True,
        )


def test_campaign_fails_closed_when_required_fault_is_missing() -> None:
    with pytest.raises(ValueError, match="fault campaign is incomplete"):
        SchedulerRuntimeFaultCampaign(
            results=tuple(_result(fault) for fault in tuple(RuntimeFault)[:-1])
        )


def test_campaign_rejects_duplicate_fault_receipts() -> None:
    results = [_result(fault) for fault in RuntimeFault]
    results[-1] = results[0]
    with pytest.raises(ValueError, match="duplicate fault"):
        SchedulerRuntimeFaultCampaign(results=tuple(results))


def test_fault_evidence_cannot_cross_execution_generation() -> None:
    row = _evidence(RuntimeFault.COMPLETION_TIMEOUT_RACE).model_copy(update={"generation": 2})
    with pytest.raises(ValueError, match="another execution generation"):
        FaultCampaignResult(
            fault=RuntimeFault.COMPLETION_TIMEOUT_RACE,
            execution_id="exec-1",
            generation=3,
            disposition=FaultCampaignDisposition.FAIL,
            semantic_outcome="single-winner",
            evidence=(row,),
            semantics_proven=True,
            evidence_proven=True,
        )


def test_external_side_effect_fault_requires_side_effect_or_recovery_evidence() -> None:
    wrong = _evidence(
        RuntimeFault.EXTERNAL_SIDE_EFFECT_UNCERTAIN,
        kind=RuntimeAuthorityEvidenceKind.WATCHDOG,
    )
    with pytest.raises(ValueError, match="authority kind relevant"):
        FaultCampaignResult(
            fault=RuntimeFault.EXTERNAL_SIDE_EFFECT_UNCERTAIN,
            execution_id="exec-1",
            generation=3,
            disposition=FaultCampaignDisposition.FAIL,
            semantic_outcome="external-effect-outcome-uncertain",
            evidence=(wrong,),
            semantics_proven=True,
            evidence_proven=True,
        )


def test_external_side_effect_fault_accepts_bounded_side_effect_evidence() -> None:
    result = _result(RuntimeFault.EXTERNAL_SIDE_EFFECT_UNCERTAIN)
    assert result.evidence[0].kind is RuntimeAuthorityEvidenceKind.SIDE_EFFECT
    assert result.passed
