"""Explicit candidate preparation and settlement through existing workspace owners."""

from __future__ import annotations

import math
import os
import stat
import sys
from collections.abc import Callable, Mapping
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from oh_goon.core.errors import FleetPlanInvalid, StateConflict, WorkspaceFenceUnproven
from oh_goon.core.models.agent_providers import ResolvedNativeAgent
from oh_goon.core.models.agent_source import MAX_REPOSITORY_BYTES, MAX_SOURCE_ENTRIES
from oh_goon.core.models.candidate_execution import CandidateWorkspaceAdmission
from oh_goon.core.models.candidate_execution_receipt import CandidateExecutionReceipt
from oh_goon.core.models.candidate_workspace import CandidateRunInspection
from oh_goon.core.models.effect_attempt import ClaimedRepositoryEffectAttempt
from oh_goon.core.models.fleet_plan import DEFAULT_COMMAND_TIMEOUT_SECONDS, ResolvedCommandStep
from oh_goon.core.models.fleet_plan_evidence import FleetPlanResult, PlanSnapshotEvidence
from oh_goon.core.models.operation import FleetPlanOperationEvidence
from oh_goon.core.models.parallel_candidate_verification import (
    is_baseline_verifier_step_id,
    is_parallel_verifier_step_id,
)
from oh_goon.core.models.provenance import RepositoryStateFingerprint
from oh_goon.core.models.run import FleetRunDetail
from oh_goon.core.models.source_authority import RepositorySourceAuthority
from oh_goon.core.models.target import FleetPlanTarget
from oh_goon.core.models.workspace import WorkspaceRef
from oh_goon.core.models.workspace_lease import WorkspaceLease
from oh_goon.core.product_identity import repository_subject_instance_id, stable_id
from oh_goon.core.vocabulary.workspace import WorkspaceKind, WorkspaceLifecycleState
from oh_goon.engine.git import (
    GitService,
    WorkspaceMetadataGitCommandRunner,
    require_workspace_metadata_git_environment,
)
from oh_goon.integrations.content_snapshot import SnapshotIdentity
from oh_goon.integrations.fleet_plan_native_steps import require_native_agent_compatibility
from oh_goon.integrations.fleet_plan_process_isolation import NativeProfileCommandRunner
from oh_goon.integrations.parallel_candidate_verifier import require_verifier_executable
from oh_goon.runtime.database import LocalDatabase
from oh_goon.runtime.execution_assignments import ExecutionAssignmentAuthority
from oh_goon.runtime.execution_evidence_acceptance import ExecutionEvidenceAcceptanceAuthority
from oh_goon.runtime.execution_materializations import ExecutionMaterializationAuthority
from oh_goon.runtime.journal import Journal
from oh_goon.runtime.parallel_agent_work import (
    read_parallel_verifier,
    reserve_parallel_candidate_workspaces,
)
from oh_goon.runtime.workspace_leases import NativeWorkspaceQuiescence, WorkspaceLeaseStore
from oh_goon.runtime.workspaces import RepositoryWorkspaceAuthority, _worktree_git_environment
from oh_goon.services.agent_authoring_sources import require_native_agent_source_path_privacy
from oh_goon.services.repositories import RepositoryService

if TYPE_CHECKING:
    from oh_goon.core.models.branch_advance import BranchAdvanceReceipt


def require_branch_round_candidate(
    database: LocalDatabase, admission: CandidateWorkspaceAdmission, repository: Path
) -> BranchAdvanceReceipt:
    from oh_goon.runtime.branch_advances import BranchAdvanceStore

    receipt = BranchAdvanceStore(database).find_by_next_run(admission.fleet_run_id)
    if receipt is None or receipt.published_at is None or receipt.next_run is None:
        raise StateConflict("Writable round lacks its published branch advancement")
    prepared = receipt.preparation
    captured = admission.source.source_snapshot
    if (
        admission.parallel_work_id is not None
        or prepared.repository_path != repository
        or prepared.repository_id != admission.workspace.repository_id
        or receipt.next_run.repository_subject_id != admission.source.subject_instance_id
        or prepared.commit != admission.workspace.base_revision
        or (
            prepared.verification.source_sha256,
            prepared.source_byte_count,
            prepared.source_entry_count,
        )
        != (captured.sha256, captured.byte_count, captured.entry_count)
    ):
        raise StateConflict("Writable round differs from its verified receiving source")
    return receipt


