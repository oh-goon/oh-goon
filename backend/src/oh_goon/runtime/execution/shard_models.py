from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Literal

from oh_goon.core.models.pytest_selection import StructuredPytestMember
from oh_goon.core.models.shard_execution import (
    CanonicalShardAttemptEvidence,
    CanonicalShardExecutionEvidence,
    CanonicalShardResultEvidence,
)
from oh_goon.runtime.execution.executed_membership_identity import (
    executed_verification_membership_identity,
)
from oh_goon.runtime.execution.pytest_projection import project_pytest_argv

ExecutionStatus = Literal[
    "PASS",
    "FAIL",
    "TIMEOUT",
    "SUITE_TIMEOUT",
    "CANCELLED",
    "ENVIRONMENT_BLOCKED",
    "RESUMED_PASS",
    "REFINED_PASS",
    "REFINED_FAIL",
    "DEADLINE_RESERVED",
]

EXECUTION_CONTRACT_VERSION = "oh-goon.shard-execution/v2"
PASS_EVIDENCE_SCHEMA = "oh-goon.shard-pass-evidence/v3"
REFINEMENT_EVIDENCE_SCHEMA = "oh-goon.shard-refinement-evidence/v2"
REPORT_SCHEMA = "oh-goon.shard-execution-report/v3"
_LOG_TAIL_LIMIT = 16_384


def _identity(payload: dict[str, object]) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _validate_sha256(name: str, value: str | None) -> None:
    if value is not None and (not value.startswith("sha256:") or len(value) != 71):
        raise ValueError(f"{name} must be a sha256 identity")


@dataclass(frozen=True, slots=True)
class ShardExecutionContext:
    """Exact Oh-Goon execution inputs that can change shard-result meaning."""

    runner_toolchain_identity: str
    dependency_identity: str
    environment_identity: str
    execution_contract_version: str = EXECUTION_CONTRACT_VERSION

    def __post_init__(self) -> None:
        for name, value in (
            ("runner_toolchain_identity", self.runner_toolchain_identity),
            ("dependency_identity", self.dependency_identity),
            ("environment_identity", self.environment_identity),
        ):
            _validate_sha256(name, value)
        if self.execution_contract_version != EXECUTION_CONTRACT_VERSION:
            raise ValueError(f"execution_contract_version must be {EXECUTION_CONTRACT_VERSION!r}")

    @property
    def context_identity(self) -> str:
        return _identity(
            {
                "execution_contract_version": self.execution_contract_version,
                "runner_toolchain_identity": self.runner_toolchain_identity,
                "dependency_identity": self.dependency_identity,
                "environment_identity": self.environment_identity,
            }
        )


def _validate_refinement_binding(value: "ShardWork") -> None:
    if value.refinement_depth < 0 or value.refinement_depth > 32:
        raise ValueError("refinement_depth must be between 0 and 32")
    if (value.refinement_depth == 0) != (value.parent_work_identity is None):
        raise ValueError("parent_work_identity must be present exactly for refined shard work")


def _expected_shard_argv(value: "ShardWork") -> tuple[str, ...]:
    if not value.members:
        if value.command_prefix or value.pytest_members:
            raise ValueError("refinable shard metadata requires explicit ordered members")
        return value.argv
    if not value.command_prefix:
        raise ValueError("refinable shard work requires command_prefix")
    if not value.pytest_members:
        return (*value.command_prefix, *value.members)
    selectors = tuple(member.executable_selector for member in value.pytest_members)
    if selectors != value.members:
        raise ValueError("structured pytest members must exactly match ordered shard members")
    return project_pytest_argv(value.command_prefix, value.pytest_members)


