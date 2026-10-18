from __future__ import annotations

from types import SimpleNamespace

import pytest


pytest.importorskip("hashmarks")
pytestmark = pytest.mark.dependency_complete

from tests.hashmarks_test_support import (
    TEST_HASHMARKS_ARTIFACT_IDENTITY,
    TEST_HASHMARKS_PRODUCER_IMPLEMENTATION_IDENTITY,
)
from oh_goon.integrations.hashmarks.repository_intelligence import bind_hashmarks_repository_evidence
from oh_goon.integrations.hashmarks.work_translation import (
    HashmarksWorkTranslationError,
    describe_hashmarks_work,
)
from oh_goon.core.errors import ExecutionAssignmentStale
from oh_goon.runtime.execution_assignments import ExecutionAssignmentAuthority

SOURCE = "sha256:" + "1" * 64
ARTIFACT = TEST_HASHMARKS_ARTIFACT_IDENTITY
PROVENANCE = "sha256:" + "3" * 64


def _verified(payload: dict[str, object]):
    return bind_hashmarks_repository_evidence(
        payload,
        repository_generation_identity=SOURCE,
        producer_artifact_identity=ARTIFACT,
        input_provenance_identity=PROVENANCE,
    )


def test_verification_plan_translates_only_command_intent_and_exact_identity() -> None:
    evidence = _verified({
        "schema": "hashmarks.verification-plan.v1",
        "available": True,
        "argv": ["pytest", "backend/tests/unit"],
        "network_authority": "declared",
        "secret_grants": ["prod-token"],
        "publish_authority": True,
        "timeout_seconds": 99999,
    })
    work = describe_hashmarks_work(evidence)
    assert work.command_intent == ("pytest", "backend/tests/unit")
    assert work.ordered_members == ()
    assert work.work_identity.startswith("producer-neutral-work-description-")
    assert work.repository_generation_identity == SOURCE
    assert work.producer_artifact_identity == TEST_HASHMARKS_ARTIFACT_IDENTITY
    assert work.producer_implementation_identity == TEST_HASHMARKS_PRODUCER_IMPLEMENTATION_IDENTITY
    for forbidden in ("network_authority", "secret_grants", "publish_authority", "timeout_seconds"):
        assert not hasattr(work, forbidden)


def test_informative_context_or_decision_evidence_cannot_become_executable_work() -> None:
    evidence = _verified({"schema": "hashmarks.task-context-plan.v1", "task": "inspect"})
    with pytest.raises(HashmarksWorkTranslationError, match="informative only") as raised:
        describe_hashmarks_work(evidence)
    assert raised.value.failure_class == "input-selection-admission"


def test_unavailable_or_empty_verification_plan_fails_closed() -> None:
    for payload in (
        {"schema": "hashmarks.verification-plan.v1", "available": False, "argv": ["pytest"]},
        {"schema": "hashmarks.verification-plan.v1", "available": True, "argv": []},
    ):
        with pytest.raises(HashmarksWorkTranslationError):
            describe_hashmarks_work(_verified(payload))


def test_action_map_is_repository_evidence_not_execution_membership() -> None:
    evidence = _verified(
        {
            "schema": "hashmarks.task-action-map.v1",
            "ownership_authority": {
                "schema": "hashmarks.ownership-authority.v1",
                "authority": "repository-ownership-only",
                "consumer_action": "external",
                "authority_proof_identity": "sha256:" + "a" * 64,
            },
        }
    )
    with pytest.raises(HashmarksWorkTranslationError, match="informative only"):
        describe_hashmarks_work(evidence)


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


def test_assignment_authority_is_explicit_and_hashmarks_cannot_smuggle_it() -> None:
    work = describe_hashmarks_work(_verified({
        "schema": "hashmarks.verification-plan.v1",
        "available": True,
        "argv": ["pytest", "x"],
        "network_authority": "declared",
        "secret_grants": ["smuggled"],
        "publish_authority": True,
    }))
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
    assert work.work_identity in result.input_artifact_ids


def test_assignment_rejects_changed_checkpoint_or_repository_generation() -> None:
    work = describe_hashmarks_work(_verified({"schema": "hashmarks.verification-plan.v1", "available": True, "argv": ["pytest"]}))
    with pytest.raises(ExecutionAssignmentStale, match="checkpoint changed"):
        _FakeAuthority(SimpleNamespace(checkpoint_id="cp-2", source_sha256="1" * 64)).issue_described_work_candidate_mutation(
            SimpleNamespace(workspace_id="ws"),  # type: ignore[arg-type]
            work=work, attempt_id="a", expected_checkpoint_id="cp-1", wall_time_seconds=1,
        )
    with pytest.raises(ExecutionAssignmentStale, match="repository generation"):
        _FakeAuthority(SimpleNamespace(checkpoint_id="cp-1", source_sha256="9" * 64)).issue_described_work_candidate_mutation(
            SimpleNamespace(workspace_id="ws"),  # type: ignore[arg-type]
            work=work, attempt_id="a", expected_checkpoint_id="cp-1", wall_time_seconds=1,
        )


def test_hashmarks_described_work_for_generation_g_cannot_execute_generation_g_plus_1() -> None:
    work = describe_hashmarks_work(_verified({
        "schema": "hashmarks.verification-plan.v1",
        "available": True,
        "argv": ["pytest"],
    }))
    changed = _FakeAuthority(SimpleNamespace(checkpoint_id="cp-1", source_sha256="9" * 64))
    with pytest.raises(ExecutionAssignmentStale, match="repository generation"):
        changed.issue_described_work_candidate_mutation(
            SimpleNamespace(workspace_id="ws"),  # type: ignore[arg-type]
            work=work, attempt_id="attempt-g-plus-1", expected_checkpoint_id="cp-1", wall_time_seconds=1,
        )