def require_current_branch_round_tip(receipt: BranchAdvanceReceipt, *, git: GitService) -> None:
    from oh_goon.integrations.parallel_branch_advance import (
        PreparedBranchAdvance,
        require_published_branch_advance,
    )

    prepared = receipt.preparation
    owner = prepared.branch_owner_path
    with RepositoryWorkspaceAuthority.pin_repository_subject(owner) as require_current:
        if repository_subject_instance_id(owner) != prepared.branch_owner_subject_id:
            raise StateConflict("Writable round's native branch owner was replaced")
        require_published_branch_advance(
            git,
            repository=owner,
            prepared=PreparedBranchAdvance(
                prepared.branch_ref, prepared.expected_tip, prepared.commit, prepared.tree
            ),
            require_current=require_current,
        )


def workspace_control_git(git: GitService | None = None) -> GitService:
    selected = git or GitService()
    runner = selected.command_runner
    if type(runner) is WorkspaceMetadataGitCommandRunner:
        capture_workspace_control_git_environment(selected)
        return selected
    reader = selected.observation_runner
    if (runner is not None and not isinstance(runner, NativeProfileCommandRunner)) or (
        reader is not None
        and not isinstance(reader, NativeProfileCommandRunner)
        and not (
            isinstance(reader, partial) and isinstance(reader.func, NativeProfileCommandRunner)
        )
    ):
        raise FleetPlanInvalid(
            "Candidate metadata does not support a custom Git execution provider"
        )
    return restore_workspace_control_git(
        executable=selected.executable,
        default_timeout_seconds=selected.default_timeout_seconds,
        environment=_worktree_git_environment(),
    )


def _require_workspace_control_git_environment(environment: object) -> dict[str, str]:
    try:
        return require_workspace_metadata_git_environment(environment)
    except ValueError:
        raise FleetPlanInvalid(
            "Candidate Git metadata environment differs from its original policy"
        ) from None


def capture_workspace_control_git_environment(git: GitService) -> dict[str, str] | None:
    """Capture only the exact canonical metadata owner, without another discovery."""
    runner = git.command_runner
    if type(runner) is not WorkspaceMetadataGitCommandRunner:
        return None
    if git.observation_runner is not runner or git.metadata_only is not True:
        raise FleetPlanInvalid("Candidate Git observation policy differs from control policy")
    assert isinstance(runner, WorkspaceMetadataGitCommandRunner)
    return _require_workspace_control_git_environment(runner.environment)


def restore_workspace_control_git(
    *, executable: str, default_timeout_seconds: float, environment: Mapping[str, str]
) -> GitService:
    """Restore the transported owner without reading ambient PATH/configuration."""
    command = WorkspaceMetadataGitCommandRunner(
        tuple(_require_workspace_control_git_environment(environment).items())
    )
    return GitService(
        executable=executable,
        default_timeout_seconds=default_timeout_seconds,
        command_runner=command,
        observation_runner=command,
        metadata_only=True,
    )


def require_native_candidate_selection(selected: ResolvedNativeAgent) -> None:
    """The first candidate mode uses the existing managed, headless Pi boundary."""
    if (
        sys.platform != "linux"
        or selected.agent != "pi"
        or selected.interactive
        or selected.profile.backend != "bubblewrap"
        or selected.profile.sandbox_owner != "harness"
        or selected.connection is None
        or selected.profile.inference_profile is None
        or selected.definition.arguments
        or selected.definition.configuration_directory is not None
    ):
        raise FleetPlanInvalid(
            "Candidate execution requires noninteractive Pi with harness-owned Bubblewrap "
            "and explicitly selected managed inference on Linux"
        )
    require_native_agent_compatibility(selected, role="report", terminal_available=False)


def _release_unstarted_workspace(
    workspaces: RepositoryWorkspaceAuthority, workspace: WorkspaceRef, git: GitService
) -> None:
    if workspaces.reference(workspace.workspace_id) != workspace:
        raise StateConflict("Candidate cleanup differs from its durable workspace identity")
    state = workspaces.state(workspace.workspace_id)
    if state.lifecycle_state is WorkspaceLifecycleState.RELEASED:
        return
    if (
        workspace.kind is WorkspaceKind.WORKTREE
        and state.lifecycle_state is WorkspaceLifecycleState.READY
    ):
        state = workspaces.transition(
            workspace.workspace_id,
            expected_state_version=state.state_version,
            next_state=WorkspaceLifecycleState.FINALIZED,
        )
        workspaces.retire_worktree(workspace, expected_state_version=state.state_version, git=git)
    else:
        workspaces.release(workspace.workspace_id, expected_state_version=state.state_version)


