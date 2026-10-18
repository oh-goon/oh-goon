from __future__ import annotations

from datetime import datetime, timezone

import pytest

from oh_goon.core.models.execution_assignment import (
    AttemptBudget,
    ExecutionAssignment,
    execution_assignment_fingerprint,
)
from oh_goon.core.models.recovery_lifecycle import RecoveryFailureEvidence
from oh_goon.core.models.workspace import CandidateCheckpoint
from oh_goon.core.recovery_lifecycle import RecoveryLifecycle


def _assignment() -> ExecutionAssignment:
    raw = dict(
        assignment_id="assignment-1",
        attempt_id="attempt-1",
        workspace_id="workspace-1",
        input_checkpoint_id="checkpoint-1",
        filesystem_authority="candidate_mutation",
        process_authority="isolated",
        network_authority="none",
        secret_grants=(),
        git_authority="none",
        publish_authority=False,
        tool_ids=(),
        delegation_limit=0,
        isolation_requirements=("private_filesystem",),
        input_artifact_ids=(),
        attempt_budget=AttemptBudget(wall_time_seconds=30),
        assignment_fingerprint="pending",
        created_at=datetime.now(timezone.utc),
    )
    unchecked = ExecutionAssignment.model_construct(**raw)
    raw["assignment_fingerprint"] = execution_assignment_fingerprint(unchecked)
    return ExecutionAssignment(**raw)


def _checkpoint() -> CandidateCheckpoint:
    return CandidateCheckpoint(
        workspace_id="workspace-1",
        checkpoint_id="checkpoint-1",
        source_sha256="a" * 64,
        byte_count=1,
        entry_count=1,
        created_at=datetime.now(timezone.utc),
    )


def _failure(kind: str, work: str = "sha256:" + "b" * 64) -> RecoveryFailureEvidence:
    return RecoveryFailureEvidence(
        failure_class=kind,
        reason_code="reason",
        work_identity=work,
        detail_identity="sha256:" + "c" * 64,
    )


@pytest.mark.parametrize("kind", ["environment", "timeout"])
def test_exact_environment_or_timeout_work_can_be_retried_within_budget(kind: str) -> None:
    work = "sha256:" + "b" * 64
    decision = RecoveryLifecycle().admit(
        previous_assignment=_assignment(),
        checkpoint=_checkpoint(),
        failure=_failure(kind),
        lineage_id="lineage-1",
        attempt_ordinal=2,
        max_attempts=3,
        exact_work_identity=work,
    )
    assert decision.disposition == "ADMITTED"
    assert decision.parent_assignment_id == "assignment-1"
    assert decision.attempt_ordinal == 2


@pytest.mark.parametrize("kind", ["product_assertion", "cancelled", "authority_integrity"])
def test_non_environment_failures_are_not_silently_reclassified_as_retryable(kind: str) -> None:
    decision = RecoveryLifecycle().admit(
        previous_assignment=_assignment(),
        checkpoint=_checkpoint(),
        failure=_failure(kind),
        lineage_id="lineage-1",
        attempt_ordinal=2,
        max_attempts=3,
        exact_work_identity="sha256:" + "b" * 64,
    )
    assert decision.disposition == "BLOCKED"


def test_retry_budget_is_hard() -> None:
    decision = RecoveryLifecycle().admit(
        previous_assignment=_assignment(),
        checkpoint=_checkpoint(),
        failure=_failure("environment"),
        lineage_id="lineage-1",
        attempt_ordinal=4,
        max_attempts=3,
        exact_work_identity="sha256:" + "b" * 64,
    )
    assert decision.disposition == "BLOCKED"
    assert decision.reason == "retry budget exhausted"


def test_recovery_cannot_cross_checkpoint_or_work_identity() -> None:
    with pytest.raises(ValueError, match="checkpoint"):
        RecoveryLifecycle().admit(
            previous_assignment=_assignment(),
            checkpoint=_checkpoint().model_copy(update={"checkpoint_id": "checkpoint-2"}),
            failure=_failure("environment"),
            lineage_id="lineage-1",
            attempt_ordinal=2,
            max_attempts=3,
            exact_work_identity="sha256:" + "b" * 64,
        )
    with pytest.raises(ValueError, match="immutable work"):
        RecoveryLifecycle().admit(
            previous_assignment=_assignment(),
            checkpoint=_checkpoint(),
            failure=_failure("environment"),
            lineage_id="lineage-1",
            attempt_ordinal=2,
            max_attempts=3,
            exact_work_identity="sha256:" + "d" * 64,
        )
