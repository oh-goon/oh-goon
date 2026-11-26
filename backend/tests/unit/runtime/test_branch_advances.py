from __future__ import annotations

import sqlite3
import stat
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import timedelta
from pathlib import Path
from typing import TypedDict

import pytest
from pydantic import TypeAdapter, ValidationError

from oh_goon.core.errors import (
    RuntimeSafetyStale,
    ScheduleDefinitionStale,
    ScheduleExecutionStale,
    StateConflict,
)
from oh_goon.core.models.agent_providers import (
    AgentConnection,
    AgentModel,
    AgentProvider,
    NativeAgentDefinition,
    NativeAgentProfile,
    PiSessionIntent,
    ResolvedNativeAgent,
)
from oh_goon.core.models.branch_advance import (
    PreparedBranchAdvance,
    branch_round_run_id,
    is_branch_round_run_id,
    writable_round_request_sha256,
)
from oh_goon.core.models.candidate_execution import CandidateWorkspaceAdmission
from oh_goon.core.models.capability import CapabilitySnapshot
from oh_goon.core.models.fleet_plan import (
    ResolvedAgentStep,
    ResolvedCommandStep,
    ResolvedFleetPlan,
    ResolvedNativeAgentWorkload,
)
from oh_goon.core.models.fleet_plan_evidence import PlanSnapshotEvidence
from oh_goon.core.models.fleet_plan_execution_policy import MaintenanceExecutionPolicy
from oh_goon.core.models.goon_plan_source import PlanFileSource
from oh_goon.core.models.parallel_candidate_verification import ParallelCandidateVerification
from oh_goon.core.models.schedule import (
    ScheduleDefinition,
    ScheduleDeleteRequest,
    ScheduleOccurrenceRepositoryRun,
    ScheduleReplace,
)
from oh_goon.core.models.schedule_execution import CompletedScheduleRun, RunningScheduleRun
from oh_goon.core.models.source_authority import RepositorySourceAuthority
from oh_goon.core.models.target import AgentStepDeliveryReference, FleetPlanTarget
from oh_goon.core.models.workspace import CandidateCheckpoint, WorkspaceRef
from oh_goon.core.models.writable_schedule import (
    ApprovedTaskBatch,
    ApprovedTaskDocument,
    ScheduledWritableRound,
    WritableSchedulePolicy,
    WritableScheduleTask,
    WritableScheduleTasks,
    writable_native_selection_id,
    writable_schedule_advance_id,
    writable_schedule_run_id,
)
from oh_goon.core.schedule_transitions import begin_schedule_run
from oh_goon.core.time import utc_now
from oh_goon.core.vocabulary import (
    ScheduleExecutionStatus,
    ScheduleRepositoryRunStatus,
    ScheduleState,
)
from oh_goon.core.vocabulary.workspace import WorkspaceKind
from oh_goon.runtime.branch_advances import BranchAdvanceStore
from oh_goon.runtime.database import LocalDatabase
from oh_goon.runtime.schedule_definitions import ScheduleDefinitionStore
from oh_goon.runtime.schedule_executions import ScheduleExecutionStore
from tests.factories.fleet_plan_targets import structural_execution_identity
from tests.factories.schedules import schedule_create, scheduled_fleet_run_intent


class _ChildBinding(TypedDict):
    schedule_run_id: str
    repository_id: str
    fleet_run_id: str
    capability: CapabilitySnapshot
    target: FleetPlanTarget


_LEGACY_OWNER = "repo-subject-instance-" + "a" * 64
_STABLE_BARE_OWNER = "bare-branch-owner-" + "b" * 64


