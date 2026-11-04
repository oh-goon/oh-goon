from __future__ import annotations

from types import SimpleNamespace

import pytest

from oh_goon.core.errors import ExecutionAssignmentStale
from oh_goon.core.models.described_work import ProducerNeutralWorkDescription
from oh_goon.runtime.execution_assignments import ExecutionAssignmentAuthority


def _work() -> ProducerNeutralWorkDescription:
    return ProducerNeutralWorkDescription(
        producer_name="test-observer",
        producer_version="1.0.0",
        producer_artifact_identity="sha256:" + "2" * 64,
        producer_implementation_identity="sha256:" + "3" * 64,
        producer_contract_schema="test.described-work.v1",
        repository_generation_identity="sha256:" + "1" * 64,
        input_provenance_identity="sha256:" + "4" * 64,
        evidence_payload_identity="sha256:" + "5" * 64,
        work_identity="sha256:" + "6" * 64,
        command_intent=("pytest", "tests/test_sample.py"),
    )


class _FakeAuthority(ExecutionAssignmentAuthority):
    def __init__(self, checkpoint):
        self.checkpoint = checkpoint
        self.kwargs = None

    def current_candidate_checkpoint(self, workspace, *, reprove=True):
        assert reprove is True
        return self.checkpoint

    def issue_candidate_mutation(self, workspace, **kwargs):
        self.kwargs = kwargs
        return SimpleNamespace(**kwargs)


def test_described_work_requires_explicit_execution_authority_and_retains_custody() -> None:
    work = _work()
    authority = _FakeAuthority(SimpleNamespace(checkpoint_id="cp-1", source_sha256="1" * 64))
    result = authority.issue_described_work_candidate_mutation(
        SimpleNamespace(workspace_id="ws-1"),  # type: ignore[arg-type]
        work=work,
        attempt_id="attempt-1",
        expected_checkpoint_id="cp-1",
        wall_time_seconds=45,
        network_authority="none",
        secret_grants=(),
        git_authority="read",
        tool_ids=("pytest",),
    )
    assert result.network_authority == "none"
    assert result.secret_grants == ()
    assert result.git_authority == "read"
    assert result.publish_authority is False
    assert result.wall_time_seconds == 45
    assert result.tool_ids == ("pytest",)
    assert result.input_artifact_ids == (
        work.producer_artifact_identity,
        work.producer_implementation_identity,
        work.input_provenance_identity,
        work.evidence_payload_identity,
        work.work_identity,
    )


@pytest.mark.parametrize(
    ("checkpoint_id", "source_sha256", "message"),
    [
        ("cp-2", "1" * 64, "checkpoint changed"),
        ("cp-1", "9" * 64, "repository generation"),
    ],
)
def test_described_work_rejects_changed_checkpoint_or_repository_generation(
    checkpoint_id: str, source_sha256: str, message: str
) -> None:
    authority = _FakeAuthority(
        SimpleNamespace(checkpoint_id=checkpoint_id, source_sha256=source_sha256)
    )
    with pytest.raises(ExecutionAssignmentStale, match=message):
        authority.issue_described_work_candidate_mutation(
            SimpleNamespace(workspace_id="ws"),  # type: ignore[arg-type]
            work=_work(),
            attempt_id="attempt-1",
            expected_checkpoint_id="cp-1",
            wall_time_seconds=1,
        )
    assert authority.kwargs is None
