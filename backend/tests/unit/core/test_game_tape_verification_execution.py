import pytest

pytest.importorskip("hashmarks")
pytestmark = pytest.mark.dependency_complete

from tests.hashmarks_test_support import (
    TEST_HASHMARKS_PRODUCER_IMPLEMENTATION_IDENTITY,
)
from oh_goon.core.game_tape_projection import _verification_execution_boundary
from oh_goon.core.models.fleet_plan_evidence import FleetPlanResult
from oh_goon.core.models.shard_execution import CanonicalShardExecutionEvidence, CanonicalShardResultEvidence


def test_game_tape_projects_hashmarks_verification_membership_and_unproven_owner_state() -> None:
    shard_execution = CanonicalShardExecutionEvidence(
        plan_identity="sha256:" + "1" * 64,
        repository_generation_identity="sha256:" + "2" * 64,
        execution_context_identity="sha256:" + "3" * 64,
        execution_policy_identity="sha256:" + "4" * 64,
        producer_contract_schema="hashmarks.verification-selection.v1",
        producer_version="0.14.0",
        producer_artifact_identity="sha256:" + "4" * 64,
        producer_implementation_identity="sha256:" + "d" * 64,
        producer_contract_identity="sha256:" + "5" * 64,
        repository_content_identity="sha256:" + "6" * 64 + ":2",
        verification_selection_evidence_identity="sha256:" + "7" * 64,
        producer_repository_generation=17,
        producer_identity_generation=5,
        producer_evidence_hashes=(("ownership", "sha256:" + "f" * 64),),
        selected_membership_identity="sha256:" + "8" * 64,
        verification_revision_identity="sha256:" + "9" * 64,
        selected_members=("tests/test_a.py::test_a", "tests/test_b.py::test_b"),
        actual_executed_membership_identity="sha256:" + "a" * 64,
        actual_executed_members=("tests/test_a.py::test_a", "tests/test_b.py::test_b"),
        membership_complete=True,
        unproven_owner_members=("tests/test_b.py::test_b",),
        results=(
            CanonicalShardResultEvidence(
                shard_id="hashmarks-verification-0",
                described_work_identity="sha256:" + "b" * 64,
                physical_work_identity="sha256:" + "c" * 64,
                status="PASS",
                members=("tests/test_a.py::test_a", "tests/test_b.py::test_b"),
            ),
        ),
    )
    result = FleetPlanResult(plan_id="plan", steps=(), shard_execution=shard_execution)

    projected = _verification_execution_boundary(result, None)

    assert projected is not None
    assert projected.producer_version == "0.14.0"
    assert projected.repository_identity == "sha256:" + "6" * 64 + ":2"
    assert projected.selected_membership_identity == "sha256:" + "8" * 64
    assert projected.selected_members == ("tests/test_a.py::test_a", "tests/test_b.py::test_b")
    assert projected.actual_executed_membership_identity == "sha256:" + "a" * 64
    assert projected.actual_executed_members == ("tests/test_a.py::test_a", "tests/test_b.py::test_b")
    assert projected.producer_contract_identity == "sha256:" + "5" * 64
    assert projected.selection_state == "ADMITTED_FRESH"
    assert projected.producer_freshness_state == "PROVEN_FRESH"
    assert projected.producer_provenance_identity == "sha256:" + "7" * 64
    assert projected.producer_repository_generation == 17
    assert projected.producer_identity_generation == 5
    assert projected.producer_evidence_hashes == (("ownership", "sha256:" + "f" * 64),)
    assert projected.execution_context_identity == "sha256:" + "3" * 64
    assert projected.membership_complete is True
    assert projected.unproven_owner_members == ("tests/test_b.py::test_b",)


