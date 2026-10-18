from __future__ import annotations

from oh_goon.core.models.execution_authority_economics import (
    ExecutionAuthorityEconomicsReceipt,
)
from oh_goon.core.models.fleet_plan import ResolvedCommandStep
from oh_goon.integrations._fleet_plan_step_evidence import attempt_evidence
from oh_goon.integrations._fleet_plan_step_outcome import StepOutcome
from oh_goon.integrations.executable_materialization_authority import (
    FileMaterializationReceipt,
    StepExecutableMaterializationReceipt,
)


def _receipt(*, scope: str, reused: int, recomputed: int) -> ExecutionAuthorityEconomicsReceipt:
    return ExecutionAuthorityEconomicsReceipt(
        scope=scope,  # type: ignore[arg-type]
        candidate_units=reused + recomputed,
        reused_units=reused,
        recomputed_units=recomputed,
        reused_bytes=100 * reused,
        recomputed_bytes=100 * recomputed,
        terminal_proof_bytes=50 if scope == "runtime-launch" else 0,
        reuse_provenance=("prepared-host-tool:true",) if reused else (),
        recompute_reasons=("mutable-attempt",) if recomputed else (),
    )


def test_attempt_evidence_preserves_bounded_authority_economics_receipts() -> None:
    materialization = _receipt(scope="materialization", reused=0, recomputed=2)
    runtime = _receipt(scope="runtime-launch", reused=1, recomputed=0)
    step = ResolvedCommandStep(timeout_seconds=30, step_id="check", argv=("true",))
    outcome = StepOutcome(argv=("true",), returncode=0)

    evidence = attempt_evidence(
        step=step,
        attempt=1,
        outcome=outcome,
        repository_state_before="before",
        repository_state_after="after",
        worktree_files_after=(),
        execution_authority_economics=(materialization, runtime),
    )

    assert [item.scope for item in evidence.execution_authority_economics] == [
        "materialization",
        "runtime-launch",
    ]
    assert evidence.execution_authority_economics[0].recomputed_units == 2
    assert evidence.execution_authority_economics[1].reused_units == 1
    assert evidence.execution_authority_economics[1].receipt_identity == runtime.receipt_identity


def test_diagnostic_economics_does_not_change_materialization_authority_identity() -> None:
    file_receipt = FileMaterializationReceipt(
        relative_path="Makefile",
        sha256="sha256:" + "1" * 64,
        byte_count=10,
    )
    cold = StepExecutableMaterializationReceipt(
        logical_sources=(file_receipt,),
        economics=_receipt(scope="materialization", reused=0, recomputed=1),
    )
    reused = StepExecutableMaterializationReceipt(
        logical_sources=(file_receipt,),
        economics=_receipt(scope="materialization", reused=1, recomputed=0),
    )

    assert cold.receipt_identity == reused.receipt_identity