def _preparation(root: Path, advance_id: str = "advance-a") -> PreparedBranchAdvance:
    prior = "a" * 40
    return PreparedBranchAdvance(
        advance_id=advance_id,
        request_sha256="1" * 64,
        repository_path=root / "checkout",
        repository_id="repository-a",
        checkout_identity="checkout-anchor-a",
        branch_owner_path=root / "branch-owner.git",
        branch_owner_subject_id="branch-owner-instance-a",
        branch_ref="refs/heads/agent-a",
        expected_tip=prior,
        commit="b" * 40,
        tree="c" * 40,
        patch_sha256="d" * 64,
        verification=ParallelCandidateVerification(
            fleet_run_id="candidate-run-a",
            parallel_work_id="work-group-a",
            workspace_id="workspace-a",
            base_revision=prior,
            base_source_id="original-source",
            verifier_id="frozen-verifier-a",
            checkpoint_id="verified-checkpoint-a",
            source_sha256="e" * 64,
            result_identity="result-a",
            physical_execution_id="native-verifier-a",
            native_outcome="PASS",
            outcome="PASS",
            reason="Candidate passed its frozen verifier",
        ),
        source_byte_count=30,
        source_entry_count=2,
    )


def test_advancement_replays_only_its_exact_publication_and_successor(tmp_path: Path) -> None:
    path = tmp_path / "state" / "oh-goon.db"
    preparation = _preparation(tmp_path)
    store = BranchAdvanceStore(LocalDatabase(path))
    prepared = store.prepare(preparation)
    assert prepared.published_at is None
    assert store.prepare(preparation) == prepared

    next_run_id = branch_round_run_id(preparation.advance_id)
    assert is_branch_round_run_id(next_run_id)
    assert not is_branch_round_run_id(next_run_id + "a")
    next_hash = "2" * 64
    with pytest.raises(StateConflict, match="awaiting publication"):
        store.bind_next_request(
            preparation.advance_id, request_sha256=next_hash, fleet_run_id=next_run_id
        )
    with pytest.raises(StateConflict, match="prepared commit"):
        store.confirm(preparation, observed_tip=preparation.expected_tip)
    with pytest.raises(StateConflict, match="different proposal"):
        store.prepare(preparation.model_copy(update={"request_sha256": "3" * 64}))
    assert store.find_by_next_run(next_run_id) is None

    reopened = BranchAdvanceStore(LocalDatabase(path))
    assert reopened.find(preparation.advance_id) == prepared
    published = reopened.confirm(preparation, observed_tip=preparation.commit)
    assert published.published_at is not None
    with reopened.lock_next_request(preparation.advance_id):
        attached = reopened.bind_next_request(
            preparation.advance_id, request_sha256=next_hash, fleet_run_id=next_run_id
        )
    assert attached.published_at == published.published_at
    assert attached.next_run is not None
    assert attached.next_run.fleet_run_id == next_run_id
    assert attached.next_run.repository_subject_id is None
    with pytest.raises(StateConflict, match="original reserved request"):
        reopened.bind_next_source(
            preparation.advance_id,
            repository_subject_id="synchronized-subject",
            fleet_run_id=next_run_id,
            request_sha256="5" * 64,
        )
    source = {
        "repository_subject_id": "synchronized-subject",
        "fleet_run_id": next_run_id,
        "request_sha256": next_hash,
    }
    source_bound = reopened.bind_next_source(preparation.advance_id, **source)
    assert source_bound == reopened.bind_next_source(preparation.advance_id, **source)
    assert source_bound.next_run is not None
    assert source_bound.next_run.repository_subject_id == "synchronized-subject"
    with pytest.raises(StateConflict, match="different repository subject"):
        reopened.bind_next_source(
            preparation.advance_id, **{**source, "repository_subject_id": "foreign-subject"}
        )
    assert reopened.confirm(preparation, observed_tip=preparation.commit) == source_bound
    assert (
        reopened.bind_next_request(
            preparation.advance_id, request_sha256=next_hash, fleet_run_id=next_run_id
        )
        == source_bound
    )
    with pytest.raises(StateConflict, match="different successor"):
        reopened.bind_next_request(
            preparation.advance_id, request_sha256="4" * 64, fleet_run_id=next_run_id
        )
    with pytest.raises(StateConflict, match="Successor Run ID differs"):
        reopened.bind_next_request(
            preparation.advance_id, request_sha256=next_hash, fleet_run_id="other-run"
        )
    readback = BranchAdvanceStore(LocalDatabase(path, read_only=True))
    assert (
        readback.find(preparation.advance_id)
        == readback.find_by_next_run(next_run_id)
        == source_bound
    )


