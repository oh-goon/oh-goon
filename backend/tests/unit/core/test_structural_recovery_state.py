from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import TypeAdapter, ValidationError

from oh_goon.core.models.effect_identity import RepositoryEffectIdentity
from oh_goon.core.models.effect_origin import DirectEffectOrigin
from oh_goon.core.models.reconciliation import (
    ConfirmedNotAppliedRepositoryReconciliation,
    PartiallyAppliedRepositoryReconciliation,
    RepositoryEffectAcknowledgementEvidence,
    RepositoryEffectReconciliationEvidence,
    RepositoryReconciliation,
)
from oh_goon.core.models.recovery import RepositoryEffectQuarantine
from oh_goon.core.models.repository import RepositoryIdentity
from oh_goon.core.vocabulary import (
    EffectAttemptOwner,
    EffectAttemptStatus,
    OperationAction,
    ReconciliationOutcome,
)
from tests.factories.capabilities import capability_snapshot
from tests.factories.repository_state import baseline_for


def _repository(tmp_path: Path) -> Path:
    repository = tmp_path / "repo"
    repository.mkdir()
    (repository / "pyproject.toml").write_text(
        "[project]\nname='fixture'\n",
        encoding="utf-8",
    )
    return repository


def _facts(tmp_path: Path):
    repository = _repository(tmp_path)
    state = baseline_for(repository)
    capability = capability_snapshot(OperationAction.UPDATE)
    origin = DirectEffectOrigin(kind=EffectAttemptOwner.DIRECT)
    return repository, state, capability, origin


def test_reconciliation_requires_current_evidence_and_rejects_derived_report_boolean(
    tmp_path: Path,
) -> None:
    _, state, _, _ = _facts(tmp_path)
    adapter = TypeAdapter(RepositoryReconciliation)
    base = {
        "outcome": ReconciliationOutcome.CONFIRMED_NOT_APPLIED,
        "reason": "repository is unchanged",
        "baseline": state,
    }

    with pytest.raises(ValidationError):
        adapter.validate_python(base)
    with pytest.raises(ValidationError):
        adapter.validate_python({**base, "current": state, "journal_report_present": False})


def test_recovery_evidence_uses_one_structural_origin(
    tmp_path: Path,
) -> None:
    repository, state, capability, origin = _facts(tmp_path)
    reconciliation = ConfirmedNotAppliedRepositoryReconciliation(
        outcome=ReconciliationOutcome.CONFIRMED_NOT_APPLIED,
        reason="repository is unchanged",
        baseline=state,
        current=state,
    )
    evidence = RepositoryEffectReconciliationEvidence(
        effect=RepositoryEffectIdentity(
            operation_id="op-1",
            repository=RepositoryIdentity(path=repository),
            capability=capability,
            origin=origin,
        ),
        reconciliation=reconciliation,
    )

    dumped = evidence.model_dump(mode="json")
    assert dumped["effect"]["origin"] == {"kind": "DIRECT"}
    assert dumped["effect"]["repository"]["path"] == str(repository.resolve())
    assert "operation_id" not in dumped
    assert "repository" not in dumped
    assert "capability" not in dumped
    assert "origin" not in dumped


def test_acknowledgement_cannot_carry_retry_safe_reconciliation(
    tmp_path: Path,
) -> None:
    repository, state, capability, origin = _facts(tmp_path)
    retry_safe = ConfirmedNotAppliedRepositoryReconciliation(
        outcome=ReconciliationOutcome.CONFIRMED_NOT_APPLIED,
        reason="repository is unchanged",
        baseline=state,
        current=state,
    )

    with pytest.raises(ValidationError):
        RepositoryEffectAcknowledgementEvidence(
            effect=RepositoryEffectIdentity(
                operation_id="op-1",
                repository=RepositoryIdentity(path=repository),
                capability=capability,
                origin=origin,
            ),
            reconciliation=retry_safe,
            note="nothing to acknowledge",
        )


def test_quarantine_variants_require_state_specific_evidence(tmp_path: Path) -> None:
    repository, state, capability, origin = _facts(tmp_path)
    partial = PartiallyAppliedRepositoryReconciliation(
        outcome=ReconciliationOutcome.PARTIALLY_APPLIED,
        reason="repository changed without canonical report",
        baseline=state,
        current=state,
    )
    adapter = TypeAdapter(RepositoryEffectQuarantine)
    common = {
        "operation_id": "op-1",
        "repository": RepositoryIdentity(path=repository),
        "capability": capability,
        "baseline": state,
        "origin": origin,
    }

    with pytest.raises(ValidationError):
        adapter.validate_python(
            {
                **common,
                "status": EffectAttemptStatus.OUTCOME_UNKNOWN,
                "note": "unknown",
                "reconciliation": partial,
            }
        )
    with pytest.raises(ValidationError):
        adapter.validate_python(
            {
                **common,
                "status": EffectAttemptStatus.PARTIALLY_APPLIED,
            }
        )
