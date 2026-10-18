from pathlib import Path

import pytest
from pydantic import ValidationError

from oh_goon.core.models.explanation import ReasonCode
from oh_goon.core.models.operation import (
    FailedAttentionOperationOutcome,
    FailedOperationOutcome,
    OperationReport,
)
from oh_goon.core.vocabulary import OperationAction
from tests.factories.operation_reports import make_operation_report


def base_report() -> dict[str, object]:
    return make_operation_report(Path("/tmp/repo"), OperationAction.INIT).model_dump(mode="python")


def test_failed_operation_outcomes_require_nonzero_exit_codes() -> None:
    with pytest.raises(ValidationError):
        FailedOperationOutcome(
            state="failed", returncode=0, reason_code=ReasonCode.OPERATION_FAILED
        )
    with pytest.raises(ValidationError):
        FailedAttentionOperationOutcome(
            state="failed_attention", returncode=0, reason_code=ReasonCode.OPERATION_FAILED
        )
    assert (
        FailedOperationOutcome(
            state="failed", returncode=-9, reason_code=ReasonCode.OPERATION_FAILED
        ).returncode
        == -9
    )


def test_operation_report_requires_durable_capability_provenance() -> None:
    payload = base_report()
    del payload["capability"]
    with pytest.raises(ValidationError, match="capability"):
        OperationReport.model_validate(payload)


def test_operation_report_forbids_unknown_top_level_fields() -> None:
    payload = base_report()
    payload["unexpected"] = True
    with pytest.raises(ValidationError):
        OperationReport.model_validate(payload)


def test_operation_report_forbids_unknown_nested_fields() -> None:
    payload = base_report()
    payload["evidence"]["readiness"]["unexpected"] = True  # type: ignore[index]
    with pytest.raises(ValidationError):
        OperationReport.model_validate(payload)


def test_operation_report_rejects_unknown_validation_state() -> None:
    payload = make_operation_report(Path("/tmp/repo"), OperationAction.VERIFY).model_dump(
        mode="python"
    )
    payload["evidence"]["validation"]["tests"] = "PASSSED"  # type: ignore[index]
    with pytest.raises(ValidationError):
        OperationReport.model_validate(payload)


def test_operation_report_rejects_unknown_remote_state() -> None:
    payload = base_report()
    payload["evidence"]["readiness"]["remote"]["state"] = "UPTODATE"  # type: ignore[index]
    with pytest.raises(ValidationError):
        OperationReport.model_validate(payload)


def test_operation_report_rejects_unknown_dependency_result_state() -> None:
    payload = make_operation_report(Path("/tmp/repo"), OperationAction.UPDATE).model_dump(
        mode="python"
    )
    payload["evidence"]["dependencies"]["state"] = "partially_updated"  # type: ignore[index]
    with pytest.raises(ValidationError):
        OperationReport.model_validate(payload)


def test_operation_report_rejects_repository_identity_mismatch() -> None:
    payload = base_report()
    payload["provenance"]["repository_id"] = "repo-wrong"  # type: ignore[index]
    with pytest.raises(ValidationError, match="repository_id"):
        OperationReport.model_validate(payload)


def test_operation_report_rejects_action_evidence_mismatch() -> None:
    payload = make_operation_report(Path("/tmp/repo"), OperationAction.INIT).model_dump(
        mode="python"
    )
    payload["evidence"] = make_operation_report(
        Path("/tmp/repo"), OperationAction.UPDATE
    ).model_dump(mode="python")["evidence"]
    with pytest.raises(ValidationError, match="requires READINESS evidence"):
        OperationReport.model_validate(payload)
