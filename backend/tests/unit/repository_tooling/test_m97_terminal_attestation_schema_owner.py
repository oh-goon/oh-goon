from __future__ import annotations

import ast
from pathlib import Path

import pytest

from oh_goon.runtime.execution import terminal_attestation
from oh_goon.runtime.execution.shard_models import (
    ShardAttemptEvidence,
    ShardExecutionContext,
    ShardExecutionPlan,
    ShardResult,
    ShardRetryPolicy,
    ShardWork,
    build_execution_report,
    execution_policy_identity,
)


ROOT = Path(__file__).resolve().parents[4]
PRODUCTION_ROOT = ROOT / "backend/src/oh_goon"
FAMILY = "oh-goon.terminal-execution-attestation"


def _sha(c: str) -> str:
    return "sha256:" + c * 64


def _report():
    work = ShardWork(
        shard_id="a",
        argv=("pytest", "tests/test_a.py"),
        work_identity=_sha("a"),
        members=("tests/test_a.py",),
        command_prefix=("pytest",),
    )
    plan = ShardExecutionPlan(
        plan_identity=_sha("b"),
        generation_identity=_sha("c"),
        shards=(work,),
        source_schema="test/v1",
        producer_version="0.14.0",
        producer_artifact_identity=_sha("d"),
        producer_implementation_identity=_sha("e"),
        producer_contract_identity=_sha("4"),
        selected_test_nodes=("tests/test_a.py",),
        selected_membership_identity=_sha("f"),
        verification_selection_evidence_identity=_sha("5"),
        verification_revision_identity=_sha("6"),
    )
    context = ShardExecutionContext(
        runner_toolchain_identity=_sha("1"),
        dependency_identity=_sha("2"),
        environment_identity=_sha("3"),
    )
    attempt = ShardAttemptEvidence(
        attempt_identity=_sha("0"),
        physical_work_identity=_sha("a"),
        attempt=1,
        status="PASS",
        returncode=0,
        duration_ms=1,
        stdout_tail="",
        stderr_tail="",
        retry_admitted=False,
        retry_reason=None,
    )
    result = ShardResult(
        shard_id="a",
        described_work_identity=_sha("a"),
        work_identity=_sha("a"),
        status="PASS",
        attempts=(attempt,),
        members=("tests/test_a.py",),
    )
    return build_execution_report(
        plan=plan,
        context=context,
        workers=1,
        shard_timeout_seconds=10,
        suite_timeout_seconds=20,
        minimum_execution_window_seconds=1,
        execution_policy_identity=execution_policy_identity(
            workers=1,
            shard_timeout_seconds=10,
            suite_timeout_seconds=20,
            minimum_execution_window_seconds=1,
            retry_policy=ShardRetryPolicy(),
            timeout_refinement=True,
            resume=True,
        ),
        results=(result,),
        duration_ms=1,
    )


def _runtime():
    return terminal_attestation.RuntimeAuthorityReceipt(
        executable_path="/sealed/bin/pytest",
        executable_identity=_sha("7"),
        executable_byte_count=7,
        path_authority_identity=_sha("8"),
        environment_identity=_sha("d"),
        provider_identity=_sha("9"),
        runtime_identity=_sha("a"),
        environment_generation_identity=_sha("b"),
        dependency_supply_identity=_sha("c"),
        execution_materialization_identity=_sha("e"),
    )


def _quiescence():
    return terminal_attestation.ProcessQuiescenceReceipt(
        quiescent=True,
        cleanup_requested=True,
        forced_termination=True,
        surviving_descendants_detected=False,
        cleanup_actions=("terminate_process_group",),
        final_process_tree_state="quiescent",
    )


def _build():
    return terminal_attestation.build_terminal_execution_attestation(
        _report(),
        runtime_authority=_runtime(),
        process_quiescence=_quiescence(),
    )


def test_terminal_attestation_producer_follows_schema_owner_mutation(monkeypatch) -> None:
    monkeypatch.setattr(terminal_attestation, "TERMINAL_EXECUTION_ATTESTATION_SCHEMA_VERSION", 7)

    receipt = _build()

    assert receipt.schema == f"{FAMILY}/v7"
    assert terminal_attestation.verify_terminal_execution_attestation(receipt).valid is True


def test_terminal_attestation_verifier_rejects_stale_schema_after_owner_change(monkeypatch) -> None:
    receipt = _build()
    monkeypatch.setattr(terminal_attestation, "TERMINAL_EXECUTION_ATTESTATION_SCHEMA_VERSION", 7)

    verified = terminal_attestation.verify_terminal_execution_attestation(receipt)

    assert verified.valid is False
    assert "unsupported terminal attestation schema" in verified.reasons


def test_terminal_attestation_identity_projection_follows_owner(monkeypatch) -> None:
    base = _build()
    monkeypatch.setattr(terminal_attestation, "TERMINAL_EXECUTION_ATTESTATION_SCHEMA_VERSION", 7)
    current = _build()

    assert base.attestation_identity != current.attestation_identity
    assert current.schema == terminal_attestation.terminal_execution_attestation_schema()


def test_terminal_attestation_schema_owner_fails_closed_on_invalid_version(monkeypatch) -> None:
    monkeypatch.setattr(terminal_attestation, "TERMINAL_EXECUTION_ATTESTATION_SCHEMA_VERSION", 0)

    with pytest.raises(RuntimeError, match="terminal attestation schema version owner is invalid"):
        terminal_attestation.terminal_execution_attestation_schema()
    with pytest.raises(RuntimeError, match="terminal attestation schema version owner is invalid"):
        _build()


def test_terminal_attestation_schema_owner_fails_closed_on_invalid_family(monkeypatch) -> None:
    monkeypatch.setattr(
        terminal_attestation,
        "TERMINAL_EXECUTION_ATTESTATION_SCHEMA_FAMILY",
        "oh-goon.terminal-execution-attestation/v9",
    )

    with pytest.raises(RuntimeError, match="terminal attestation schema family owner is invalid"):
        terminal_attestation.terminal_execution_attestation_schema()
    with pytest.raises(RuntimeError, match="terminal attestation schema family owner is invalid"):
        _build()


def test_production_has_one_terminal_attestation_schema_owner() -> None:
    family_literals: list[tuple[Path, int]] = []
    family_assignments: list[tuple[Path, int]] = []
    version_assignments: list[tuple[Path, int]] = []
    full_v1_literals: list[tuple[Path, int]] = []

    for path in PRODUCTION_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                if node.value == FAMILY:
                    family_literals.append((path, node.lineno))
                if node.value == f"{FAMILY}/v1":
                    full_v1_literals.append((path, node.lineno))
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                names = [target.id for target in targets if isinstance(target, ast.Name)]
                if "TERMINAL_EXECUTION_ATTESTATION_SCHEMA_FAMILY" in names:
                    family_assignments.append((path, node.lineno))
                if "TERMINAL_EXECUTION_ATTESTATION_SCHEMA_VERSION" in names:
                    version_assignments.append((path, node.lineno))

    owner = PRODUCTION_ROOT / "runtime/execution/terminal_attestation.py"
    assert family_assignments == [(owner, family_assignments[0][1])]
    assert version_assignments == [(owner, version_assignments[0][1])]
    assert family_literals == [(owner, family_literals[0][1])]
    assert not full_v1_literals
