from pathlib import Path

import pytest
from pydantic import ValidationError

from oh_goon.core.models.operation import (
    NoDependencyUpdates,
    OperationReport,
    UpdateOperationEvidence,
)
from oh_goon.core.vocabulary import OperationAction, PolicySource
from tests.factories.operation_reports import make_operation_report
from tests.factories.policy import make_policy_snapshot


def test_policy_snapshot_id_is_deterministic() -> None:
    first = make_policy_snapshot()
    second = make_policy_snapshot()
    assert first.policy_id == second.policy_id


def test_policy_snapshot_rejects_explicit_derived_id() -> None:
    from oh_goon.core.models.policy import PolicySnapshot

    valid = make_policy_snapshot()
    with pytest.raises(ValidationError):
        PolicySnapshot(
            policy_id="policy-wrong",
            captured_at=valid.captured_at,
            values=valid.values,
            sources=valid.sources,
        )


def test_policy_sources_capture_only_oh_goon_owned_policy() -> None:
    snapshot = make_policy_snapshot()
    assert snapshot.sources.workspace_scope is PolicySource.DERIVED
    values = snapshot.values.model_dump(mode="json")
    assert "resolution" not in values
    assert "prerelease" not in values
    assert "index_policy" not in values
    assert "uv_required_version" not in values


def _payload_with_policy(tmp_path: Path) -> tuple[dict, object]:
    report = make_operation_report(tmp_path / "repo", OperationAction.UPDATE)
    policy = make_policy_snapshot()
    payload = report.model_dump(mode="json", exclude_computed_fields=True)
    payload["evidence"] = UpdateOperationEvidence(
        kind="UPDATE", policy=policy, dependencies=NoDependencyUpdates(state="no_updates")
    ).model_dump(mode="json", exclude_computed_fields=True)
    payload["provenance"]["started_at"] = policy.captured_at.isoformat()
    payload["provenance"]["finished_at"] = policy.captured_at.isoformat()
    return payload, policy


def test_operation_report_exposes_policy_from_evidence(tmp_path: Path) -> None:
    payload, policy = _payload_with_policy(tmp_path)
    validated = OperationReport.model_validate(payload)
    assert isinstance(validated.evidence, UpdateOperationEvidence)
    assert validated.evidence.policy is not None
    assert validated.policy == validated.evidence.policy == policy
