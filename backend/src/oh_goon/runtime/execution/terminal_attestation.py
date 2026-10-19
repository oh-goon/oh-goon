"""Self-describing terminal shard attestation without authority invention.

The attestation binds already-admitted execution authority to an already-terminal
execution report.  It does not launch work, infer repository semantics, or make
proof reusable.  Quiescence and runtime authority are supplied by their native
Oh-Goon owners and are integrity-bound here for offline inspection.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass

from oh_goon.engine.command import CommandSupervisorReceipt, RuntimeAuthorityReceipt
from oh_goon.runtime.execution.offline_proof import verify_completed_shard_proof
from oh_goon.runtime.execution.shard_models import ShardExecutionReport

TERMINAL_EXECUTION_ATTESTATION_SCHEMA_FAMILY = "oh-goon.terminal-execution-attestation"
TERMINAL_EXECUTION_ATTESTATION_SCHEMA_VERSION = 1


def terminal_execution_attestation_schema() -> str:
    family = TERMINAL_EXECUTION_ATTESTATION_SCHEMA_FAMILY
    version = TERMINAL_EXECUTION_ATTESTATION_SCHEMA_VERSION
    if (
        not isinstance(family, str)
        or not family
        or family.strip() != family
        or family.endswith("/")
        or "/v" in family
    ):
        raise RuntimeError("terminal attestation schema family owner is invalid")
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        raise RuntimeError("terminal attestation schema version owner is invalid")
    return f"{family}/v{version}"


def _identity(payload: object) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _require_sha256(name: str, value: str | None) -> None:
    if value is not None and (not value.startswith("sha256:") or len(value) != 71):
        raise ValueError(f"{name} must be a sha256 identity")


@dataclass(frozen=True, slots=True)
class ProcessQuiescenceReceipt:
    quiescent: bool
    cleanup_requested: bool
    forced_termination: bool
    surviving_descendants_detected: bool
    cleanup_actions: tuple[str, ...] = ()
    final_process_tree_state: str = "quiescent"

    def __post_init__(self) -> None:
        if self.quiescent and self.surviving_descendants_detected:
            raise ValueError("quiescent receipt cannot report surviving descendants")
        if self.quiescent and self.final_process_tree_state != "quiescent":
            raise ValueError("quiescent receipt must report final_process_tree_state=quiescent")
        if not self.quiescent and self.final_process_tree_state == "quiescent":
            raise ValueError("nonquiescent receipt cannot report a quiescent final state")

    @property
    def receipt_identity(self) -> str:
        return _identity({"schema": "oh-goon.process-quiescence-receipt/v1", **asdict(self)})


def process_quiescence_receipt_from_supervisor(
    supervisor: CommandSupervisorReceipt,
) -> ProcessQuiescenceReceipt:
    """Project native command-supervisor evidence into terminal quiescence evidence.

    This is a one-way projection from the execution owner. It does not infer cleanup
    from logs or caller assertions. A nonquiescent supervisor outcome remains
    nonterminal and therefore cannot be attested.
    """
    actions = tuple(event.action for event in supervisor.recovery_events)
    return ProcessQuiescenceReceipt(
        quiescent=supervisor.final_process_group_quiescent,
        cleanup_requested=supervisor.process_group_cleanup_requested,
        forced_termination=supervisor.forced_termination,
        surviving_descendants_detected=not supervisor.final_process_group_quiescent,
        cleanup_actions=actions,
        final_process_tree_state=(
            "quiescent" if supervisor.final_process_group_quiescent else "survivors-detected"
        ),
    )


@dataclass(frozen=True, slots=True)
class TerminalExecutionAttestation:
    schema: str
    report: ShardExecutionReport
    runtime_authority: RuntimeAuthorityReceipt
    process_quiescence: ProcessQuiescenceReceipt
    offline_proof_identity: str
    runtime_authority_receipt_identity: str
    process_quiescence_receipt_identity: str
    attestation_identity: str


@dataclass(frozen=True, slots=True)
class TerminalAttestationVerification:
    valid: bool
    reasons: tuple[str, ...]
    verification_identity: str


def _attestation_payload(
    report: ShardExecutionReport,
    runtime: RuntimeAuthorityReceipt,
    quiescence: ProcessQuiescenceReceipt,
    proof_identity: str,
) -> dict[str, object]:
    return {
        "schema": terminal_execution_attestation_schema(),
        "report": asdict(report),
        "runtime_authority": asdict(runtime),
        "runtime_authority_receipt_identity": runtime.receipt_identity,
        "process_quiescence": asdict(quiescence),
        "process_quiescence_receipt_identity": quiescence.receipt_identity,
        "offline_proof_identity": proof_identity,
    }


def build_terminal_execution_attestation(
    report: ShardExecutionReport,
    *,
    runtime_authority: RuntimeAuthorityReceipt,
    process_quiescence: ProcessQuiescenceReceipt,
) -> TerminalExecutionAttestation:
    """Bind valid terminal proof + runtime authority + quiescence into one receipt."""
    proof = verify_completed_shard_proof(report)
    if not proof.valid:
        raise ValueError(
            "cannot attest invalid terminal execution proof: " + "; ".join(proof.reasons)
        )
    if not process_quiescence.quiescent:
        raise ValueError("cannot attest terminal execution before process-tree quiescence")
    if runtime_authority.execution_materialization_identity is None:
        raise ValueError(
            "cannot attest terminal execution without exact runtime execution materialization"
        )
    payload = _attestation_payload(
        report, runtime_authority, process_quiescence, proof.proof_identity
    )
    return TerminalExecutionAttestation(
        schema=terminal_execution_attestation_schema(),
        report=report,
        runtime_authority=runtime_authority,
        process_quiescence=process_quiescence,
        offline_proof_identity=proof.proof_identity,
        runtime_authority_receipt_identity=runtime_authority.receipt_identity,
        process_quiescence_receipt_identity=process_quiescence.receipt_identity,
        attestation_identity=_identity(payload),
    )


def verify_terminal_execution_attestation(
    attestation: TerminalExecutionAttestation,
) -> TerminalAttestationVerification:
    """Verify an attestation offline; never rerun or reuse its workload proof."""
    reasons: list[str] = []
    if attestation.schema != terminal_execution_attestation_schema():
        reasons.append("unsupported terminal attestation schema")
    proof = verify_completed_shard_proof(attestation.report)
    if not proof.valid:
        reasons.extend(f"execution proof: {reason}" for reason in proof.reasons)
    if attestation.offline_proof_identity != proof.proof_identity:
        reasons.append("offline proof identity mismatch")
    if (
        attestation.runtime_authority_receipt_identity
        != attestation.runtime_authority.receipt_identity
    ):
        reasons.append("runtime authority receipt identity mismatch")
    if attestation.runtime_authority.execution_materialization_identity is None:
        reasons.append("runtime authority lacks exact execution materialization")
    if (
        attestation.process_quiescence_receipt_identity
        != attestation.process_quiescence.receipt_identity
    ):
        reasons.append("process quiescence receipt identity mismatch")
    if not attestation.process_quiescence.quiescent:
        reasons.append("process tree is not quiescent")
    expected = _identity(
        _attestation_payload(
            attestation.report,
            attestation.runtime_authority,
            attestation.process_quiescence,
            attestation.offline_proof_identity,
        )
    )
    if attestation.attestation_identity != expected:
        reasons.append("terminal attestation identity mismatch")
    verification_payload = {
        "schema": "oh-goon.terminal-attestation-verification/v1",
        "attestation_identity": attestation.attestation_identity,
        "valid": not reasons,
        "reasons": reasons,
    }
    return TerminalAttestationVerification(
        not reasons, tuple(reasons), _identity(verification_payload)
    )