def _validate_shard_work(value: "ShardWork") -> None:
    if not value.shard_id.strip() or not value.argv:
        raise ValueError("shard_id/argv must be nonempty")
    _validate_sha256("work_identity", value.work_identity)
    described = value.described_work_identity or value.work_identity
    _validate_sha256("described_work_identity", described)
    object.__setattr__(value, "described_work_identity", described)
    _validate_sha256("parent_work_identity", value.parent_work_identity)
    _validate_refinement_binding(value)
    if value.pytest_members:
        expected_original = tuple(member.member_id for member in value.pytest_members)
        if value.original_admitted_member_identities != expected_original:
            raise ValueError(
                "structured pytest work must explicitly bind original admitted member identities"
            )
        if len(set(value.original_admitted_member_identities)) != len(
            value.original_admitted_member_identities
        ):
            raise ValueError("original admitted member identities must be unique")
    elif value.original_admitted_member_identities:
        raise ValueError("original admitted member identities require structured pytest members")
    if value.argv != _expected_shard_argv(value):
        raise ValueError("refinable shard argv does not match its admitted structured membership")


@dataclass(frozen=True, slots=True)
class ShardWork:
    shard_id: str
    argv: tuple[str, ...]
    work_identity: str
    isolated_process: bool = False
    described_work_identity: str | None = None
    members: tuple[str, ...] = ()
    command_prefix: tuple[str, ...] = ()
    parent_work_identity: str | None = None
    refinement_depth: int = 0
    pytest_members: tuple[StructuredPytestMember, ...] = ()
    original_admitted_member_identities: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _validate_shard_work(self)


def _validate_structured_plan(shards: tuple[ShardWork, ...], admitted: tuple[str, ...]) -> None:
    structured = tuple(member for shard in shards for member in shard.pytest_members)
    if structured and tuple(member.executable_selector for member in structured) != admitted:
        raise ValueError(
            "structured pytest membership must conserve admitted verification order exactly"
        )


@dataclass(frozen=True, slots=True)
class ShardExecutionPlan:
    plan_identity: str
    generation_identity: str
    shards: tuple[ShardWork, ...]
    source_schema: str
    producer_version: str
    repository_evidence_identity: str | None = None
    repository_content_identity: str | None = None
    selection_input_identity: str | None = None
    producer_artifact_identity: str | None = None
    producer_implementation_identity: str | None = None
    producer_algorithm: str | None = None
    producer_contract_identity: str | None = None
    verification_selection_evidence_identity: str | None = None
    selected_membership_identity: str | None = None
    verification_revision_identity: str | None = None
    producer_repository_generation: int | None = None
    producer_identity_generation: int | None = None
    producer_evidence_hashes: tuple[tuple[str, str], ...] = ()
    selected_test_nodes: tuple[str, ...] = ()
    unproven_owner_members: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        value: str | None
        for field_name, value in (
            ("plan_identity", self.plan_identity),
            ("generation_identity", self.generation_identity),
        ):
            _validate_sha256(field_name, value)
        for field_name, value in (
            ("repository_evidence_identity", self.repository_evidence_identity),
            ("selection_input_identity", self.selection_input_identity),
            ("producer_artifact_identity", self.producer_artifact_identity),
            ("producer_implementation_identity", self.producer_implementation_identity),
            ("producer_contract_identity", self.producer_contract_identity),
            (
                "verification_selection_evidence_identity",
                self.verification_selection_evidence_identity,
            ),
            ("selected_membership_identity", self.selected_membership_identity),
            ("verification_revision_identity", self.verification_revision_identity),
        ):
            _validate_sha256(field_name, value)
        if self.producer_artifact_identity is not None and self.producer_contract_identity is None:
            raise ValueError("artifact-backed producer work requires producer_contract_identity")
        if self.producer_contract_identity is not None and self.producer_artifact_identity is None:
            raise ValueError("producer_contract_identity requires producer_artifact_identity")
        if not self.shards:
            raise ValueError("execution plan must contain at least one shard")
        if len({shard.shard_id for shard in self.shards}) != len(self.shards):
            raise ValueError("shard ids must be unique")
        if len({shard.work_identity for shard in self.shards}) != len(self.shards):
            raise ValueError("root shard work identities must be unique")
        verification_fields = (
            self.verification_selection_evidence_identity,
            self.selected_membership_identity,
            self.verification_revision_identity,
        )
        if (
            any(value is not None for value in verification_fields)
            or self.selected_test_nodes
            or self.unproven_owner_members
        ):
            if any(value is None for value in verification_fields) or not self.selected_test_nodes:
                raise ValueError(
                    "verification selection binding requires evidence, membership, revision, and selected test nodes"
                )
            if len(set(self.selected_test_nodes)) != len(self.selected_test_nodes):
                raise ValueError("selected verification test nodes must be unique")
            if not set(self.unproven_owner_members).issubset(self.selected_test_nodes):
                raise ValueError("unproven owner members must be selected test nodes")
            admitted = tuple(member for shard in self.shards for member in shard.members)
            if len(admitted) != len(set(admitted)) or set(admitted) != set(
                self.selected_test_nodes
            ):
                raise ValueError(
                    "Oh-Goon shard grouping must conserve admitted verification membership exactly"
                )
            _validate_structured_plan(self.shards, admitted)