@pytest.mark.parametrize(
    "owner_ids",
    (
        pytest.param((_LEGACY_OWNER, _LEGACY_OWNER), id="same-identity"),
        pytest.param((_LEGACY_OWNER, _STABLE_BARE_OWNER), id="legacy-and-stable"),
    ),
)
def test_concurrent_advances_reserve_the_same_native_tip_only_once(
    tmp_path: Path, owner_ids: tuple[str, str]
) -> None:
    path = tmp_path / "state" / "oh-goon.db"
    first = BranchAdvanceStore(LocalDatabase(path))
    second = BranchAdvanceStore(LocalDatabase(path))

    def prepare(store: BranchAdvanceStore, advance_id: str, owner_id: str) -> bool:
        try:
            store.prepare(
                _preparation(tmp_path, advance_id).model_copy(
                    update={"branch_owner_subject_id": owner_id}
                )
            )
        except StateConflict as error:
            assert str(error) == "This exact prior branch tip already has an advancement"
            return False
        return True

    with ThreadPoolExecutor(max_workers=2) as workers:
        a = workers.submit(prepare, first, "a", owner_ids[0])
        b = workers.submit(prepare, second, "b", owner_ids[1])
        assert sorted((a.result(timeout=10), b.result(timeout=10))) == [False, True]
    assert sum(store is not None for store in (first.find("a"), first.find("b"))) == 1


def test_receipt_storage_preserves_existing_current_epoch_database(tmp_path: Path) -> None:
    path = tmp_path / "state" / "oh-goon.db"
    database = LocalDatabase(path)
    with database.transaction(write=True) as connection:
        connection.execute(
            "INSERT INTO locations (path, position, added_at) VALUES (?, ?, ?)",
            (str(tmp_path / "existing-location"), 0, "2026-11-25T00:00:00+00:00"),
        )
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("DROP TABLE branch_advances")
        connection.commit()

    old_readback = BranchAdvanceStore(LocalDatabase(path, read_only=True))
    assert old_readback.find("no-receipt") is None
    opened = LocalDatabase(path)
    assert not opened.initialized_new_state
    with opened.transaction() as connection:
        assert [row[0] for row in connection.execute("SELECT path FROM locations")] == [
            str(tmp_path / "existing-location")
        ]
    preparation = _preparation(tmp_path)
    assert BranchAdvanceStore(opened).prepare(preparation).preparation == preparation


def _native_selection() -> ResolvedNativeAgent:
    return ResolvedNativeAgent(
        agent="pi",
        profile_name="fixture",
        definition=NativeAgentDefinition(executable="/usr/bin/pi"),
        profile=NativeAgentProfile(backend="bubblewrap", allowed_agents=("pi",)),
        executable_sha256="a" * 64,
        backend_configuration_id="fixture-bubblewrap",
        session=PiSessionIntent(mode="ephemeral"),
        connection=AgentConnection(
            profile="fixture",
            provider_name="fixture",
            provider=AgentProvider(api="openai-chat-completions", base_url="http://127.0.0.1:9/v1"),
            selection=AgentModel(provider="fixture", model="fixture"),
        ),
    )