def prepare_candidate_workspace(
    *,
    database: LocalDatabase,
    repositories: RepositoryService,
    repository_id: str,
    fleet_run_id: str,
    git: GitService | None = None,
    expected_source: RepositorySourceAuthority | None = None,
    expected_subject: str | None = None,
    expected_base_revision: str | None = None,
    expected_source_branch: str | None = None,
    parallel_work_id: str | None = None,
    validate_candidate_path: Callable[[Path], None] | None = None,
) -> CandidateWorkspaceAdmission:
    """Capture one registered clean source and materialize an independently owned candidate."""
    if not fleet_run_id:
        raise FleetPlanInvalid("Candidate preparation requires its proposed Run identity")
    repository = repositories.require_included(repository_id)
    control = workspace_control_git(git)
    workspaces = RepositoryWorkspaceAuthority(database)
    source_workspace = candidate = None
    try:
        with workspaces.pin_repository_subject(repository.path) as require_source_current:
            subject = repository_subject_instance_id(repository.path)
            if expected_subject is not None and expected_subject != subject:
                raise StateConflict(
                    "Registered candidate source subject changed before preparation"
                )
            require_native_agent_source_path_privacy(repository.path, Path("."), True)
            if control.root(repository.path) != repository.path:
                raise FleetPlanInvalid(
                    "Candidate source requires its registered canonical Git root"
                )
            observation = control.observe(repository.path)
            observation.require_repository()
            if observation.head_sha is None or observation.changed_files:
                raise FleetPlanInvalid("Candidate source must be committed and clean")
            if (
                expected_base_revision is not None
                and observation.head_sha != expected_base_revision
            ):
                raise StateConflict("Registered candidate base changed before preparation")
            if expected_source_branch is not None and observation.branch != expected_source_branch:
                raise StateConflict("Registered candidate branch changed before preparation")
            if parallel_work_id is None:
                candidate = workspaces.request(
                    repository_id=repository.repository_id,
                    base_revision=observation.head_sha,
                    kind=WorkspaceKind.WORKTREE,
                )
            else:
                candidate, source_workspace = reserve_parallel_candidate_workspaces(
                    database,
                    workspaces,
                    work_id=parallel_work_id,
                    fleet_run_id=fleet_run_id,
                    repository_id=repository.repository_id,
                    base_revision=observation.head_sha,
                    expected_source=expected_source,
                )
            if validate_candidate_path is not None:
                validate_candidate_path(workspaces.physical_path(candidate))
            if source_workspace is None:
                source_workspace = workspaces.request(
                    repository_id=repository.repository_id,
                    base_revision=observation.head_sha,
                    kind=WorkspaceKind.READ_SNAPSHOT,
                )
            allocated = workspaces.allocate(source_workspace.workspace_id, expected_state_version=0)
            _, captured = workspaces.materialize_read_snapshot(
                source_workspace,
                repository.path,
                expected_state_version=allocated.state_version,
                source_path_validator=require_native_agent_source_path_privacy,
                max_bytes=MAX_REPOSITORY_BYTES,
                max_entries=MAX_SOURCE_ENTRIES,
            )
            source = RepositorySourceAuthority.create(
                subject_instance_id=subject,
                source_snapshot=PlanSnapshotEvidence(
                    sha256=captured.identity.sha256,
                    byte_count=captured.identity.byte_count,
                    entry_count=captured.identity.entry_count,
                ),
            )
            if expected_source is not None and expected_source != source:
                raise StateConflict("Registered candidate source changed since review")
            allocated = workspaces.allocate(candidate.workspace_id, expected_state_version=0)
            ready, checkpoint = workspaces.materialize_worktree(
                candidate,
                repository.path,
                captured,
                expected_state_version=allocated.state_version,
                git=control,
                expected_source_branch=expected_source_branch,
            )
            require_source_current()
            if repository_subject_instance_id(repository.path) != subject:
                raise StateConflict(
                    "Registered candidate source subject changed during preparation"
                )
            physical = workspaces.physical_path(candidate).lstat()
            return CandidateWorkspaceAdmission(
                fleet_run_id=fleet_run_id,
                parallel_work_id=parallel_work_id,
                source=source,
                source_workspace=source_workspace,
                workspace=candidate,
                checkpoint=checkpoint,
                state_version=ready.state_version,
                workspace_device=physical.st_dev,
                workspace_inode=physical.st_ino,
                workspace_mode=physical.st_mode,
                workspace_ctime_ns=physical.st_ctime_ns,
            )
    except BaseException as failure:
        # Only receipt-proven unused allocations may be removed. Ambiguous Git
        # publication remains with its original workspace reconciliation owner.
        try:
            if candidate is not None:
                _release_unstarted_workspace(workspaces, candidate, control)
            if source_workspace is not None:
                _release_unstarted_workspace(workspaces, source_workspace, control)
        except Exception as cleanup:
            failure.add_note(
                f"Candidate preparation cleanup is incomplete: {type(cleanup).__name__}"
            )
        raise