@dataclass(frozen=True, slots=True)
class ShardRetryPolicy:
    max_attempts: int = 1
    retryable_exit_codes: tuple[int, ...] = ()
    retry_timeouts: bool = False
    retry_environment_failures: bool = False
    reason: str = ""
    delays_seconds: tuple[float, ...] = ()

    def __post_init__(self) -> None:
        if self.max_attempts < 1 or self.max_attempts > 5:
            raise ValueError("max_attempts must be between 1 and 5")
        retry_configured = bool(
            self.retryable_exit_codes or self.retry_timeouts or self.retry_environment_failures
        )
        if self.max_attempts == 1:
            if retry_configured or self.reason or self.delays_seconds:
                raise ValueError("retry settings require max_attempts > 1")
            return
        if not retry_configured:
            raise ValueError("max_attempts > 1 requires an explicit retry condition")
        if not self.reason.strip():
            raise ValueError("retries require an explicit nonblank reason")
        if len(self.delays_seconds) != self.max_attempts - 1:
            raise ValueError("retries require one explicit delay for every retry")
        if any(delay < 0 for delay in self.delays_seconds):
            raise ValueError("retry delays must be >= 0")

    @property
    def policy_identity(self) -> str:
        return _identity(
            {
                "schema": "oh-goon.shard-retry-policy/v1",
                "max_attempts": self.max_attempts,
                "retryable_exit_codes": list(self.retryable_exit_codes),
                "retry_timeouts": self.retry_timeouts,
                "retry_environment_failures": self.retry_environment_failures,
                "reason": self.reason,
                "delays_seconds": list(self.delays_seconds),
            }
        )


def execution_policy_identity(
    *,
    workers: int,
    shard_timeout_seconds: float,
    suite_timeout_seconds: float | None,
    minimum_execution_window_seconds: float,
    retry_policy: ShardRetryPolicy,
    timeout_refinement: bool,
    resume: bool,
) -> str:
    """Identity the Oh-Goon execution policy without creating a second policy model."""
    return _identity(
        {
            "schema": "oh-goon.shard-execution-policy/v1",
            "workers": workers,
            "shard_timeout_seconds": shard_timeout_seconds,
            "suite_timeout_seconds": suite_timeout_seconds,
            "minimum_execution_window_seconds": minimum_execution_window_seconds,
            "retry_policy_identity": retry_policy.policy_identity,
            "timeout_refinement": timeout_refinement,
            "resume": resume,
        }
    )


@dataclass(frozen=True, slots=True)
class ShardAttemptEvidence:
    attempt_identity: str
    physical_work_identity: str
    attempt: int
    status: ExecutionStatus
    returncode: int | None
    duration_ms: int
    stdout_tail: str
    stderr_tail: str
    retry_admitted: bool
    retry_reason: str | None
    runtime_authority_receipt_identity: str | None = None
    supervisor_receipt_identity: str | None = None
    process_group_quiescent: bool | None = None
    forced_termination: bool | None = None


