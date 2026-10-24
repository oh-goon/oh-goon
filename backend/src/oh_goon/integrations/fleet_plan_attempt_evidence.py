from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Literal

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.fleet_plan_tool_requirements import step_execution_tool
from oh_goon.core.models.execution_authority_diagnostics import (
    PlanExecutionAuthorityAtomicHandoffEvidence,
    PlanExecutionAuthorityDiagnosticEvidence,
    PlanExecutionAuthorityMaterializationEvidence,
    PlanExecutionAuthorityRuntimeEvidence,
)
from oh_goon.core.models.fleet_plan import (
    ResolvedCommandStep,
    ResolvedLocalSearchAgentStep,
    ResolvedPlanStep,
    ResolvedPreCommitStep,
)
from oh_goon.core.models.fleet_plan_evidence import (
    PlanResourceEvidence,
    PlanRetainedContentEvidence,
    PlanSnapshotEvidence,
    PlanStepAttemptEvidence,
    PlanToolEvidence,
)
from oh_goon.core.models.fleet_plan_observation_steps import ResolvedDependencyInventoryStep
from oh_goon.core.models.fleet_plan_retry import PlanRetryDecisionEvidence
from oh_goon.core.path_authority import RepositoryPathSpace, canonical_path
from oh_goon.engine.git import GitObservation
from oh_goon.integrations._fleet_plan_resource_evidence import read_attempt_resource_evidence
from oh_goon.integrations._fleet_plan_step_evidence import attempt_evidence
from oh_goon.integrations._fleet_plan_step_local_execution import (
    step_working_directory,
)
from oh_goon.integrations._fleet_plan_step_outcome import StepOutcome
from oh_goon.integrations.content_snapshot import SnapshotIdentity
from oh_goon.integrations.fleet_plan_managed_runtime import pre_commit_tool_evidence
from oh_goon.integrations.fleet_plan_run import FleetPlanStepAttempt
from oh_goon.integrations.fleet_plan_secret_redaction import redact_host_secrets


def build_direct_attempt_evidence(
    canonical_step: ResolvedPlanStep,
    step: ResolvedPlanStep,
    attempt_number: int,
    outcome: StepOutcome,
    repository: Path,
    before: GitObservation,
    after: GitObservation,
    *,
    retry: PlanRetryDecisionEvidence | None,
) -> PlanStepAttemptEvidence:
    return attempt_evidence(
        step=canonical_step,
        attempt=attempt_number,
        outcome=redact_host_secrets(canonical_step, outcome, None),
        repository_state_before=before.fingerprint.state_id,
        repository_state_after=after.fingerprint.state_id,
        worktree_files_after=after.changed_files,
        tool=_direct_tool_evidence(step, repository, outcome),
        retry=retry,
        release_effect=outcome.release_effect,
        release_verification=outcome.release_verification,
    )


def build_strict_attempt_evidence(
    canonical_step: ResolvedPlanStep,
    step: ResolvedPlanStep,
    attempt_number: int,
    outcome: StepOutcome,
    repository: Path,
    before: GitObservation,
    after: GitObservation,
    *,
    attempt: FleetPlanStepAttempt,
    cache_output_identity: SnapshotIdentity | None,
    retained_cache: PlanRetainedContentEvidence | None,
    retry: PlanRetryDecisionEvidence | None,
) -> PlanStepAttemptEvidence:
    materialization_economics = attempt.executable_materialization.economics
    return attempt_evidence(
        step=canonical_step,
        attempt=attempt_number,
        outcome=redact_host_secrets(canonical_step, outcome, attempt.host_environment),
        repository_state_before=before.fingerprint.state_id,
        repository_state_after=after.fingerprint.state_id,
        worktree_files_after=after.changed_files,
        tool=_strict_tool_evidence(step, repository, attempt),
        cache_input=_snapshot_evidence(attempt.cache_identity),
        cache_output=(
            _snapshot_evidence(cache_output_identity) if cache_output_identity is not None else None
        ),
        retained_cache=retained_cache,
        effective_egress=attempt.egress,
        resources=_strict_resource_evidence(step, attempt),
        retry=retry,
        release_effect=outcome.release_effect,
        release_verification=outcome.release_verification,
        execution_authority_economics=(
            *((materialization_economics,) if materialization_economics is not None else ()),
            *outcome.execution_authority_economics,
        ),
        execution_authority=_execution_authority_diagnostic(attempt, outcome),
    )


def _strict_resource_evidence(
    step: ResolvedPlanStep, attempt: FleetPlanStepAttempt
) -> PlanResourceEvidence | None:
    if isinstance(step, ResolvedDependencyInventoryStep):
        return None
    evidence = read_attempt_resource_evidence(attempt.paths.root)
    if evidence is None:
        raise FleetPlanInvalid(
            "Strict Fleet process backend completed without resource-enforcement evidence"
        )
    return evidence


def _direct_tool_evidence(
    step: ResolvedPlanStep,
    repository: Path,
    outcome: StepOutcome,
) -> PlanToolEvidence | None:
    executable = step_execution_tool(step)
    if executable is None:
        if isinstance(step, ResolvedCommandStep):
            executable = step.argv[0]
        else:
            return None
    runtime = outcome.runtime_authority
    if runtime is None:
        return None
    path = Path(runtime.executable_path)
    return PlanToolEvidence(
        name=(
            path.name
            if isinstance(step, ResolvedLocalSearchAgentStep) and step.native_agent is not None
            else Path(executable).name or executable
        ),
        origin=(
            "managed" if isinstance(step, ResolvedPreCommitStep) else _tool_origin(path, repository)
        ),
        sha256=runtime.executable_identity.removeprefix("sha256:"),
        byte_count=runtime.executable_byte_count,
    )