def test_game_tape_projects_existing_runtime_and_quiescence_receipt_identities() -> None:
    from oh_goon.core.game_tape_projection import _shard_lineage
    from oh_goon.core.models.shard_execution import CanonicalShardAttemptEvidence

    runtime_receipt = "sha256:" + "d" * 64
    supervisor_receipt = "sha256:" + "e" * 64
    shard_execution = CanonicalShardExecutionEvidence(
        plan_identity="sha256:" + "1" * 64,
        repository_generation_identity="sha256:" + "2" * 64,
        execution_context_identity="sha256:" + "3" * 64,
        execution_policy_identity="sha256:" + "4" * 64,
        producer_contract_schema="hashmarks.verification-selection.v1",
        producer_version="0.14.0",
        results=(
            CanonicalShardResultEvidence(
                shard_id="verification-0",
                described_work_identity="sha256:" + "b" * 64,
                physical_work_identity="sha256:" + "c" * 64,
                status="PASS",
                attempts=(
                    CanonicalShardAttemptEvidence(
                        attempt_identity="sha256:" + "a" * 64,
                        attempt=1,
                        physical_work_identity="sha256:" + "c" * 64,
                        status="PASS",
                        duration_ms=12,
                        runtime_authority_receipt_identity=runtime_receipt,
                        supervisor_receipt_identity=supervisor_receipt,
                        process_group_quiescent=True,
                        forced_termination=False,
                    ),
                ),
            ),
        ),
    )
    result = FleetPlanResult(plan_id="plan", steps=(), shard_execution=shard_execution)

    attempt = _shard_lineage(result)[0].attempts[0]
    lineage = _shard_lineage(result)[0]

    assert lineage.members == ()
    assert attempt.runtime_authority_receipt_identity == runtime_receipt
    assert attempt.supervisor_receipt_identity == supervisor_receipt
    assert attempt.process_group_quiescent is True
    assert attempt.forced_termination is False





def test_game_tape_runtime_progress_projects_latest_durable_lifecycle_fact_only() -> None:
    from datetime import datetime, timezone
    from types import SimpleNamespace

    from oh_goon.core.game_tape_projection import _runtime_progress
    from oh_goon.core.models.run_events import ProcessStartedEvent, TerminationRequestedEvent
    from oh_goon.core.vocabulary import FleetRunEventType

    at = datetime(2026, 9, 25, tzinfo=timezone.utc)
    common = dict(
        fleet_run_id="run-1", repository_id="repo-1", operation_id="op-1",
        step_id="check", step_order=2, attempt_number=1, step_attempt_id="attempt-1",
        physical_execution_id="physical-1", pid=4321, process_identity="pid:4321",
    )
    started = ProcessStartedEvent(
        event_type=FleetRunEventType.PROCESS_STARTED, sequence=7, generated_at=at, **common
    )
    termination = TerminationRequestedEvent(
        event_type=FleetRunEventType.TERMINATION_REQUESTED, sequence=8, generated_at=at,
        reason="runtime-deadline", **common,
    )

    projected = _runtime_progress(SimpleNamespace(lifecycle_events=(started, termination)), "repo-1")

    assert projected is not None
    assert projected.journal_sequence == 8
    assert projected.step_id == "check"
    assert projected.attempt_number == 1
    assert projected.physical_execution_id == "physical-1"
    assert projected.process_state == "RUNNING"
    assert projected.pid == 4321
    assert projected.termination_reason == "runtime-deadline"


def test_game_tape_runtime_progress_does_not_infer_missing_heartbeat_or_progress() -> None:
    from types import SimpleNamespace
    from oh_goon.core.game_tape_projection import _runtime_progress

    assert _runtime_progress(SimpleNamespace(lifecycle_events=()), "repo-1") is None


def test_execution_ledger_ranks_termination_requested_lifecycle_event() -> None:
    """Every projected physical lifecycle fact has a deterministic Step rank."""
    from oh_goon.core.execution_ledger import _STEP_EVENT_STAGE
    from oh_goon.core.vocabulary import GameTapeEventType

    assert _STEP_EVENT_STAGE[GameTapeEventType.TERMINATION_REQUESTED] == 6