@dataclass(frozen=True, slots=True)
class ShardResult:
    shard_id: str
    described_work_identity: str
    work_identity: str
    status: ExecutionStatus
    attempts: tuple[ShardAttemptEvidence, ...]
    resumed: bool = False
    isolated_process: bool = False
    parent_work_identity: str | None = None
    refinement_depth: int = 0
    reason_code: str | None = None
    members: tuple[str, ...] = ()
    pytest_members: tuple[StructuredPytestMember, ...] = ()
    children: tuple["ShardResult", ...] = ()

    @property
    def passed(self) -> bool:
        if self.children:
            return self.status == "REFINED_PASS" and all(child.passed for child in self.children)
        return self.status in {"PASS", "RESUMED_PASS"}

    def canonical_evidence(self) -> CanonicalShardResultEvidence:
        return CanonicalShardResultEvidence(
            shard_id=self.shard_id,
            described_work_identity=self.described_work_identity,
            physical_work_identity=self.work_identity,
            parent_work_identity=self.parent_work_identity,
            refinement_depth=self.refinement_depth,
            status=self.status,
            resumed=self.resumed,
            reason_code=self.reason_code,
            members=self.members,
            attempts=tuple(
                CanonicalShardAttemptEvidence(
                    attempt_identity=item.attempt_identity,
                    attempt=item.attempt,
                    physical_work_identity=item.physical_work_identity,
                    status=item.status,
                    returncode=item.returncode,
                    duration_ms=item.duration_ms,
                    retry_admitted=item.retry_admitted,
                    retry_reason=item.retry_reason,
                    runtime_authority_receipt_identity=item.runtime_authority_receipt_identity,
                    supervisor_receipt_identity=item.supervisor_receipt_identity,
                    process_group_quiescent=item.process_group_quiescent,
                    forced_termination=item.forced_termination,
                )
                for item in self.attempts
            ),
            children=tuple(child.canonical_evidence() for child in self.children),
        )


@dataclass(frozen=True, slots=True)
class ShardExecutionReport:
    schema: str
    plan_identity: str
    generation_identity: str
    source_schema: str
    producer_version: str
    repository_evidence_identity: str | None
    repository_content_identity: str | None
    selection_input_identity: str | None
    producer_artifact_identity: str | None
    producer_implementation_identity: str | None
    producer_algorithm: str | None
    producer_contract_identity: str | None
    verification_selection_evidence_identity: str | None
    selected_membership_identity: str | None
    verification_revision_identity: str | None
    producer_repository_generation: int | None
    producer_identity_generation: int | None
    producer_evidence_hashes: tuple[tuple[str, str], ...]
    selected_test_nodes: tuple[str, ...]
    unproven_owner_members: tuple[str, ...]
    actual_executed_membership_identity: str | None
    actual_executed_members: tuple[str, ...]
    membership_complete: bool
    execution_contract_version: str
    execution_context_identity: str
    execution_policy_identity: str
    runner_toolchain_identity: str
    dependency_identity: str
    environment_identity: str
    workers: int
    shard_timeout_seconds: float
    suite_timeout_seconds: float | None
    minimum_execution_window_seconds: float
    results: tuple[ShardResult, ...]
    duration_ms: int

    @property
    def passed(self) -> bool:
        return all(result.passed for result in self.results) and self.membership_complete

    def to_json(self) -> str:
        return json.dumps(asdict(self), sort_keys=True, separators=(",", ":"))

    def canonical_evidence(self) -> CanonicalShardExecutionEvidence:
        return CanonicalShardExecutionEvidence(
            plan_identity=self.plan_identity,
            repository_generation_identity=self.generation_identity,
            execution_context_identity=self.execution_context_identity,
            execution_policy_identity=self.execution_policy_identity,
            producer_contract_schema=self.source_schema,
            producer_version=self.producer_version,
            producer_artifact_identity=self.producer_artifact_identity,
            producer_implementation_identity=self.producer_implementation_identity,
            producer_contract_identity=self.producer_contract_identity,
            repository_content_identity=self.repository_content_identity,
            verification_selection_evidence_identity=self.verification_selection_evidence_identity,
            selected_membership_identity=self.selected_membership_identity,
            verification_revision_identity=self.verification_revision_identity,
            producer_repository_generation=self.producer_repository_generation,
            producer_identity_generation=self.producer_identity_generation,
            producer_evidence_hashes=self.producer_evidence_hashes,
            selected_members=self.selected_test_nodes,
            actual_executed_membership_identity=self.actual_executed_membership_identity,
            actual_executed_members=self.actual_executed_members,
            membership_complete=self.membership_complete,
            unproven_owner_members=self.unproven_owner_members,
            results=tuple(result.canonical_evidence() for result in self.results),
        )