def release_unadmitted_candidate(
    admission: CandidateWorkspaceAdmission,
    *,
    database: LocalDatabase,
    fleet_run_id: str,
    git: GitService | None = None,
) -> None:
    admission = CandidateWorkspaceAdmission.model_validate(admission.model_dump(mode="json"))
    if admission.fleet_run_id != fleet_run_id:
        raise StateConflict("Candidate cleanup belongs to another proposed Run")
    view = Journal(database).read_view()
    detail = view.run_detail(fleet_run_id)
    if detail is not None and isinstance(detail.run.target, FleetPlanTarget):
        if detail.run.target.candidate_workspace == admission:
            return
    owned_ids = {admission.workspace.workspace_id, admission.source_workspace.workspace_id}
    for recorded in view.run_details():
        target = recorded.run.target
        if isinstance(target, FleetPlanTarget) and target.candidate_workspace is not None:
            recorded_ids = {
                target.candidate_workspace.workspace.workspace_id,
                target.candidate_workspace.source_workspace.workspace_id,
            }
            if owned_ids & recorded_ids:
                raise StateConflict("Candidate cleanup cannot remove another admitted Run's data")
    workspaces = RepositoryWorkspaceAuthority(database)
    control = workspace_control_git(git)
    _release_unstarted_workspace(workspaces, admission.workspace, control)
    _release_unstarted_workspace(workspaces, admission.source_workspace, control)