def test_hashmarks_runtime_contract_translates_producer_owned_implementation_provenance(monkeypatch) -> None:
    from oh_goon.integrations.hashmarks import runtime_contract

    expected = "sha256:" + "d" * 64
    payload = {
        "schema": "hashmarks.producer-implementation-provenance.v1",
        "authority": "non-authoritative-explanation",
        "storage": "derived-not-persisted",
        "identity_schema": "hashmarks.producer-implementation.v1",
        "implementation_identity": expected,
        "input_count": 1,
        "inputs": [{"path": "codemap/store.py", "bytes": 123, "content_sha256": "sha256:" + "e" * 64}],
    }
    monkeypatch.setattr(runtime_contract, "producer_implementation_provenance", lambda: payload)

    projected = runtime_contract.loaded_hashmarks_producer_provenance()

    assert projected is not None
    assert projected.implementation_identity == expected
    assert projected.inputs[0].path == "codemap/store.py"
    assert projected.inputs[0].byte_count == 123


def test_hashmarks_runtime_contract_suppresses_unsafe_producer_provenance_paths(monkeypatch) -> None:
    from oh_goon.integrations.hashmarks import runtime_contract

    expected = "sha256:" + "d" * 64
    monkeypatch.setattr(runtime_contract, "producer_implementation_provenance", lambda: {
        "schema": "hashmarks.producer-implementation-provenance.v1",
        "authority": "non-authoritative-explanation",
        "storage": "derived-not-persisted",
        "identity_schema": "hashmarks.producer-implementation.v1",
        "implementation_identity": expected,
        "input_count": 1,
        "inputs": [{"path": "../host-secret.py", "bytes": 1, "content_sha256": "sha256:" + "e" * 64}],
    })

    assert runtime_contract.loaded_hashmarks_producer_provenance() is None


def test_game_tape_binds_non_durable_provenance_only_to_matching_execution_identity() -> None:
    from oh_goon.core.models.game_tape import GameTapeProducerImplementationProvenance

    expected = "sha256:" + "d" * 64
    shard_execution = CanonicalShardExecutionEvidence(
        plan_identity="sha256:" + "1" * 64,
        repository_generation_identity="sha256:" + "2" * 64,
        execution_context_identity="sha256:" + "3" * 64,
        execution_policy_identity="sha256:" + "4" * 64,
        producer_contract_schema="hashmarks.verification-selection.v1",
        producer_version="0.25.1",
        producer_artifact_identity="sha256:" + "4" * 64,
        producer_implementation_identity=expected,
        producer_contract_identity="sha256:" + "5" * 64,
        repository_content_identity="sha256:" + "6" * 64 + ":2",
        verification_selection_evidence_identity="sha256:" + "7" * 64,
        selected_membership_identity="sha256:" + "8" * 64,
        verification_revision_identity="sha256:" + "9" * 64,
        actual_executed_membership_identity="sha256:" + "a" * 64,
        membership_complete=True,
        results=(
            CanonicalShardResultEvidence(
                shard_id="verification-0",
                described_work_identity="sha256:" + "b" * 64,
                physical_work_identity="sha256:" + "c" * 64,
                status="PASS",
            ),
        ),
    )
    result = FleetPlanResult(plan_id="plan", steps=(), shard_execution=shard_execution)
    provenance = GameTapeProducerImplementationProvenance(
        producer_schema="hashmarks.producer-implementation-provenance.v1",
        authority="non-authoritative-explanation",
        storage="derived-not-persisted",
        identity_schema="hashmarks.producer-implementation.v1",
        implementation_identity=expected,
        input_count=0,
    )

    projected = _verification_execution_boundary(result, provenance)
    assert projected is not None
    assert projected.producer_implementation_provenance == provenance

    mismatch = provenance.model_copy(update={"implementation_identity": "sha256:" + "f" * 64})
    mismatched = _verification_execution_boundary(result, mismatch)
    assert mismatched is not None
    assert mismatched.producer_implementation_provenance is None
