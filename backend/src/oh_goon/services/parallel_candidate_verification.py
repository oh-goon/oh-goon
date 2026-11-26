"""Read and export exact candidate evidence from an original parallel child Run."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Literal

from oh_goon.core.errors import FleetDomainError, FleetPlanInvalid, StateConflict
from oh_goon.core.file_digest import sha256_file_with_size
from oh_goon.core.models.branch_advance import is_branch_round_run_id
from oh_goon.core.models.candidate_workspace import CandidateRunInspection
from oh_goon.core.models.fleet_plan import ResolvedCommandStep
from oh_goon.core.models.fleet_plan_evidence import FleetPlanResult
from oh_goon.core.models.operation import FleetPlanOperationEvidence
from oh_goon.core.models.parallel_candidate_verification import (
    ParallelCandidateExport,
    ParallelCandidateVerification,
    ParallelCandidateVerifierObservation,
    is_baseline_verifier_step_id,
    is_parallel_verifier_step_id,
)
from oh_goon.core.models.run import FleetRunDetail
from oh_goon.core.models.target import FleetPlanTarget
from oh_goon.core.path_authority import RepositoryScopedPath
from oh_goon.core.product_identity import stable_id
from oh_goon.core.vocabulary.workspace import WorkspaceLifecycleState
from oh_goon.engine.git import GitService
from oh_goon.integrations.content_snapshot import SnapshotIdentity
from oh_goon.integrations.content_snapshot_proof import snapshot_metadata_fingerprint
from oh_goon.integrations.exact_file_write import publish_prepared_file
from oh_goon.integrations.fleet_content_store import FleetContentStore
from oh_goon.integrations.parallel_candidate_patch import (
    NativeCandidatePatch,
    prepare_native_candidate_patch,
)
from oh_goon.runtime.database import LocalDatabase
from oh_goon.runtime.execution_evidence_acceptance import ExecutionEvidenceAcceptanceAuthority
from oh_goon.runtime.parallel_agent_work import read_parallel_verifier, read_parallel_work
from oh_goon.runtime.workspaces import RepositoryWorkspaceAuthority
from oh_goon.services.fleet_plan_candidate import (
    inspect_candidate_run,
    require_branch_round_candidate,
    workspace_control_git,
)


@dataclass(frozen=True, slots=True)
class _CandidateVerificationRead:
    inspection: CandidateRunInspection
    verification: ParallelCandidateVerification
    observation: ParallelCandidateVerifierObservation | None = None


@dataclass(frozen=True, slots=True)
class VerifiedCandidatePatch:
    patch: NativeCandidatePatch
    verification: ParallelCandidateVerification
    source: SnapshotIdentity
    require_current: Callable[[], None]


def _incomplete(candidate: _CandidateVerificationRead, reason: str) -> _CandidateVerificationRead:
    return replace(
        candidate,
        verification=candidate.verification.model_copy(
            update={"outcome": "INCOMPLETE", "reason": reason}
        ),
    )


def _read_verification(
    detail: FleetRunDetail, *, database: LocalDatabase, git: GitService
) -> _CandidateVerificationRead:
    run_id = detail.run.fleet_run_id
    current = inspect_candidate_run(run_id, database=database, detail=detail)
    admission = current.admission
    work_id = admission.parallel_work_id
    branch = None
    if work_id is None:
        if not is_branch_round_run_id(run_id) or len(detail.run.targets) != 1:
            raise FleetPlanInvalid("Candidate has no original frozen verifier")
        branch = require_branch_round_candidate(database, admission, detail.run.targets[0].path)
        work_id = branch.preparation.verification.parallel_work_id
    else:
        work = read_parallel_work(database, work_id)
        if (
            work.intent.source != admission.source
            or work.intent.repository_id != admission.workspace.repository_id
            or work.intent.base_revision != admission.workspace.base_revision
            or run_id not in {child.fleet_run_id for child in work.intent.children}
        ):
            raise StateConflict("Candidate does not belong to its original parallel work")
    verifier = read_parallel_verifier(database, work_id)
    if branch is not None and verifier.identity != branch.preparation.verification.verifier_id:
        raise StateConflict("Writable candidate lost its original frozen verifier")
    candidate = _CandidateVerificationRead(
        current,
        ParallelCandidateVerification(
            fleet_run_id=run_id,
            parallel_work_id=work_id,
            workspace_id=admission.workspace.workspace_id,
            base_revision=admission.workspace.base_revision,
            base_source_id=admission.source.authority_id,
            verifier_id=verifier.identity,
            outcome="INCOMPLETE",
            reason="Verifier Step has no accepted result",
        ),
    )
    target = detail.run.target
    if not isinstance(target, FleetPlanTarget):
        return candidate
    expected = target.plan.steps[-1]
    if (
        not isinstance(expected, ResolvedCommandStep)
        or not is_parallel_verifier_step_id(expected.step_id)
        or expected.argv != verifier.argv
        or not detail.run.status.terminal
    ):
        return candidate
    operations = tuple(
        operation
        for operation in detail.operations
        if operation.target == target
        and operation.provenance.fleet_run_id == run_id
        and operation.repository.repository_id == admission.workspace.repository_id
        and isinstance(operation.evidence, FleetPlanOperationEvidence)
        and operation.evidence.candidate_receipt is not None
    )
    if len(operations) != 1:
        return candidate
    operation = operations[0]
    evidence = operation.evidence
    assert isinstance(evidence, FleetPlanOperationEvidence)
    if branch is not None:
        baseline = evidence.plan.steps[0] if evidence.plan.steps else None
        source = admission.source.source_snapshot
        observation = (
            baseline.attempts[0].parallel_verifier
            if baseline is not None and len(baseline.attempts) == 1
            else None
        )
        if (
            baseline is None
            or not is_baseline_verifier_step_id(baseline.step_id)
            or baseline.argv != verifier.argv
            or baseline.settlement.outcome != "SUCCESS"
            or baseline.settlement.cleanup != "VERIFIED"
            or observation is None
            or observation.verifier_id != verifier.identity
            or observation.postcondition != "UNCHANGED"
            or (observation.source_sha256, observation.byte_count, observation.entry_count)
            != (source.sha256, source.byte_count, source.entry_count)
        ):
            return _incomplete(
                candidate, "Receiving source did not pass its frozen baseline verifier"
            )
    verifications = tuple(
        step
        for step in evidence.plan.steps
        if step.kind == "command" and step.step_id == expected.step_id
    )
    if len(verifications) != 1:
        return candidate
    step = verifications[0]
    if (
        step.argv != verifier.argv
        or len(step.attempts) != 1
        or step.settlement.cleanup != "VERIFIED"
        or step.settlement.reason not in {"ACCEPTED_EXIT", "FAILED_EXIT"}
    ):
        return candidate
    attempt = step.attempts[0]
    observation = attempt.parallel_verifier
    isolation = attempt.standard_isolation
    if (
        observation is None
        or observation.verifier_id != verifier.identity
        or attempt.argv != verifier.argv
        or attempt.physical_execution_id is None
        or attempt.supervisor is None
        or attempt.supervisor.final_process_group_quiescent is not True
        or isolation is None
        or isolation.filesystem_cleanup != "verified"
        or isolation.workspace_writer_scope is None
    ):
        return candidate
    native_outcome: Literal["PASS", "FAIL"] = (
        "PASS" if step.settlement.outcome == "SUCCESS" else "FAIL"
    )
    candidate = replace(
        candidate,
        verification=candidate.verification.model_copy(
            update={
                "native_outcome": native_outcome,
                "physical_execution_id": attempt.physical_execution_id,
                "source_sha256": observation.source_sha256,
            }
        ),
        observation=observation,
    )
    receipt = current.receipt
    checkpoint = current.checkpoint
    if (
        current.path is None
        or receipt is None
        or receipt != evidence.candidate_receipt
        or receipt.settlement != "checkpointed"
        or receipt.workspace != admission.workspace
        or receipt.assignment.attempt_id != operation.provenance.operation_id
        or receipt.assignment.input_checkpoint_id != admission.checkpoint.checkpoint_id
        or receipt.checkpoint != checkpoint
        or receipt.acceptance is None
        or checkpoint is None
        or checkpoint.parent_checkpoint_id != admission.checkpoint.checkpoint_id
        or current.state != receipt.state
        or current.state.lifecycle_state is not WorkspaceLifecycleState.READY
    ):
        return _incomplete(candidate, "Candidate settlement is missing or stale")
    result = FleetPlanResult(plan_id=evidence.plan.plan_id, steps=evidence.plan.steps)
    result_identity = stable_id("candidate-run-result", result.model_dump(mode="json"))
    workspaces = RepositoryWorkspaceAuthority(database, initialize=False)
    try:
        durable_acceptance = ExecutionEvidenceAcceptanceAuthority(
            database, workspaces=workspaces
        ).acceptance(receipt.acceptance.acceptance_id)
        if (
            durable_acceptance != receipt.acceptance
            or durable_acceptance.result_identity != result_identity
            or isolation.workspace_writer_scope.materialization != receipt.materialization
            or isolation.workspace_writer_scope.physical_execution_id
            != attempt.physical_execution_id
        ):
            return _incomplete(candidate, "Verifier result differs from original accepted evidence")
        if (
            observation.postcondition != "UNCHANGED"
            or (observation.source_sha256, observation.byte_count, observation.entry_count)
            != (checkpoint.source_sha256, checkpoint.byte_count, checkpoint.entry_count)
            or workspaces.current_candidate_checkpoint(admission.workspace, git=git) != checkpoint
            or snapshot_metadata_fingerprint(Path(current.path))
            != observation.workspace_fingerprint
            or workspaces.state(admission.workspace.workspace_id) != current.state
        ):
            return _incomplete(
                candidate, "Candidate differs from the source observed by the verifier"
            )
    except (OSError, FleetDomainError, ValueError):
        return _incomplete(candidate, "Candidate or verification authority cannot be re-proved")
    return replace(
        candidate,
        verification=candidate.verification.model_copy(
            update={
                "checkpoint_id": checkpoint.checkpoint_id,
                "result_identity": result_identity,
                "outcome": native_outcome,
                "reason": "Candidate passed its frozen verifier"
                if native_outcome == "PASS"
                else "Candidate failed its frozen verifier",
            }
        ),
    )


def verify_parallel_candidate(
    detail: FleetRunDetail, *, database: LocalDatabase, git: GitService | None = None
) -> ParallelCandidateVerification:
    """Read original verification evidence and re-prove its current candidate bytes."""
    return _read_verification(
        detail, database=database, git=workspace_control_git(git)
    ).verification


def _require_verified_candidate(
    detail: FleetRunDetail,
    *,
    database: LocalDatabase,
    checkpoint_id: str,
    git: GitService,
) -> _CandidateVerificationRead:
    selected = _read_verification(detail, database=database, git=git)
    if (
        selected.verification.outcome != "PASS"
        or selected.verification.checkpoint_id != checkpoint_id
        or selected.observation is None
        or selected.inspection.checkpoint is None
        or selected.inspection.path is None
    ):
        raise FleetPlanInvalid("Select the current exact verified candidate checkpoint")
    return selected


def _prepare_verified_patch(
    selected: _CandidateVerificationRead,
    *,
    database: LocalDatabase,
    scratch: Path,
    git: GitService,
) -> VerifiedCandidatePatch:
    current, observation = selected.inspection, selected.observation
    checkpoint = current.checkpoint
    assert checkpoint is not None and observation is not None and current.path is not None
    admission = current.admission
    workspaces = RepositoryWorkspaceAuthority(database, initialize=False)
    base = workspaces.read_snapshot_binding(admission.source_workspace)
    if (base.identity.sha256, base.identity.byte_count, base.identity.entry_count) != (
        admission.checkpoint.source_sha256,
        admission.checkpoint.byte_count,
        admission.checkpoint.entry_count,
    ):
        raise StateConflict("Candidate base differs from the original admitted source")
    path = Path(current.path)
    source = SnapshotIdentity(
        checkpoint.source_sha256, checkpoint.byte_count, checkpoint.entry_count
    )

    def require_current() -> None:
        if (
            workspaces.current_candidate_checkpoint(admission.workspace, git=git) != checkpoint
            or snapshot_metadata_fingerprint(path) != observation.workspace_fingerprint
            or workspaces.state(admission.workspace.workspace_id) != current.state
        ):
            raise StateConflict("Candidate changed while preparing its verified patch")

    patch = prepare_native_candidate_patch(
        git,
        original=path,
        original_revision=admission.workspace.base_revision,
        base_snapshot=base.snapshot_path,
        base_identity=base.identity,
        candidate_identity=source,
        scratch=scratch,
    )
    require_current()
    return VerifiedCandidatePatch(patch, selected.verification, source, require_current)


def prepare_verified_candidate_patch(
    detail: FleetRunDetail,
    *,
    database: LocalDatabase,
    checkpoint_id: str,
    scratch: Path,
    git: GitService,
) -> VerifiedCandidatePatch:
    """Prepare the same verified native patch used for operator export."""
    selected = _require_verified_candidate(
        detail, database=database, checkpoint_id=checkpoint_id, git=git
    )
    return _prepare_verified_patch(selected, database=database, scratch=scratch, git=git)


def export_parallel_candidate(
    detail: FleetRunDetail,
    *,
    database: LocalDatabase,
    content_store: FleetContentStore,
    checkpoint_id: str,
    destination: Path,
    git: GitService | None = None,
) -> ParallelCandidateExport:
    """Publish an exact verified patch without touching the developer checkout."""
    control = workspace_control_git(git)
    selected = _require_verified_candidate(
        detail, database=database, checkpoint_id=checkpoint_id, git=control
    )
    current = selected.inspection
    assert current.path is not None
    target = destination.expanduser().absolute()
    path = Path(current.path)
    if (
        ".." in target.parts
        or target.exists()
        or not target.parent.is_dir()
        or any(part.is_symlink() for part in (target, *target.parents))
        or any(
            target.is_relative_to(root)
            for root in (
                path,
                database.path.parent,
                *(op.repository.path for op in detail.operations),
            )
        )
    ):
        raise FleetPlanInvalid("Export requires a new file outside the source and execution state")
    cleanup: Literal["VERIFIED", "UNVERIFIED"] = "VERIFIED"
    temporary = TemporaryDirectory(prefix=".oh-goon-candidate-export-", dir=target.parent)
    owner = current.admission.workspace.repository_id
    retained = None
    try:
        stage = Path(temporary.name)
        verified = _prepare_verified_patch(selected, database=database, scratch=stage, git=control)
        retained = content_store.put_file(
            RepositoryScopedPath.create(
                owner_repository_id=owner,
                root=stage,
                path=verified.patch.path,
                label="verified candidate patch",
            )
        )
        artifact = stage / "artifact"
        content_store.materialize_file(
            retained.reference_id,
            RepositoryScopedPath.create(
                owner_repository_id=owner, root=stage, path=artifact, label="candidate patch export"
            ),
            expected_identity=retained.identity,
        )
        patch_sha256, patch_size = sha256_file_with_size(artifact)
        verified.require_current()
        publish_prepared_file(artifact, target)
    finally:
        if retained is not None:
            try:
                content_store.release_reference(retained.reference_id, owner_repository_id=owner)
            except (OSError, FleetDomainError, ValueError):
                cleanup = "UNVERIFIED"
        try:
            temporary.cleanup()
        except OSError:
            cleanup = "UNVERIFIED"
    assert retained is not None
    return ParallelCandidateExport(
        verification=verified.verification,
        destination=str(target),
        sha256=patch_sha256,
        byte_count=patch_size,
        changed_paths=verified.patch.changed_paths,
        cleanup=cleanup,
    )