def _admitted_candidate(
    prepared: PreparedBranchAdvance,
    task: WritableScheduleTask,
    native: ResolvedNativeAgent,
    subject: str,
) -> FleetPlanTarget:
    source = RepositorySourceAuthority.create(
        subject_instance_id=subject,
        source_snapshot=PlanSnapshotEvidence(
            sha256="e" * 64,
            byte_count=prepared.source_byte_count,
            entry_count=prepared.source_entry_count,
        ),
    )
    receiving = WorkspaceRef(
        workspace_id="writable-candidate",
        repository_id=prepared.repository_id,
        base_revision=prepared.commit,
        kind=WorkspaceKind.WORKTREE,
    )
    admission = CandidateWorkspaceAdmission(
        fleet_run_id=branch_round_run_id(prepared.advance_id),
        source=source,
        source_workspace=receiving.model_copy(
            update={"workspace_id": "writable-source", "kind": WorkspaceKind.READ_SNAPSHOT}
        ),
        workspace=receiving,
        checkpoint=CandidateCheckpoint(
            checkpoint_id="writable-first-checkpoint",
            workspace_id=receiving.workspace_id,
            source_sha256=source.source_snapshot.sha256,
            byte_count=source.source_snapshot.byte_count,
            entry_count=source.source_snapshot.entry_count,
            created_at=utc_now(),
        ),
        state_version=1,
        workspace_device=1,
        workspace_inode=1,
        workspace_mode=stat.S_IFDIR | 0o700,
        workspace_ctime_ns=1,
    )
    agent = ResolvedAgentStep(
        step_id="agent",
        source_step_id="agent",
        timeout_seconds=5,
        workload=ResolvedNativeAgentWorkload(native_agent=native),
        delivery_sha256="a" * 64,
        handoff_identity="retained-handoff",
        task_summary=task.prompt,
        task_sha256=task.prompt_sha256,
    )
    plan = ResolvedFleetPlan.create(
        name="Writable round",
        description="",
        execution=MaintenanceExecutionPolicy(),
        steps=(
            ResolvedCommandStep(step_id="verify-base", argv=("verify",), timeout_seconds=5),
            agent,
            ResolvedCommandStep(step_id="verify-1", argv=("verify",), timeout_seconds=5),
        ),
        native_agent=native,
    )
    return FleetPlanTarget(
        kind="fleet_plan",
        plan=plan,
        source=PlanFileSource(sha256="0" * 64),
        identity=structural_execution_identity(plan),
        agent_deliveries=(
            AgentStepDeliveryReference(
                step_id=agent.step_id,
                delivery_sha256=agent.delivery_sha256,
                handoff_identity=agent.handoff_identity,
                reference_id="retained-delivery",
            ),
        ),
        candidate_workspace=admission,
        reviewed_repository_source=source,
    )


def _published_seed(database: LocalDatabase, root: Path) -> PreparedBranchAdvance:
    store = BranchAdvanceStore(database)
    preparation = _preparation(root)
    store.prepare(preparation)
    store.confirm(preparation, observed_tip=preparation.commit)
    return preparation


def _writable_policy(prepared: PreparedBranchAdvance) -> WritableSchedulePolicy:
    return WritableSchedulePolicy(
        seed_advance_id=prepared.advance_id,
        native_selection_id=writable_native_selection_id(_native_selection()),
        tasks=(
            WritableScheduleTask(task_id="first", prompt="First private durable task"),
            WritableScheduleTask(task_id="second", prompt="Second private durable task"),
        ),
    )


def _set_schedule_state(definition: ScheduleDefinition, state: ScheduleState) -> ScheduleReplace:
    return ScheduleReplace.model_validate(
        definition.model_dump(exclude={"schedule_id", "created_at", "updated_at"})
        | {"state": state}
    )


