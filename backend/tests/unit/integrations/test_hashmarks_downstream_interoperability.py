from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

pytest.importorskip("hashmarks")
from hashmarks.verification_selection import (
    VerificationSelectionEnvelopeState,
    downstream_consumption_contract,
    verification_member,
    verification_membership,
    verification_selection_envelope,
)


pytestmark = pytest.mark.dependency_complete

from tests.hashmarks_test_support import (
    TEST_HASHMARKS_ARTIFACT_IDENTITY,
)
from oh_goon.engine.command import CommandResult
from tests.factories.runtime_authority import authorized_command_result
from oh_goon.integrations.hashmarks.verification_selection import (
    load_hashmarks_verification_selection,
)
from oh_goon.repository_tooling.hashmarks_verification_execution import (
    VerificationExecutionGroupingPolicy,
    build_verification_execution_plan,
    build_verification_execution_receipt,
)
from oh_goon.runtime.execution import shards as subject


def _sha(char: str) -> str:
    return "sha256:" + char * 64


def _context() -> subject.ShardExecutionContext:
    return subject.ShardExecutionContext(_sha("1"), _sha("2"), _sha("3"))


def _selection(tmp_path: Path, count: int = 4):
    membership = verification_membership(
        [verification_member("tests/test_x.py", test_symbol=f"test_{index}") for index in range(count)]
    )
    envelope = verification_selection_envelope(
        VerificationSelectionEnvelopeState(
            repository_identity="git-tree:interop",
            source_identity=_sha("a"),
            codemap_generation=9,
            identity_generation=4,
            stale=False,
            membership=membership,
            owner_evidence={"selected": "src/x.py"},
            evidence_hashes={"verification_plan": _sha("b")},
        )
    )
    contract = downstream_consumption_contract(envelope)
    contract_path = tmp_path / "contract.json"
    envelope_path = tmp_path / "envelope.json"
    contract_path.write_text(json.dumps(contract), encoding="utf-8")
    envelope_path.write_text(json.dumps(envelope), encoding="utf-8")
    return load_hashmarks_verification_selection(
        contract_path,
        envelope_path,
        admitted_repository_identity="git-tree:interop",
        admitted_source_identity=_sha("a"),
        expected_producer_artifact_identity=TEST_HASHMARKS_ARTIFACT_IDENTITY,
    )


def test_native_selection_timeout_bisection_preserves_hashmarks_membership(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    selection = _selection(tmp_path)
    plan = build_verification_execution_plan(selection, command_prefix=("fake",), grouping_policy=VerificationExecutionGroupingPolicy(4))
    calls: list[tuple[str, ...]] = []

    def fake_run(*args: str, **kwargs: object) -> CommandResult:
        calls.append(args)
        if len(args) == 6:
            raise subprocess.TimeoutExpired(args, float(kwargs["timeout"]))
        return authorized_command_result(returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr(subject, "run_command", fake_run)
    report = subject.execute_shard_plan(
        plan,
        cwd=tmp_path,
        workers=1,
        shard_timeout_seconds=1,
        execution_context=_context(),
    )
    receipt = build_verification_execution_receipt(selection, report)
    assert report.passed is True
    assert receipt.terminal_success is True
    assert receipt.selected_membership_identity == selection.membership_identity
    assert receipt.terminal_membership_identity == selection.membership_identity
    assert receipt.missing == receipt.duplicates == receipt.extras == ()
    assert any(row["refinement_depth"] == 1 for row in receipt.retry_bisection_lineage)
    assert calls[0] == ("fake", "--", *selection.selected_test_nodes)


def test_native_selection_retry_keeps_terminal_coverage_unique(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    selection = _selection(tmp_path, count=1)
    plan = build_verification_execution_plan(selection, command_prefix=("fake",), grouping_policy=VerificationExecutionGroupingPolicy(1))
    attempts = 0

    def fake_run(*args: str, **kwargs: object) -> CommandResult:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return authorized_command_result(returncode=17, stdout="", stderr="retryable")
        return authorized_command_result(returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr(subject, "run_command", fake_run)
    report = subject.execute_shard_plan(
        plan,
        cwd=tmp_path,
        workers=1,
        shard_timeout_seconds=1,
        retry_policy=subject.ShardRetryPolicy(
            max_attempts=2,
            retryable_exit_codes=(17,),
            reason="interop retry",
            delays_seconds=(0,),
        ),
        execution_context=_context(),
    )
    receipt = build_verification_execution_receipt(selection, report)
    assert attempts == 2
    assert receipt.terminal_success is True
    assert receipt.duplicates == ()
    assert len(receipt.retry_bisection_lineage[0]["attempts"]) == 2  # type: ignore[arg-type]


def test_native_selection_resume_reuses_exact_receipts_without_relaunch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    selection = _selection(tmp_path, count=2)
    plan = build_verification_execution_plan(selection, command_prefix=("fake",), grouping_policy=VerificationExecutionGroupingPolicy(1))
    state = tmp_path / "state"
    calls: list[tuple[str, ...]] = []

    def fake_run(*args: str, **kwargs: object) -> CommandResult:
        calls.append(args)
        return authorized_command_result(returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr(subject, "run_command", fake_run)
    first = subject.execute_shard_plan(
        plan,
        cwd=tmp_path,
        workers=1,
        shard_timeout_seconds=1,
        state_root=state,
        execution_context=_context(),
    )
    first_count = len(calls)
    second = subject.execute_shard_plan(
        plan,
        cwd=tmp_path,
        workers=1,
        shard_timeout_seconds=1,
        state_root=state,
        execution_context=_context(),
    )
    receipt = build_verification_execution_receipt(selection, second)
    assert first.passed is True
    assert second.passed is True
    assert len(calls) == first_count
    assert all(result.status == "RESUMED_PASS" for result in second.results)
    assert receipt.terminal_success is True
    assert receipt.attempted == ()
    assert receipt.resumed == tuple(sorted(selection.selected_test_nodes))
    assert receipt.completed == tuple(sorted(selection.selected_test_nodes))
