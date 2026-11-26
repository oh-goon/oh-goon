"""Prepare and dispatch the selected writable task through the original Fleet owners."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from oh_goon.core.errors import (
    FleetPlanInvalid,
    ScheduleExecutionStale,
    ScheduleNotFound,
    StateConflict,
)
from oh_goon.core.models.agent_providers import ResolvedNativeAgent
from oh_goon.core.models.branch_advance import BranchAdvanceReceipt
from oh_goon.core.models.schedule import ScheduleCreate, ScheduleDefinition
from oh_goon.core.models.schedule_execution import RunningScheduleRun
from oh_goon.core.models.target import FleetPlanTarget
from oh_goon.core.models.writable_schedule import (
    WritableSchedulePolicy,
    WritableScheduleTask,
    is_scheduled_branch_advance_id,
    writable_native_selection_id,
)
from oh_goon.core.product_identity import repository_subject_instance_id
from oh_goon.engine.git import GitService
from oh_goon.integrations.fleet_plan_process_isolation import NativeProfileCommandRunner
from oh_goon.runtime.branch_advances import BranchAdvanceStore
from oh_goon.runtime.database import LocalDatabase
from oh_goon.runtime.schedule_definitions import ScheduleDefinitionStore
from oh_goon.runtime.schedule_executions import ScheduleExecutionStore
from oh_goon.runtime.workspaces import RepositoryWorkspaceAuthority
from oh_goon.services.branch_advance import BranchAdvanceRequest, advance_verified_candidate
from oh_goon.services.fleet_plan_candidate import (
    inspect_candidate_run,
    require_current_branch_round_tip,
    workspace_control_git,
)
from oh_goon.services.fleet_run_control_plane import FleetRunControlPlane
from oh_goon.services.repositories import RepositoryService

if TYPE_CHECKING:
    from oh_goon.services.fleet_plans import FleetPlanService


class WritableScheduleRounds:
    def __init__(
        self,
        database: LocalDatabase,
        definitions: ScheduleDefinitionStore,
        executions: ScheduleExecutionStore,
        runs: FleetRunControlPlane,
        repositories: RepositoryService,
        plans: Callable[[], FleetPlanService],
        native_git: GitService | None,
    ) -> None:
        self._database = database
        self._definitions = definitions
        self._executions = executions
        self._runs = runs
        self._repositories = repositories
        self._plans = plans
        self._git = native_git
        self._branches = BranchAdvanceStore(database)

    def _selection(self, workspace: Path) -> ResolvedNativeAgent | None:
        runner = self._git.command_runner if self._git is not None else None
        if not isinstance(runner, NativeProfileCommandRunner) or runner.repository != workspace:
            return None
        return runner.native_agent

    def _published(self, advance_id: str) -> BranchAdvanceReceipt:
        receipt = self._branches.find(advance_id)
        if receipt is None or receipt.published_at is None:
            raise StateConflict("Writable Schedule requires an original published branch advance")
        return receipt

    def create(
        self,
        schedule_id: str,
        *,
        seed_advance_id: str,
        tasks: tuple[WritableScheduleTask, ...],
        interval_minutes: int,
        name: str | None = None,
    ) -> ScheduleDefinition:
        if is_scheduled_branch_advance_id(seed_advance_id):
            raise FleetPlanInvalid("Select an explicitly published seed for the writable Schedule")
        seed = self._published(seed_advance_id)
        prepared = seed.preparation
        selected = self._selection(prepared.repository_path)
        if selected is None or self._git is None:
            raise FleetPlanInvalid(
                "Writable Schedule creation requires its selected native runtime"
            )
        policy = WritableSchedulePolicy(
            seed_advance_id=seed_advance_id,
            tasks=tasks,
            native_selection_id=writable_native_selection_id(selected),
        )
        display_name = name if name is not None else schedule_id
        with self._branches.lock_next_request(seed_advance_id):
            try:
                existing = self._definitions.get(schedule_id)
            except ScheduleNotFound:
                pass
            else:
                if (
                    self._definitions.writable_policy(existing.generation_id) == policy
                    and existing.interval_minutes == interval_minutes
                    and existing.name == display_name
                ):
                    return existing
                raise StateConflict("Schedule ID already binds another definition")
            if self._published(seed_advance_id).next_run is not None:
                raise StateConflict("Writable Schedule seed already binds a successor")
            repository = self._repositories.require_included(prepared.repository_id)
            if (
                repository.path != prepared.repository_path
                or RepositoryWorkspaceAuthority.receiving_checkout_identity(repository.path)
                != prepared.checkout_identity
            ):
                raise StateConflict("Writable Schedule requires its registered checkout")
            self._plans().require_writable_round_runtime(selected, repository.path)
            require_current_branch_round_tip(seed, git=workspace_control_git(self._git))
            original = self._runs.get(prepared.verification.fleet_run_id).run.target
            if not isinstance(original, FleetPlanTarget) or original.candidate_workspace is None:
                raise StateConflict("Writable Schedule seed lost its original verified candidate")
            return self._definitions.create(
                schedule_id,
                ScheduleCreate(
                    name=display_name,
                    target=original,
                    repository_ids=(prepared.repository_id,),
                    repository_instance_ids={
                        prepared.repository_id: repository_subject_instance_id(repository.path)
                    },
                    interval_minutes=interval_minutes,
                ),
                writable_policy=policy,
            )

    def start(self, occurrence: RunningScheduleRun, repository_id: str) -> None:
        selection = occurrence.intent.writable_round
        assert selection is not None
        predecessor_run_id = selection.predecessor_run_id
        if predecessor_run_id is None:
            previous = self._published(selection.advance_id)
        else:
            recorded = self._branches.find_by_next_run(predecessor_run_id)
            if recorded is None or recorded.published_at is None:
                raise StateConflict("Writable Schedule predecessor has no published branch receipt")
            previous = recorded
        prior = previous.preparation
        selected = self._selection(prior.repository_path)
        if (
            selected is None
            or writable_native_selection_id(selected) != selection.native_selection_id
        ):
            raise ScheduleExecutionStale(
                "Writable round is waiting for its selected native runtime"
            )
        self._executions.require_pending_writable_round(
            occurrence.schedule_run_id,
            repository_id=repository_id,
            advance_id=selection.advance_id,
            task_sha256=selection.task.prompt_sha256,
            native_selection_id=selection.native_selection_id,
        )
        plans = self._plans()
        plans.require_writable_round_runtime(selected, prior.repository_path)
        if predecessor_run_id is not None:
            staged = self._branches.find(selection.advance_id)
            if staged is not None:
                checkpoint_id = staged.preparation.verification.checkpoint_id
            else:
                candidate = inspect_candidate_run(predecessor_run_id, database=self._database)
                checkpoint_id = candidate.checkpoint.checkpoint_id if candidate.checkpoint else None
            if checkpoint_id is None:
                raise StateConflict("Writable Schedule predecessor has no candidate checkpoint")
            advance_verified_candidate(
                BranchAdvanceRequest(
                    advance_id=selection.advance_id,
                    fleet_run_id=predecessor_run_id,
                    checkpoint_id=checkpoint_id,
                    branch_owner=prior.branch_owner_path,
                    workspace=prior.repository_path,
                    branch_ref=prior.branch_ref,
                    expected_tip=prior.commit,
                ),
                database=self._database,
                repositories=self._repositories,
                schedule_run_id=occurrence.schedule_run_id,
                git=workspace_control_git(self._git),
            )
        plans.start_writable_round(
            advance_id=selection.advance_id,
            selected=selected,
            workspace=prior.repository_path,
            skill=None,
            task=selection.task.prompt,
            schedule_run_id=occurrence.schedule_run_id,
        )