def test_writable_policy_reserves_a_published_seed_until_the_definition_is_removed(
    tmp_path: Path,
) -> None:
    database = LocalDatabase(tmp_path / "state" / "app.db")
    prepared = _published_seed(database, tmp_path)
    policy = _writable_policy(prepared)
    request = schedule_create(repository_ids=(prepared.repository_id,))
    definitions = ScheduleDefinitionStore(database)

    with pytest.raises(ValidationError, match="task IDs must be unique"):
        TypeAdapter(WritableScheduleTasks).validate_python((policy.tasks[0], policy.tasks[0]))
    original = definitions.create("writable", request, writable_policy=policy)
    reopened = ScheduleDefinitionStore(LocalDatabase(database.path))
    assert reopened.create("writable", request, writable_policy=policy) == original
    assert policy.tasks[0].prompt not in original.model_dump_json()
    assert reopened.seed_reserved(prepared.advance_id)
    with pytest.raises(StateConflict, match="already owns"):
        reopened.create("duplicate", request, writable_policy=policy)
    assert reopened.definitions() == [original]
    with pytest.raises(StateConflict, match="another definition"):
        reopened.create("writable", request)

    paused = reopened.replace("writable", _set_schedule_state(original, ScheduleState.PAUSED))
    assert reopened.writable_policy(paused.generation_id) == policy
    reopened.delete(
        "writable",
        ScheduleDeleteRequest(
            generation_id=paused.generation_id,
            record_version=paused.record_version,
        ),
    )
    assert not reopened.seed_reserved(prepared.advance_id)
    assert reopened.writable_policy(paused.generation_id) is None
    recreated = reopened.create("writable", request, writable_policy=policy)
    assert recreated.generation_id != paused.generation_id


def test_approved_document_is_bound_to_one_writable_schedule_generation(tmp_path: Path) -> None:
    database = LocalDatabase(tmp_path / "state" / "app.db")
    prepared = _published_seed(database, tmp_path)
    definitions = ScheduleDefinitionStore(database)
    request = schedule_create(repository_ids=(prepared.repository_id,))
    ordinary = definitions.create("ordinary", request)
    writable = definitions.create("writable", request, writable_policy=_writable_policy(prepared))
    directory = tmp_path.stat()
    document = ApprovedTaskDocument(
        path=tmp_path / "approved.json",
        directory_device=directory.st_dev,
        directory_inode=directory.st_ino,
    )

    with pytest.raises(StateConflict, match="require a writable Schedule"):
        definitions.bind_approved_task_document(
            ordinary.schedule_id, expected_generation_id=ordinary.generation_id, document=document
        )
    with pytest.raises(ScheduleDefinitionStale, match="current Schedule"):
        definitions.bind_approved_task_document(
            writable.schedule_id, expected_generation_id=ordinary.generation_id, document=document
        )

    assert (
        definitions.bind_approved_task_document(
            writable.schedule_id, expected_generation_id=writable.generation_id, document=document
        )
        == writable
    )
    replay_revision = database.durable_revision()
    reopened_database = LocalDatabase(database.path)
    reopened = ScheduleDefinitionStore(reopened_database)
    assert (
        reopened.bind_approved_task_document(
            writable.schedule_id, expected_generation_id=writable.generation_id, document=document
        )
        == writable
    )
    assert reopened_database.durable_revision() == replay_revision
    with pytest.raises(StateConflict, match="another approved document"):
        reopened.bind_approved_task_document(
            writable.schedule_id,
            expected_generation_id=writable.generation_id,
            document=document.model_copy(update={"directory_inode": directory.st_ino + 1}),
        )

    with reopened_database.transaction() as connection:
        assert reopened.approved_task_document_in(connection, writable.generation_id) == document
        assert reopened.approved_task_document_in(connection, ordinary.generation_id) is None
    reopened.delete(
        writable.schedule_id,
        ScheduleDeleteRequest(
            generation_id=writable.generation_id, record_version=writable.record_version
        ),
    )
    with reopened_database.transaction() as connection:
        assert reopened.approved_task_document_in(connection, writable.generation_id) is None