class CandidateExecution:
    """One claimed effect's original workspace lease and retained root handles."""

    def __init__(
        self,
        admission: CandidateWorkspaceAdmission,
        *,
        database: LocalDatabase,
        effect: ClaimedRepositoryEffectAttempt,
        require_execution_current: Callable[[], None],
        require_settlement_current: Callable[[], None] | None = None,
        git: GitService | None = None,
    ) -> None:
        self._admission = admission
        self._database = database
        self._effect = effect
        self._require_execution_current = require_execution_current
        self._require_settlement_current = require_settlement_current or require_execution_current
        self._workspaces = RepositoryWorkspaceAuthority(database)
        self._assignments = ExecutionAssignmentAuthority(database, self._workspaces)
        self._leases = WorkspaceLeaseStore(
            database, workspaces=self._workspaces, assignments=self._assignments
        )
        self._materializations = ExecutionMaterializationAuthority(
            database,
            workspaces=self._workspaces,
            assignments=self._assignments,
            leases=self._leases,
        )
        self._control = workspace_control_git(git)
        self._writer_started = False
        self._closed = False
        self._receipt: CandidateExecutionReceipt | None = None
        target = effect.target
        require_execution_current()
        if (
            not isinstance(target, FleetPlanTarget)
            or target.candidate_workspace != admission
            or target.reviewed_repository_source != admission.source
            or effect.fleet_run_id != admission.fleet_run_id
            or effect.repository.repository_id != admission.workspace.repository_id
            or target.plan.native_agent is None
        ):
            raise StateConflict("Candidate execution differs from original admitted Run authority")
        require_native_candidate_selection(target.plan.native_agent)
        from oh_goon.core.models.branch_advance import is_branch_round_run_id

        branch = (
            require_branch_round_candidate(database, admission, effect.repository.path)
            if is_branch_round_run_id(admission.fleet_run_id)
            else None
        )
        if branch is not None:
            require_current_branch_round_tip(branch, git=self._control)
        work_id = (
            branch.preparation.verification.parallel_work_id
            if branch is not None
            else admission.parallel_work_id
        )
        self.verifier = read_parallel_verifier(database, work_id) if work_id is not None else None
        if self.verifier is not None:
            step = target.plan.steps[-1]
            if (
                not isinstance(step, ResolvedCommandStep)
                or not is_parallel_verifier_step_id(step.step_id)
                or step.argv != self.verifier.argv
            ):
                raise StateConflict("Candidate Step differs from its admitted verifier")
            if branch is not None:
                baseline = target.plan.steps[0]
                if (
                    self.verifier.identity != branch.preparation.verification.verifier_id
                    or not isinstance(baseline, ResolvedCommandStep)
                    or not is_baseline_verifier_step_id(baseline.step_id)
                    or baseline.argv != self.verifier.argv
                ):
                    raise StateConflict("Writable baseline Step differs from its admitted verifier")
            require_verifier_executable(self.verifier)
        backend_configuration_id = target.plan.native_agent.backend_configuration_id
        if backend_configuration_id is None:
            raise FleetPlanInvalid("Candidate execution requires its resolved native backend")
        self._backend_configuration_id = backend_configuration_id
        self.path = self._workspaces.physical_path(admission.workspace)
        self.workspace_directory_fd = os.open(
            self.path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
        )
        self._git_fd: int | None = None
        try:
            physical = os.fstat(self.workspace_directory_fd)
            if (physical.st_dev, physical.st_ino, physical.st_mode, physical.st_ctime_ns) != (
                admission.workspace_device,
                admission.workspace_inode,
                admission.workspace_mode,
                admission.workspace_ctime_ns,
            ) or not stat.S_ISDIR(physical.st_mode):
                raise StateConflict("Candidate physical root changed before worker preparation")
            self._git_fd = os.open(self.path / ".git", os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            self._git_initial = os.fstat(self._git_fd)
            state = self._workspaces.state(admission.workspace.workspace_id)
            if (
                state.state_version != admission.state_version
                or state.lifecycle_state is not WorkspaceLifecycleState.READY
            ):
                raise StateConflict("Candidate workspace state changed before worker preparation")
            checkpoint = self._workspaces.current_candidate_checkpoint(
                admission.workspace, git=self._control
            )
            if checkpoint != admission.checkpoint:
                raise StateConflict("Candidate checkpoint changed before worker preparation")
            captured = self._workspaces.read_snapshot_binding(admission.source_workspace)
            if captured.identity != SnapshotIdentity(
                admission.source.source_snapshot.sha256,
                admission.source.source_snapshot.byte_count,
                admission.source.source_snapshot.entry_count,
            ):
                raise StateConflict("Candidate retained source differs from admitted capture")
            budget = math.ceil(
                sum(
                    step.timeout_seconds or DEFAULT_COMMAND_TIMEOUT_SECONDS
                    for step in target.plan.steps
                )
            )
            if not 1 <= budget <= 86400:
                raise FleetPlanInvalid(
                    "Candidate Run exceeds its existing one-day workspace lease bound"
                )
            self.assignment = self._assignments.issue_candidate_mutation(
                admission.workspace,
                attempt_id=effect.operation_id,
                expected_checkpoint_id=checkpoint.checkpoint_id,
                wall_time_seconds=budget,
                isolation_requirements=("private_filesystem", "private_pid_namespace"),
                input_artifact_ids=(admission.source.authority_id, target.execution_digest),
            )
            self._state, self.lease = self._leases.acquire(
                admission.workspace,
                self.assignment,
                executor_id=database.session_id,
                executor_generation=effect.record_version + 1,
                expected_state_version=state.state_version,
                ttl_seconds=86400,
            )
            self.materialization = self._materializations.materialize(
                admission.workspace,
                self.assignment,
                self.lease,
                writer_scope_kind="native_namespace_scope",
                workspace_directory_fd=self.workspace_directory_fd,
            )
            self.require_current()
        except BaseException as failure:
            if hasattr(self, "lease"):
                try:
                    self._require_settlement_current()
                    self._state = self._leases.release_unmaterialized(
                        self.lease, expected_state_version=self._state.state_version
                    )
                except Exception as cleanup:
                    failure.add_note(
                        "Candidate preparation lease retained after unresolved cleanup: "
                        f"{type(cleanup).__name__}"
                    )
            if self._git_fd is not None:
                os.close(self._git_fd)
            os.close(self.workspace_directory_fd)
            self._closed = True
            raise

    def require_current(self) -> None:
        self._validate_current(self._require_execution_current)

    def _validate_current(self, require_owned: Callable[[], None]) -> None:
        require_owned()
        if self._closed:
            raise StateConflict("Candidate execution root handles are closed")
        if (
            self._workspaces.reference(self._admission.workspace.workspace_id)
            != self._admission.workspace
        ):
            raise StateConflict("Candidate durable workspace identity changed")
        if self._assignments.assignment(self.assignment.assignment_id) != self.assignment:
            raise StateConflict("Candidate assignment authority changed")
        self._leases.assert_current(self.lease)
        if (
            self._materializations.materialization(self.materialization.materialization_id)
            != self.materialization
        ):
            raise StateConflict("Candidate materialization authority changed")
        state = self._workspaces.state(self._admission.workspace.workspace_id)
        if (
            state != self._state
            or state.current_checkpoint_id != self.assignment.input_checkpoint_id
        ):
            raise StateConflict("Candidate lease/checkpoint state changed before effect")
        held = os.fstat(self.workspace_directory_fd)
        current = self.path.lstat()
        if (current.st_dev, current.st_ino, current.st_mode) != (
            held.st_dev,
            held.st_ino,
            held.st_mode,
        ) or (held.st_dev, held.st_ino, held.st_mode) != (
            self.materialization.workspace_device,
            self.materialization.workspace_inode,
            self.materialization.workspace_mode,
        ):
            raise StateConflict("Candidate physical root was replaced")
        assert self._git_fd is not None
        git = os.fstat(self._git_fd)
        marker = (self.path / ".git").lstat()
        if (marker.st_dev, marker.st_ino, marker.st_mode, marker.st_ctime_ns) != (
            git.st_dev,
            git.st_ino,
            git.st_mode,
            self._git_initial.st_ctime_ns,
        ):
            raise StateConflict("Candidate Git authority was replaced")
        if (
            not self._writer_started
            and self._workspaces.current_candidate_checkpoint(
                self._admission.workspace, git=self._control
            )
            != self._admission.checkpoint
        ):
            raise StateConflict("Candidate input source changed before first effect")

    def mark_writer_started(self) -> None:
        # The native scope is already durable in the materialization before
        # birth; this is the original receiver's last-use validation, not an ACK.
        self.require_current()
        self._writer_started = True

    def observe_subject(self) -> RepositoryStateFingerprint:
        return self._control.observe(self._effect.repository.path).fingerprint

    def settle(self, result: FleetPlanResult) -> CandidateExecutionReceipt:
        if self._receipt is not None:
            return self._receipt
        checkpoint = acceptance = None
        reason = None
        settlement: Literal["checkpointed", "retained_incomplete"] = "retained_incomplete"
        try:
            self._validate_current(self._require_settlement_current)
            target = self._effect.target
            assert isinstance(target, FleetPlanTarget)
            if result.plan_id != target.plan.plan_id or tuple(
                (step.step_id, step.kind) for step in result.steps
            ) != tuple((step.step_id, step.kind) for step in target.plan.steps):
                raise WorkspaceFenceUnproven(
                    "Candidate result has incomplete original Step membership"
                )
            scopes = []
            physical = []
            for step in result.steps:
                if step.status == "SKIPPED" and not step.attempts:
                    continue
                if step.settlement.cleanup != "VERIFIED" or not step.attempts:
                    raise WorkspaceFenceUnproven("Candidate Step cleanup is unavailable")
                for attempt in step.attempts:
                    if (
                        attempt.physical_execution_id is None
                        or attempt.standard_isolation is None
                        or attempt.standard_isolation.workspace_writer_scope is None
                    ):
                        raise WorkspaceFenceUnproven(
                            "Candidate native namespace receipt is unavailable"
                        )
                    physical.append(attempt.physical_execution_id)
                    scopes.append(attempt.standard_isolation.workspace_writer_scope)
            current_run = (
                Journal(self._database)
                .read_view()
                .required_run_detail(self._admission.fleet_run_id)
            )
            canonical_physical = self._workspaces.native_writer_launch_ids(
                current_run, self.materialization
            )
            if len(set(canonical_physical)) != len(canonical_physical) or set(
                canonical_physical
            ) != set(physical):
                raise WorkspaceFenceUnproven(
                    "Candidate result omitted original scoped writer launches"
                )
            proof = NativeWorkspaceQuiescence(
                tuple(scopes), tuple(physical), self._backend_configuration_id
            )
            self._leases.require_writer_quiescence(self.lease, None, native_quiescence=proof)
            self._state, checkpoint = self._workspaces.create_candidate_checkpoint(
                self._admission.workspace,
                expected_state_version=self._state.state_version,
                expected_parent_checkpoint_id=self.assignment.input_checkpoint_id,
                attempt_id=self.assignment.attempt_id,
                assignment_id=self.assignment.assignment_id,
                git=self._control,
            )
            acceptance = ExecutionEvidenceAcceptanceAuthority(
                self._database,
                workspaces=self._workspaces,
                assignments=self._assignments,
                leases=self._leases,
                materializations=self._materializations,
            ).accept(
                self._admission.workspace,
                self.assignment,
                self.lease,
                self.materialization,
                output_checkpoint_id=checkpoint.checkpoint_id,
                result_identity=stable_id("candidate-run-result", result.model_dump(mode="json")),
                native_quiescence=proof,
            )
            self._state = self._leases.release(
                self.lease,
                expected_state_version=self._state.state_version,
                native_quiescence=proof,
            )
            settlement = "checkpointed"
        except Exception:
            reason = "CANDIDATE_SETTLEMENT_INCOMPLETE"
            try:
                self._state = self._workspaces.state(self._admission.workspace.workspace_id)
            except Exception:
                reason = "CANDIDATE_STATE_UNAVAILABLE"
        self._receipt = CandidateExecutionReceipt(
            workspace=self._admission.workspace,
            state=self._state,
            assignment=self.assignment,
            lease=self.lease,
            materialization=self.materialization,
            checkpoint=checkpoint,
            acceptance=acceptance,
            settlement=settlement,
            reason_code=reason,
        )
        return self._receipt

    def close(self) -> None:
        if self._closed:
            return
        if self._git_fd is not None:
            os.close(self._git_fd)
        os.close(self.workspace_directory_fd)
        self._closed = True


def prepare_candidate_execution(
    admission: CandidateWorkspaceAdmission,
    *,
    database: LocalDatabase,
    effect: ClaimedRepositoryEffectAttempt,
    require_execution_current: Callable[[], None],
    require_settlement_current: Callable[[], None] | None = None,
    git: GitService | None = None,
) -> CandidateExecution:
    return CandidateExecution(
        admission,
        database=database,
        effect=effect,
        require_execution_current=require_execution_current,
        require_settlement_current=require_settlement_current,
        git=git,
    )


def inspect_candidate_run(
    fleet_run_id: str,
    *,
    database: LocalDatabase,
    detail: FleetRunDetail | None = None,
) -> CandidateRunInspection:
    """Read through original owners without creating paths or reinterpreting missing bytes."""
    selected = detail or Journal(database).read_view().required_run_detail(fleet_run_id)
    target = selected.run.target
    if (
        selected.run.fleet_run_id != fleet_run_id
        or not isinstance(target, FleetPlanTarget)
        or target.candidate_workspace is None
        or target.candidate_workspace.fleet_run_id != fleet_run_id
    ):
        raise StateConflict("Run does not own this candidate workspace")
    admission = target.candidate_workspace
    workspaces = RepositoryWorkspaceAuthority(database, initialize=False)
    if workspaces.reference(admission.workspace.workspace_id) != admission.workspace:
        raise StateConflict("Candidate Run workspace identity differs from its durable owner")
    state = workspaces.state(admission.workspace.workspace_id)
    checkpoint = (
        workspaces.candidate_checkpoint(state.current_checkpoint_id)
        if state.current_checkpoint_id is not None
        else None
    )
    receipts = [
        operation.evidence.candidate_receipt
        for operation in selected.operations
        if isinstance(operation.evidence, FleetPlanOperationEvidence)
        and operation.evidence.candidate_receipt is not None
    ]
    if len(receipts) > 1:
        raise StateConflict("Candidate Run has ambiguous original operation receipts")
    try:
        path = str(workspaces.physical_path(admission.workspace))
    except StateConflict:
        path = None
    return CandidateRunInspection(
        fleet_run_id=fleet_run_id,
        admission=admission,
        state=state,
        source_state=workspaces.state(admission.source_workspace.workspace_id),
        checkpoint=checkpoint,
        receipt=receipts[0] if receipts else None,
        path=path,
        path_status="resolved" if path is not None else "unavailable",
    )


def retire_candidate_run(
    fleet_run_id: str,
    *,
    database: LocalDatabase,
    detail: FleetRunDetail,
    expected_state_version: int,
    reconcile: bool = False,
    git: GitService | None = None,
) -> CandidateRunInspection:
    """Explicit cleanup through original owners; no caller-supplied physical proof."""
    from oh_goon.core.time import utc_now

    current = inspect_candidate_run(fleet_run_id, database=database, detail=detail)
    if not detail.run.status.terminal:
        raise StateConflict("Candidate retirement requires its terminal original Run")
    if current.state.state_version != expected_state_version:
        raise StateConflict("Candidate retirement state changed since inspection")
    workspaces = RepositoryWorkspaceAuthority(database, initialize=False)
    workspace = current.admission.workspace
    control = workspace_control_git(git)
    state = current.state
    marker = workspaces._worktree_retirement_marker_for_id(workspace.workspace_id)
    pending = marker.exists() or marker.is_symlink()
    if pending:
        if not reconcile:
            raise StateConflict("Native candidate retirement is unresolved; inspect and reconcile")
        state = workspaces.reconcile_worktree_retirement(workspace, git=control)
    elif state.lifecycle_state is not WorkspaceLifecycleState.RELEASED:
        physical = workspaces.physical_path(workspace).lstat()
        if (physical.st_dev, physical.st_ino, physical.st_mode) != (
            current.admission.workspace_device,
            current.admission.workspace_inode,
            current.admission.workspace_mode,
        ):
            raise StateConflict("Candidate physical root changed before retirement")
        if state.current_lease_id is not None:
            if not reconcile:
                raise StateConflict("Candidate writer requires explicit cleanup reconciliation")
            leases = WorkspaceLeaseStore(database, workspaces=workspaces)
            lease = leases.lease(state.current_lease_id)
            # This observes canonical cancellation facts, never a local ACK.
            proof = workspaces.recorded_cancelled_writer_quiescence(workspace, lease)
            _require_clean_retirement_candidate(workspaces, current.admission, control)
            if utc_now() >= lease.expires_at:

                def original_native_proof(observed: WorkspaceLease) -> NativeWorkspaceQuiescence:
                    if observed != lease:
                        raise StateConflict(
                            "Candidate writer changed during cleanup reconciliation"
                        )
                    return proof

                state = leases.reconcile_expired(
                    lease.lease_id,
                    expected_state_version=state.state_version,
                    prove_native_quiescence=original_native_proof,
                )
            else:
                state = leases.release(
                    lease,
                    expected_state_version=state.state_version,
                    native_quiescence=proof,
                )
        _require_clean_retirement_candidate(workspaces, current.admission, control)
        if state.lifecycle_state is WorkspaceLifecycleState.READY:
            state = workspaces.transition(
                workspace.workspace_id,
                expected_state_version=state.state_version,
                next_state=WorkspaceLifecycleState.FINALIZED,
            )
        if state.lifecycle_state is not WorkspaceLifecycleState.FINALIZED:
            raise StateConflict("Candidate lifecycle cannot authorize native retirement")
        state = workspaces.retire_worktree(
            workspace,
            expected_state_version=state.state_version,
            git=control,
        )
    if state.lifecycle_state is WorkspaceLifecycleState.RELEASED:
        source = current.admission.source_workspace
        source_state = workspaces.state(source.workspace_id)
        if source_state.lifecycle_state is not WorkspaceLifecycleState.RELEASED:
            # The original owner rejects still-admitted references in its write cut.
            workspaces.release(
                source.workspace_id, expected_state_version=source_state.state_version
            )
    return inspect_candidate_run(fleet_run_id, database=database, detail=detail)


def _require_clean_retirement_candidate(
    workspaces: RepositoryWorkspaceAuthority,
    admission: CandidateWorkspaceAdmission,
    control: GitService,
) -> None:
    """Refuse dirty/unpromoted output before changing lease or lifecycle state."""
    workspace = admission.workspace
    checkpoint = workspaces.current_candidate_checkpoint(workspace, git=control)
    if (checkpoint.source_sha256, checkpoint.byte_count, checkpoint.entry_count) != (
        admission.checkpoint.source_sha256,
        admission.checkpoint.byte_count,
        admission.checkpoint.entry_count,
    ):
        raise StateConflict("Candidate contains unpromoted source; preserve it for review")
    path = workspaces.physical_path(workspace)
    observed = control.observe(path, env=_worktree_git_environment())
    if observed.head_sha != workspace.base_revision or observed.changed_files:
        raise StateConflict("Candidate contains dirty or changed output; preserve it for review")
    control.require_no_ignored_files(path, env=_worktree_git_environment())
