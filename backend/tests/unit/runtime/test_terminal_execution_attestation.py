from __future__ import annotations

from dataclasses import replace

import pytest

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
from oh_goon.runtime.execution.terminal_attestation import (
    ProcessQuiescenceReceipt,
    RuntimeAuthorityReceipt,
    build_terminal_execution_attestation,
    verify_terminal_execution_attestation,
)


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
    return RuntimeAuthorityReceipt(
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
    return ProcessQuiescenceReceipt(
        quiescent=True,
        cleanup_requested=True,
        forced_termination=True,
        surviving_descendants_detected=False,
        cleanup_actions=("terminate_process_group",),
        final_process_tree_state="quiescent",
    )


def test_terminal_attestation_binds_terminal_proof_runtime_and_quiescence() -> None:
    receipt = build_terminal_execution_attestation(
        _report(), runtime_authority=_runtime(), process_quiescence=_quiescence()
    )
    verified = verify_terminal_execution_attestation(receipt)
    assert verified.valid is True
    assert verified.reasons == ()
    assert receipt.attestation_identity.startswith("sha256:")


def test_terminal_attestation_rejects_nonterminal_or_nonquiescent_evidence() -> None:
    report = replace(_report(), membership_complete=False)
    with pytest.raises(ValueError, match="invalid terminal execution proof"):
        build_terminal_execution_attestation(
            report, runtime_authority=_runtime(), process_quiescence=_quiescence()
        )
    nonquiescent = ProcessQuiescenceReceipt(
        quiescent=False,
        cleanup_requested=True,
        forced_termination=True,
        surviving_descendants_detected=True,
        cleanup_actions=("terminate_process_group",),
        final_process_tree_state="survivors-detected",
    )
    with pytest.raises(ValueError, match="process-tree quiescence"):
        build_terminal_execution_attestation(
            _report(), runtime_authority=_runtime(), process_quiescence=nonquiescent
        )


def test_terminal_attestation_rejects_pre_exec_runtime_observation() -> None:
    observed_only = replace(_runtime(), execution_materialization_identity=None)
    with pytest.raises(ValueError, match="exact runtime execution materialization"):
        build_terminal_execution_attestation(
            _report(),
            runtime_authority=observed_only,
            process_quiescence=_quiescence(),
        )


def test_offline_attestation_rejects_each_bound_receipt_tamper() -> None:
    receipt = build_terminal_execution_attestation(
        _report(), runtime_authority=_runtime(), process_quiescence=_quiescence()
    )
    cases = (
        replace(
            receipt,
            runtime_authority=replace(receipt.runtime_authority, executable_identity=_sha("0")),
        ),
        replace(
            receipt,
            process_quiescence=replace(receipt.process_quiescence, forced_termination=False),
        ),
        replace(receipt, offline_proof_identity=_sha("0")),
        replace(receipt, attestation_identity=_sha("0")),
    )
    for tampered in cases:
        assert verify_terminal_execution_attestation(tampered).valid is False


def test_runtime_authority_requires_exact_identity_shapes_and_path() -> None:
    with pytest.raises(ValueError, match="executable_path"):
        RuntimeAuthorityReceipt(
            executable_path=" ",
            executable_identity=_sha("1"),
            executable_byte_count=1,
            path_authority_identity=_sha("2"),
            environment_identity=_sha("3"),
        )
    with pytest.raises(ValueError, match="executable_identity"):
        RuntimeAuthorityReceipt(
            executable_path="/bin/x",
            executable_identity="x",
            executable_byte_count=1,
            path_authority_identity=_sha("2"),
            environment_identity=_sha("3"),
        )


def test_quiescence_receipt_is_derived_from_native_command_supervisor() -> None:
    from oh_goon.engine.command import CommandSupervisorReceipt
    from oh_goon.runtime.execution.terminal_attestation import (
        process_quiescence_receipt_from_supervisor,
    )
    from oh_goon.runtime.recovery_evidence import ExecutionRecoveryEvent

    supervisor = CommandSupervisorReceipt(
        leader_exited_before_group_quiescence=True,
        process_group_cleanup_requested=True,
        cleanup_reason="output_cleanup",
        forced_termination=False,
        final_process_group_quiescent=True,
        recovery_events=(
            ExecutionRecoveryEvent(
                kind="process_group_cleanup",
                action="terminate_process_group",
                reason_code="leader_exited_with_descendants",
                outcome="requested",
            ),
        ),
    )
    receipt = process_quiescence_receipt_from_supervisor(supervisor)
    assert receipt.quiescent is True
    assert receipt.cleanup_requested is True
    assert receipt.cleanup_actions == ("terminate_process_group",)
    assert receipt.surviving_descendants_detected is False


def test_nonquiescent_native_supervisor_cannot_become_terminal_attestation() -> None:
    from oh_goon.engine.command import CommandSupervisorReceipt
    from oh_goon.runtime.execution.terminal_attestation import (
        process_quiescence_receipt_from_supervisor,
    )

    receipt = process_quiescence_receipt_from_supervisor(
        CommandSupervisorReceipt(final_process_group_quiescent=False)
    )
    assert receipt.quiescent is False
    with pytest.raises(ValueError, match="process-tree quiescence"):
        build_terminal_execution_attestation(
            _report(), runtime_authority=_runtime(), process_quiescence=receipt
        )


def test_runtime_authority_receipt_is_owned_by_command_execution_layer():
    from oh_goon.engine.command import RuntimeAuthorityReceipt as NativeReceipt
    from oh_goon.runtime.execution.terminal_attestation import (
        RuntimeAuthorityReceipt as AttestationReceipt,
    )

    assert NativeReceipt is AttestationReceipt


def test_offline_attestation_rejects_full_authority_tamper_matrix() -> None:
    receipt = build_terminal_execution_attestation(
        _report(), runtime_authority=_runtime(), process_quiescence=_quiescence()
    )
    report = receipt.report
    result = report.results[0]
    attempt = result.attempts[0]

    report_tampers = {
        "producer identity": replace(report, producer_implementation_identity=_sha("0")),
        "source identity": replace(report, verification_revision_identity=_sha("0")),
        "selection identity": replace(report, verification_selection_evidence_identity=_sha("0")),
        "membership": replace(report, actual_executed_members=("tests/test_other.py",)),
        "execution context identity": replace(report, execution_context_identity=_sha("0")),
        "execution policy identity": replace(report, execution_policy_identity=_sha("0")),
        "supply identity": replace(report, dependency_identity=_sha("0")),
        "attempt lineage": replace(
            report,
            results=(replace(result, attempts=(replace(attempt, attempt_identity=_sha("f")),)),),
        ),
        "terminal result": replace(report, results=(replace(result, status="FAIL"),)),
    }
    runtime_tampers = {
        "runtime identity": replace(receipt.runtime_authority, runtime_identity=_sha("0")),
        "provider identity": replace(receipt.runtime_authority, provider_identity=_sha("0")),
        "PATH authority": replace(receipt.runtime_authority, path_authority_identity=_sha("0")),
        "executable identity": replace(receipt.runtime_authority, executable_identity=_sha("0")),
        "executable path": replace(
            receipt.runtime_authority, executable_path="/tampered/bin/pytest"
        ),
        "dependency supply identity": replace(
            receipt.runtime_authority, dependency_supply_identity=_sha("0")
        ),
    }

    for label, tampered_report in report_tampers.items():
        verified = verify_terminal_execution_attestation(replace(receipt, report=tampered_report))
        assert verified.valid is False, label
    for label, tampered_runtime in runtime_tampers.items():
        verified = verify_terminal_execution_attestation(
            replace(receipt, runtime_authority=tampered_runtime)
        )
        assert verified.valid is False, label

    # The attestation identity is the exact linkage exported to certification/audit.
    certification_linkage_tamper = replace(receipt, attestation_identity=_sha("0"))
    assert verify_terminal_execution_attestation(certification_linkage_tamper).valid is False


def test_offline_verification_never_launches_workload(monkeypatch: pytest.MonkeyPatch) -> None:
    receipt = build_terminal_execution_attestation(
        _report(), runtime_authority=_runtime(), process_quiescence=_quiescence()
    )

    def forbidden_launch(*args: object, **kwargs: object) -> object:
        raise AssertionError("offline verification launched workload")

    from oh_goon.runtime.execution import shards

    monkeypatch.setattr(shards, "execute_shard_plan", forbidden_launch)
    verified = verify_terminal_execution_attestation(receipt)
    assert verified.valid is True


def test_offline_verification_cannot_upgrade_nonterminal_report() -> None:
    receipt = build_terminal_execution_attestation(
        _report(), runtime_authority=_runtime(), process_quiescence=_quiescence()
    )
    report = replace(receipt.report, membership_complete=False)
    verified = verify_terminal_execution_attestation(replace(receipt, report=report))
    assert verified.valid is False
    assert any(
        "not terminal PASS" in reason or "incomplete" in reason for reason in verified.reasons
    )


def test_terminal_verification_result_is_inspection_only_not_reusable_execution_authority() -> None:
    receipt = build_terminal_execution_attestation(
        _report(), runtime_authority=_runtime(), process_quiescence=_quiescence()
    )
    verified = verify_terminal_execution_attestation(receipt)
    assert verified.valid is True
    assert not hasattr(verified, "execute")
    assert not hasattr(verified, "admit")
    assert not hasattr(verified, "reuse")
    assert not hasattr(verified, "execution_plan")