def test_approved_task_append_is_ordered_revision_fenced_and_atomic(tmp_path: Path) -> None:
    database = LocalDatabase(tmp_path / "state" / "app.db")
    prepared = _published_seed(database, tmp_path)
    policy = _writable_policy(prepared)
    request = schedule_create(repository_ids=(prepared.repository_id,))
    definitions = ScheduleDefinitionStore(database)
    definition = definitions.create("writable", request, writable_policy=policy)
    generation = definition.generation_id
    extras = tuple(
        WritableScheduleTask(task_id=f"task-{index}", prompt=f"Approved task {index}.")
        for index in range(32)
    )
    with pytest.raises(ValidationError, match="task IDs must be unique"):
        ApprovedTaskBatch(tasks=(extras[0], extras[0]))
    with pytest.raises(ValidationError, match="at most 32"):
        ApprovedTaskBatch(tasks=(*extras, policy.tasks[0]))
    with pytest.raises(ScheduleDefinitionStale, match="no longer binds"):
        definitions.append_approved_tasks(
            generation,
            expected_task_index=2,
            expected_durable_revision=database.durable_revision(),
            tasks=extras,
        )

    directory = tmp_path.stat()
    definitions.bind_approved_task_document(
        definition.schedule_id,
        expected_generation_id=generation,
        document=ApprovedTaskDocument(
            path=tmp_path / "approved.json",
            directory_device=directory.st_dev,
            directory_inode=directory.st_ino,
        ),
    )
    initial_revision = database.durable_revision()
    assert (
        definitions.append_approved_tasks(
            generation,
            expected_task_index=2,
            expected_durable_revision=initial_revision,
            tasks=extras,
        )
        == 32
    )

    reopened_database = LocalDatabase(database.path)
    reopened = ScheduleDefinitionStore(reopened_database)
    current_revision = reopened_database.durable_revision()
    assert reopened.create("writable", request, writable_policy=policy) == definition
    with pytest.raises(RuntimeSafetyStale):
        reopened.append_approved_tasks(
            generation, expected_task_index=34, expected_durable_revision=initial_revision, tasks=()
        )
    with pytest.raises(ScheduleDefinitionStale, match="append position changed"):
        reopened.append_approved_tasks(
            generation,
            expected_task_index=2,
            expected_durable_revision=current_revision,
            tasks=extras,
        )
    assert (
        reopened.append_approved_tasks(
            generation,
            expected_task_index=34,
            expected_durable_revision=current_revision,
            tasks=extras,
        )
        == 0
    )
    assert (
        reopened.append_approved_tasks(
            generation, expected_task_index=34, expected_durable_revision=current_revision, tasks=()
        )
        == 0
    )

    next_task = WritableScheduleTask(task_id="next", prompt="Later approved task.")
    for prior in (policy.tasks[0], extras[-1]):
        conflicting = WritableScheduleTask(task_id=prior.task_id, prompt="Different task.")
        with pytest.raises(StateConflict, match="different content"):
            reopened.append_approved_tasks(
                generation,
                expected_task_index=34,
                expected_durable_revision=current_revision,
                tasks=(next_task, conflicting),
            )
    assert reopened_database.durable_revision() == current_revision
    with reopened_database.transaction() as connection:
        assert reopened.writable_task_in(connection, generation, policy, 34) is None
    assert (
        reopened.append_approved_tasks(
            generation,
            expected_task_index=34,
            expected_durable_revision=current_revision,
            tasks=(policy.tasks[0], next_task, extras[-1]),
        )
        == 1
    )

    with reopened_database.transaction() as connection:
        assert tuple(
            reopened.writable_task_in(connection, generation, policy, index) for index in range(35)
        ) == (*policy.tasks, *extras, next_task)
        assert reopened.writable_task_in(connection, generation, policy, 35) is None