def _strict_tool_evidence(
    step: ResolvedPlanStep,
    repository: Path,
    attempt: FleetPlanStepAttempt,
) -> PlanToolEvidence | None:
    if isinstance(step, ResolvedLocalSearchAgentStep) and step.native_agent is not None:
        # Native execution has no strict backend. A failed/replayed attempt must
        # not reinterpret its selected path as a strict host-tool binding.
        return None
    if isinstance(step, ResolvedPreCommitStep):
        return pre_commit_tool_evidence(step, attempt.managed_runtime, attempt.sandbox_bindings)

    executable = step_execution_tool(step)
    if executable is None:
        if isinstance(step, ResolvedCommandStep):
            executable = step.argv[0]
        else:
            return None

    if "/" not in executable and "\\" not in executable:
        binding = next((item for item in attempt.host_tools if item.name == executable), None)
        if binding is None:
            # Custom/test backends may deliberately omit external-capability binding.
            # Never compensate by reading the parent process PATH here.
            return None
        return PlanToolEvidence(
            name=binding.name,
            origin="host",
            sha256=binding.identity.sha256,
            byte_count=binding.identity.byte_count,
        )

    return _path_tool_evidence(executable, repository, step, managed=False)


def _path_tool_evidence(
    executable: str,
    repository: Path,
    step: ResolvedPlanStep,
    *,
    managed: bool,
) -> PlanToolEvidence | None:
    path = _resolve_executable_path(executable, repository, step)
    if path is None or not path.is_file():
        return None
    try:
        digest, byte_count = _hash_file(path)
    except OSError:
        return None
    return PlanToolEvidence(
        name=Path(executable).name or executable,
        origin="managed" if managed else _tool_origin(path, repository),
        sha256=digest,
        byte_count=byte_count,
    )


def _resolve_executable_path(
    executable: str, repository: Path, step: ResolvedPlanStep
) -> Path | None:
    value = Path(executable)
    if value.is_absolute():
        return value.resolve()
    if "/" not in executable and "\\" not in executable:
        # Plain host-tool names are owned by the attempt's frozen host-tool binding.
        # Evidence must never re-resolve the parent process PATH as a fallback.
        return None
    try:
        candidate = canonical_path(step_working_directory(repository, step) / value)
        RepositoryPathSpace.from_root(repository).relative(
            candidate, label=f"Fleet plan step {step.step_id} executable"
        )
        return candidate
    except FleetPlanInvalid:
        return None


def _hash_file(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def _tool_origin(path: Path, repository: Path) -> Literal["workspace", "host"]:
    try:
        RepositoryPathSpace.from_root(repository).relative(path, label="tool evidence path")
    except FleetPlanInvalid:
        return "host"
    return "workspace"


def _snapshot_evidence(identity: SnapshotIdentity) -> PlanSnapshotEvidence:
    return PlanSnapshotEvidence(
        sha256=identity.sha256,
        byte_count=identity.byte_count,
        entry_count=identity.entry_count,
    )


def _execution_authority_diagnostic(
    attempt: FleetPlanStepAttempt, outcome: StepOutcome
) -> PlanExecutionAuthorityDiagnosticEvidence:
    materialization = attempt.executable_materialization
    materialization_evidence = PlanExecutionAuthorityMaterializationEvidence(
        receipt_identity=materialization.receipt_identity,
        logical_sources=tuple(item.relative_path for item in materialization.logical_sources),
        package_executables=tuple(
            item.authority.bin_name for item in materialization.package_executables
        ),
        owned_scripts=tuple(item.relative_path for item in materialization.owned_scripts),
        direct_workspace_executable=(
            materialization.direct_workspace_executable.relative_path
            if materialization.direct_workspace_executable is not None
            else None
        ),
    )
    runtime = outcome.runtime_authority
    runtime_evidence = (
        PlanExecutionAuthorityRuntimeEvidence(
            receipt_identity=runtime.receipt_identity,
            executable_path=runtime.executable_path,
            executable_identity=runtime.executable_identity,
            path_authority_identity=runtime.path_authority_identity,
            environment_identity=runtime.environment_identity,
            provider_identity=runtime.provider_identity,
            runtime_identity=runtime.runtime_identity,
            interpreter_path=runtime.interpreter_path,
            interpreter_identity=runtime.interpreter_identity,
            execution_materialization_identity=runtime.execution_materialization_identity,
        )
        if runtime is not None
        else None
    )
    terminal_bytes = sum(
        item.terminal_proof_bytes
        for item in outcome.execution_authority_economics
        if item.scope == "runtime-launch"
    )
    if runtime is None or terminal_bytes <= 0:
        atomic = PlanExecutionAuthorityAtomicHandoffEvidence(
            state="NOT_OBSERVED",
            mechanism="not_observed",
            terminal_proof_bytes=0,
        )
    else:
        mechanism: Literal["remote_sealed_execution", "sealed_memfd_bind"] = (
            "remote_sealed_execution"
            if attempt.isolation_backend.remote_execution
            else "sealed_memfd_bind"
        )
        atomic = PlanExecutionAuthorityAtomicHandoffEvidence(
            state="PROVEN",
            mechanism=mechanism,
            terminal_proof_bytes=terminal_bytes,
        )
    return PlanExecutionAuthorityDiagnosticEvidence(
        materialization=materialization_evidence,
        runtime=runtime_evidence,
        atomic_handoff=atomic,
    )