def build_execution_report(
    *,
    plan: ShardExecutionPlan,
    context: ShardExecutionContext,
    workers: int,
    shard_timeout_seconds: float,
    suite_timeout_seconds: float | None,
    minimum_execution_window_seconds: float,
    execution_policy_identity: str,
    results: tuple[ShardResult, ...],
    duration_ms: int,
) -> ShardExecutionReport:
    def accepted_leaf_members(result: ShardResult) -> tuple[str, ...]:
        if result.children:
            return tuple(
                member for child in result.children for member in accepted_leaf_members(child)
            )
        return result.members if result.passed else ()

    executed_members = tuple(
        member for result in results for member in accepted_leaf_members(result)
    )
    executed_unique = len(executed_members) == len(set(executed_members))
    if plan.selected_test_nodes:
        membership_complete = (
            executed_unique
            and len(executed_members) == len(plan.selected_test_nodes)
            and set(executed_members) == set(plan.selected_test_nodes)
        )
        actual_membership_identity = executed_verification_membership_identity(executed_members)
    else:
        membership_complete = all(result.passed for result in results)
        actual_membership_identity = None
    _validate_sha256("execution_policy_identity", execution_policy_identity)
    return ShardExecutionReport(
        schema=REPORT_SCHEMA,
        plan_identity=plan.plan_identity,
        generation_identity=plan.generation_identity,
        source_schema=plan.source_schema,
        producer_version=plan.producer_version,
        repository_evidence_identity=plan.repository_evidence_identity,
        repository_content_identity=plan.repository_content_identity,
        selection_input_identity=plan.selection_input_identity,
        producer_artifact_identity=plan.producer_artifact_identity,
        producer_implementation_identity=plan.producer_implementation_identity,
        producer_algorithm=plan.producer_algorithm,
        producer_contract_identity=plan.producer_contract_identity,
        verification_selection_evidence_identity=plan.verification_selection_evidence_identity,
        selected_membership_identity=plan.selected_membership_identity,
        verification_revision_identity=plan.verification_revision_identity,
        producer_repository_generation=plan.producer_repository_generation,
        producer_identity_generation=plan.producer_identity_generation,
        producer_evidence_hashes=plan.producer_evidence_hashes,
        selected_test_nodes=plan.selected_test_nodes,
        unproven_owner_members=plan.unproven_owner_members,
        actual_executed_membership_identity=actual_membership_identity,
        actual_executed_members=tuple(sorted(executed_members)),
        membership_complete=membership_complete,
        execution_contract_version=context.execution_contract_version,
        execution_context_identity=context.context_identity,
        execution_policy_identity=execution_policy_identity,
        runner_toolchain_identity=context.runner_toolchain_identity,
        dependency_identity=context.dependency_identity,
        environment_identity=context.environment_identity,
        workers=workers,
        shard_timeout_seconds=shard_timeout_seconds,
        suite_timeout_seconds=suite_timeout_seconds,
        minimum_execution_window_seconds=minimum_execution_window_seconds,
        results=results,
        duration_ms=duration_ms,
    )