def test_writable_policy_reserves_the_same_branch_across_owner_identity_changes(
    tmp_path: Path,
) -> None:
    database = LocalDatabase(tmp_path / "state" / "app.db")
    branches = BranchAdvanceStore(database)
    legacy = _preparation(tmp_path, "legacy-seed").model_copy(
        update={"branch_owner_subject_id": _LEGACY_OWNER}
    )
    current = _preparation(tmp_path / "receiving", "current-seed").model_copy(
        update={
            "repository_id": "repository-b",
            "checkout_identity": "checkout-anchor-b",
            "branch_owner_path": legacy.branch_owner_path,
            "branch_owner_subject_id": _STABLE_BARE_OWNER,
            "expected_tip": legacy.commit,
            "commit": "f" * 40,
            "tree": "9" * 40,
            "verification": legacy.verification.model_copy(
                update={
                    "fleet_run_id": "candidate-run-b",
                    "base_revision": legacy.commit,
                    "source_sha256": "8" * 64,
                }
            ),
        }
    )
    for prepared in (legacy, current):
        branches.prepare(prepared)
        branches.confirm(prepared, observed_tip=prepared.commit)

    definitions = ScheduleDefinitionStore(database)
    first = definitions.create(
        "legacy-queue",
        schedule_create(repository_ids=(legacy.repository_id,)),
        writable_policy=_writable_policy(legacy),
    )
    request = schedule_create(repository_ids=(current.repository_id,))
    policy = _writable_policy(current)
    reopened = ScheduleDefinitionStore(LocalDatabase(database.path))
    with pytest.raises(StateConflict, match="already owns this seed, branch or checkout"):
        reopened.create("current-queue", request, writable_policy=policy)
    assert reopened.definitions() == [first]

    reopened.delete(
        first.schedule_id,
        ScheduleDeleteRequest(
            generation_id=first.generation_id,
            record_version=first.record_version,
        ),
    )
    second = reopened.create("current-queue", request, writable_policy=policy)
    assert reopened.writable_policy(second.generation_id) == policy


