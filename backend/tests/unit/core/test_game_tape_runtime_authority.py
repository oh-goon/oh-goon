import pytest

from oh_goon.core.game_tape_runtime_authority import project_runtime_authority_evidence
from oh_goon.core.models.game_tape_runtime_authority import (
    RuntimeAuthorityEvidence,
    RuntimeAuthorityEvidenceKind,
)


def _row(
    index: int, kind: RuntimeAuthorityEvidenceKind = RuntimeAuthorityEvidenceKind.WATCHDOG
) -> RuntimeAuthorityEvidence:
    return RuntimeAuthorityEvidence(
        kind=kind,
        authority_identity=f"sha256:{index:064x}",
        execution_id="exec-1",
        generation=2,
        disposition="OBSERVED",
        reason=f"fact-{index}",
    )


def test_projection_is_diagnostic_and_cannot_be_execution_authority() -> None:
    receipt = project_runtime_authority_evidence([_row(1)])
    assert receipt.ui_execution_authority is False
    assert receipt.evidence[0].authority_identity.endswith("1")


def test_projection_covers_runtime_authority_fact_families_without_merging_them() -> None:
    kinds = tuple(RuntimeAuthorityEvidenceKind)
    receipt = project_runtime_authority_evidence(
        [_row(i + 1, kind) for i, kind in enumerate(kinds)]
    )
    assert tuple(row.kind for row in receipt.evidence) == kinds


def test_projection_is_bounded_and_reports_omission_instead_of_hiding_it() -> None:
    receipt = project_runtime_authority_evidence((_row(i) for i in range(300)))
    assert len(receipt.evidence) == 256
    assert receipt.observed_count == 300
    assert receipt.omitted_count == 44
    assert receipt.truncated is True
    assert receipt.evidence[0].reason == "fact-44"
    assert receipt.evidence[-1].reason == "fact-299"


def test_non_truncated_projection_reports_complete_window() -> None:
    receipt = project_runtime_authority_evidence([_row(1), _row(2)], limit=8)
    assert receipt.observed_count == 2
    assert receipt.omitted_count == 0
    assert receipt.truncated is False


def test_projection_limit_is_itself_bounded() -> None:
    with pytest.raises(ValueError, match="between 1 and 256"):
        project_runtime_authority_evidence([], limit=257)
