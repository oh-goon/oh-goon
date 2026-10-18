"""Offline verification for completed shard execution reports.

This verifier validates already-produced evidence only.  It never launches work,
reuses proof, reconstructs producer semantics, or upgrades a nonterminal result.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass

from oh_goon.runtime.execution.executed_membership_identity import (
    executed_verification_membership_identity,
)
from oh_goon.runtime.execution.shard_models import ShardExecutionReport


def _identity(payload: object) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )
    return "sha256:" + hashlib.sha256(raw).hexdigest()


@dataclass(frozen=True, slots=True)
class OfflineProofVerification:
    valid: bool
    reasons: tuple[str, ...]
    proof_identity: str


def _authority_reasons(
    report: ShardExecutionReport, expected: tuple[tuple[str | None, str | None, str], ...]
) -> list[str]:
    reasons: list[str] = []
    expected_context = _identity(
        {
            "execution_contract_version": report.execution_contract_version,
            "runner_toolchain_identity": report.runner_toolchain_identity,
            "dependency_identity": report.dependency_identity,
            "environment_identity": report.environment_identity,
        }
    )
    if report.execution_context_identity != expected_context:
        reasons.append("execution context identity mismatch")
    reasons.extend(
        f"{label} mismatch"
        for observed, wanted, label in expected
        if wanted is not None and observed != wanted
    )
    return reasons


def _membership_reasons(report: ShardExecutionReport) -> list[str]:
    reasons: list[str] = []
    if not report.passed:
        reasons.append("execution report is not terminal PASS")
    if not report.membership_complete:
        reasons.append("terminal membership is incomplete")
    if not report.selected_test_nodes:
        return reasons
    executed = report.actual_executed_members
    if len(executed) != len(set(executed)):
        reasons.append("terminal membership contains duplicates")
    if set(executed) != set(report.selected_test_nodes):
        reasons.append("terminal membership differs from admitted selection")
    expected_membership = executed_verification_membership_identity(executed)
    if report.actual_executed_membership_identity != expected_membership:
        reasons.append("executed membership identity mismatch")
    return reasons


def verify_completed_shard_proof(
    report: ShardExecutionReport,
    *,
    expected_plan_identity: str | None = None,
    expected_generation_identity: str | None = None,
    expected_execution_policy_identity: str | None = None,
    expected_producer_artifact_identity: str | None = None,
    expected_producer_implementation_identity: str | None = None,
) -> OfflineProofVerification:
    """Verify authority closure and terminal membership without rerunning workload."""
    expected = (
        (report.plan_identity, expected_plan_identity, "plan identity"),
        (report.generation_identity, expected_generation_identity, "generation identity"),
        (
            report.execution_policy_identity,
            expected_execution_policy_identity,
            "execution policy identity",
        ),
        (
            report.producer_artifact_identity,
            expected_producer_artifact_identity,
            "producer artifact identity",
        ),
        (
            report.producer_implementation_identity,
            expected_producer_implementation_identity,
            "producer implementation identity",
        ),
    )
    reasons = _authority_reasons(report, expected) + _membership_reasons(report)
    proof_payload = {
        "schema": "oh-goon.offline-shard-proof-verification/v1",
        "report": asdict(report),
        "verification": {
            "expected_plan_identity": expected_plan_identity,
            "expected_generation_identity": expected_generation_identity,
            "expected_execution_policy_identity": expected_execution_policy_identity,
            "expected_producer_artifact_identity": expected_producer_artifact_identity,
            "expected_producer_implementation_identity": expected_producer_implementation_identity,
        },
    }
    return OfflineProofVerification(not reasons, tuple(reasons), _identity(proof_payload))