def test_writable_selection_survives_pause_and_admission_rollback(tmp_path: Path) -> None:
    database = LocalDatabase(tmp_path / "state" / "app.db")
    prepared = _published_seed(database, tmp_path)
    policy = _writable_policy(prepared)
    definitions = ScheduleDefinitionStore(database)
    repository_id = prepared.repository_id
    setup = schedule_create(repository_ids=(repository_id,))
    definition = definitions.create("writable", setup, writable_policy=policy)
    executions = ScheduleExecutionStore(database)
    selected = ScheduledWritableRound(
        task_index=0,
        task=policy.tasks[0],
        advance_id=policy.seed_advance_id,
        native_selection_id=policy.native_selection_id,
    )
    occurrence_id = writable_schedule_run_id(definition.generation_id, 0)
    frozen = scheduled_fleet_run_intent(definition)
    occurrence = begin_schedule_run(
        schedule_run_id=occurrence_id,
        schedule_id=definition.schedule_id,
        intent=frozen.model_copy(update={"writable_round": selected}),
        started_at=utc_now(),
    )
    executions.insert(occurrence, expected_durable_revision=database.durable_revision())
    reopened = ScheduleExecutionStore(LocalDatabase(database.path))
    selection_args = {
        "repository_id": repository_id,
        "advance_id": selected.advance_id,
        "task_sha256": selected.task.prompt_sha256,
        "native_selection_id": selected.native_selection_id,
    }

    assert reopened.require_pending_writable_round(occurrence_id, **selection_args) == selected
    with pytest.raises(ScheduleExecutionStale, match="frozen Schedule selection"):
        reopened.require_pending_writable_round(
            occurrence_id, **(selection_args | {"task_sha256": policy.tasks[1].prompt_sha256})
        )
    paused = definitions.replace("writable", _set_schedule_state(definition, ScheduleState.PAUSED))
    with pytest.raises(ScheduleExecutionStale, match="no active selected occurrence"):
        reopened.require_pending_writable_round(occurrence_id, **selection_args)
    definitions.replace("writable", _set_schedule_state(paused, ScheduleState.ACTIVE))
    assert reopened.require_pending_writable_round(occurrence_id, **selection_args) == selected

    native = _native_selection()
    fleet_run_id = branch_round_run_id(selected.advance_id)
    subject = "repo-subject-instance-" + "a" * 64
    target = _admitted_candidate(prepared, selected.task, native, subject)
    args: _ChildBinding = {
        "schedule_run_id": occurrence_id,
        "repository_id": repository_id,
        "fleet_run_id": fleet_run_id,
        "capability": frozen.capability,
        "target": target,
    }
    with pytest.raises(ScheduleExecutionStale, match="published branch receipt"):
        reopened.require_pending_child_binding(
            **args, repository_instance_ids={repository_id: subject}
        )

    store = BranchAdvanceStore(database)
    digest = writable_round_request_sha256(selected=native, skill=None, task=selected.task.prompt)
    store.bind_next_request(selected.advance_id, request_sha256=digest, fleet_run_id=fleet_run_id)
    store.bind_next_source(
        selected.advance_id,
        request_sha256=digest,
        fleet_run_id=fleet_run_id,
        repository_subject_id=subject,
    )
    changed = args.copy()
    changed["target"] = _admitted_candidate(prepared, policy.tasks[1], native, subject)
    with pytest.raises(ScheduleExecutionStale, match="frozen task"):
        reopened.require_pending_child_binding(
            **changed,
            repository_instance_ids={repository_id: subject},
        )
    reopened.require_pending_child_binding(**args, repository_instance_ids={repository_id: subject})
    with pytest.raises(RuntimeError, match="interrupted admission"):
        with database.transaction(write=True) as connection:
            reopened.bind_admitted_child_in(connection, **args)
            raise RuntimeError("interrupted admission")
    assert reopened.get(occurrence_id) == occurrence
    with database.transaction(write=True) as connection:
        reopened.bind_admitted_child_in(connection, **args)

    bound = executions.get(occurrence_id)
    assert isinstance(bound, RunningScheduleRun)
    assert bound.intent.writable_round == selected
    assert bound.intent.target == target
    assert bound.intent.repository_instance_ids == {repository_id: subject}
    assert bound.repository_runs[0].fleet_run_id == fleet_run_id
    with pytest.raises(ScheduleExecutionStale, match="no longer pending"):
        reopened.require_pending_writable_round(occurrence_id, **selection_args)

    completed = CompletedScheduleRun.model_validate(
        bound.model_dump(mode="python", exclude_computed_fields=True)
        | {
            "status": ScheduleExecutionStatus.COMPLETED,
            "finished_at": utc_now(),
            "repository_runs": (
                ScheduleOccurrenceRepositoryRun(
                    repository_id=repository_id,
                    fleet_run_id=fleet_run_id,
                    status=ScheduleRepositoryRunStatus.SUCCESSFUL,
                ),
            ),
        }
    )
    executions.compare_and_set(bound, completed)
    next_id = writable_schedule_run_id(definition.generation_id, 1)
    next_round = ScheduledWritableRound(
        task_index=1,
        task=policy.tasks[1],
        advance_id=writable_schedule_advance_id(next_id),
        predecessor_run_id=fleet_run_id,
        native_selection_id=policy.native_selection_id,
    )
    following = begin_schedule_run(
        schedule_run_id=next_id,
        schedule_id=definition.schedule_id,
        intent=frozen.model_copy(update={"writable_round": next_round}),
        started_at=occurrence.started_at - timedelta(minutes=1),
    )
    executions.insert(following, expected_durable_revision=database.durable_revision())
    with database.transaction() as connection:
        assert (
            reopened.latest_writable_for_generation_in(
                connection, definition.schedule_id, definition.generation_id
            )
            == following
        )
    assert (
        reopened.require_pending_writable_round(
            next_id,
            **(
                selection_args
                | {
                    "advance_id": next_round.advance_id,
                    "task_sha256": next_round.task.prompt_sha256,
                }
            ),
        )
        == next_round
    )
