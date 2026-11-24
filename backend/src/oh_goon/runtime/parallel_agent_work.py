"""Durable task membership and dispatch fencing beside the original Run journal."""

from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

from oh_goon.core.errors import ExecutionBusy, FleetDomainError, StateConflict
from oh_goon.core.models.parallel_agent_work import (
    ParallelAgentChildReadback,
    ParallelAgentWorkIntent,
    ParallelAgentWorkReadback,
    parallel_native_selection_id,
)
from oh_goon.core.models.parallel_candidate_verification import (
    ParallelCandidateVerifier,
    is_parallel_verifier_step_id,
)
from oh_goon.core.models.run_admission import DirectFleetExecutionAdmission
from oh_goon.core.models.source_authority import RepositorySourceAuthority
from oh_goon.core.models.target import FleetPlanTarget
from oh_goon.core.models.workspace import WorkspaceRef
from oh_goon.core.process_birth import (
    ProcessBirthIdentity,
    observe_process_birth,
    observe_process_liveness,
)
from oh_goon.core.vocabulary.workspace import WorkspaceKind, WorkspaceLifecycleState
from oh_goon.runtime.database import LocalDatabase
from oh_goon.runtime.workspaces import RepositoryWorkspaceAuthority

if TYPE_CHECKING:
    from oh_goon.runtime.execution import ExecutionCoordinator
    from oh_goon.runtime.journal_view import JournalReadView

_NOTE_MAINTENANCE_GROUPS = 32


@dataclass(frozen=True, slots=True)
class _ParallelWork:
    intent: ParallelAgentWorkIntent
    request_hash: str
    controller: ProcessBirthIdentity
    closed: bool
    cancelled: bool


def _work_in(connection: sqlite3.Connection, work_id: str) -> _ParallelWork | None:
    row = connection.execute(
        "SELECT intent_json, request_hash, controller_json, closed, cancelled "
        "FROM parallel_agent_work WHERE work_id = ?",
        (work_id,),
    ).fetchone()
    if row is None:
        return None
    return _ParallelWork(
        intent=ParallelAgentWorkIntent.model_validate_json(row[0]),
        request_hash=row[1],
        controller=ProcessBirthIdentity.from_payload(json.loads(row[2])),
        closed=bool(row[3]),
        cancelled=bool(row[4]),
    )


def _dispatch_is_closed(work: _ParallelWork) -> bool:
    if work.closed:
        return True
    observed = observe_process_birth(work.controller.pid)
    if observed is not None and observed != work.controller:
        return True
    return observe_process_liveness(work.controller.pid) is False


def _planned_workspace(
    intent: ParallelAgentWorkIntent, fleet_run_id: str, kind: WorkspaceKind
) -> WorkspaceRef:
    return RepositoryWorkspaceAuthority.parallel_child_reference(
        work_id=intent.work_id,
        fleet_run_id=fleet_run_id,
        repository_id=intent.repository_id,
        base_revision=intent.base_revision,
        kind=kind,
    )


def _readback_in(
    database: LocalDatabase, connection: sqlite3.Connection, work: _ParallelWork
) -> ParallelAgentWorkReadback:
    from oh_goon.runtime.journal import Journal

    view = Journal(database).read_view_from_connection(connection)
    closed = _dispatch_is_closed(work)
    workspaces = RepositoryWorkspaceAuthority(database, initialize=False)
    children = []
    for run_id, refusal, cancel_requested in connection.execute(
        "SELECT fleet_run_id, refusal, cancellation_requested FROM parallel_agent_children "
        "WHERE work_id = ? ORDER BY ordinal",
        (work.intent.work_id,),
    ):
        detail = view.run_detail(run_id)
        workspace = _planned_workspace(work.intent, run_id, WorkspaceKind.WORKTREE)
        source = _planned_workspace(work.intent, run_id, WorkspaceKind.READ_SNAPSHOT)
        if detail is not None:
            candidate = (
                detail.run.target.candidate_workspace
                if isinstance(detail.run.target, FleetPlanTarget)
                else None
            )
            if candidate is None or candidate.parallel_work_id != work.intent.work_id:
                raise StateConflict("Parallel work membership differs from the original Run")
            workspace = candidate.workspace
            source = candidate.source_workspace
            child = ParallelAgentChildReadback(
                fleet_run_id=run_id,
                dispatch="admitted",
                status=detail.run.status,
                outcome=detail.outcome,
                cancellation_requested=bool(cancel_requested or work.cancelled),
            )
        elif closed or refusal is not None or cancel_requested:
            child = ParallelAgentChildReadback(
                fleet_run_id=run_id,
                dispatch="never_started",
                refusal=refusal
                or (
                    "cancelled_before_admission"
                    if work.cancelled or cancel_requested
                    else "dispatch_closed"
                ),
                cancellation_requested=bool(cancel_requested or work.cancelled),
            )
        else:
            child = ParallelAgentChildReadback(
                fleet_run_id=run_id,
                dispatch=(
                    "pending"
                    if observe_process_birth(work.controller.pid) == work.controller
                    else "unresolved"
                ),
            )
        children.append(
            child.model_copy(
                update={
                    "candidate_state": workspaces.observe_requested_state_in(connection, workspace),
                    "source_state": workspaces.observe_requested_state_in(connection, source),
                }
            )
        )
    if tuple(child.fleet_run_id for child in children) != tuple(
        child.fleet_run_id for child in work.intent.children
    ):
        raise StateConflict("Parallel work membership is incomplete")
    return ParallelAgentWorkReadback(
        intent=work.intent,
        dispatch_closed=closed,
        cancelled=work.cancelled,
        children=tuple(children),
    )


def read_parallel_work(database: LocalDatabase, work_id: str) -> ParallelAgentWorkReadback:
    with database.transaction() as connection:
        work = _work_in(connection, work_id)
        if work is None:
            raise StateConflict("Parallel work request was not found")
        return _readback_in(database, connection, work)


def replay_parallel_work(
    database: LocalDatabase, work_id: str, request_hash: str
) -> ParallelAgentWorkReadback | None:
    with database.transaction() as connection:
        work = _work_in(connection, work_id)
        if work is None:
            return None
        if work.request_hash != request_hash:
            raise StateConflict(
                "Parallel work identity is already bound to different request bytes"
            )
        return _readback_in(database, connection, work)


def begin_parallel_work(
    database: LocalDatabase,
    intent: ParallelAgentWorkIntent,
    request_hash: str,
    verifier: ParallelCandidateVerifier,
) -> bool:
    """Reserve the two child IDs once, before original Fleet admission."""
    from oh_goon.runtime.execution import ExecutionCoordinator
    from oh_goon.runtime.journal import Journal

    controller = observe_process_birth()
    if controller is None:
        raise StateConflict("Parallel work requires an observed Linux controller identity")
    if verifier.identity != intent.verifier_id:
        raise StateConflict("Parallel work differs from its frozen acceptance verification")
    with database.transaction(write=True) as connection:
        existing = _work_in(connection, intent.work_id)
        if existing is not None:
            if existing.request_hash != request_hash:
                raise StateConflict(
                    "Parallel work identity is already bound to different request bytes"
                )
            return False
        if ExecutionCoordinator(database).retained_leases_in(connection):
            raise ExecutionBusy("Parallel work requires both original native worker slots")
        view = Journal(database).read_view_from_connection(connection)
        if any(view.run_detail(child.fleet_run_id) is not None for child in intent.children):
            raise StateConflict("A planned child Run identity is already admitted")
        connection.execute(
            "INSERT INTO parallel_agent_work "
            "(work_id, request_hash, intent_json, verifier_json, controller_json) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                intent.work_id,
                request_hash,
                intent.model_dump_json(),
                verifier.model_dump_json(),
                json.dumps(controller.to_payload(), separators=(",", ":")),
            ),
        )
        connection.executemany(
            "INSERT INTO parallel_agent_children (work_id, ordinal, fleet_run_id) VALUES (?, ?, ?)",
            (
                (intent.work_id, index, child.fleet_run_id)
                for index, child in enumerate(intent.children)
            ),
        )
    return True


def _member_work_id_in(connection: sqlite3.Connection, fleet_run_id: str) -> str | None:
    row = connection.execute(
        "SELECT work_id FROM parallel_agent_children WHERE fleet_run_id = ?", (fleet_run_id,)
    ).fetchone()
    return row[0] if row is not None else None


def reserve_parallel_candidate_workspaces(
    database: LocalDatabase,
    workspaces: RepositoryWorkspaceAuthority,
    *,
    work_id: str,
    fleet_run_id: str,
    repository_id: str,
    base_revision: str,
    expected_source: RepositorySourceAuthority | None,
) -> tuple[WorkspaceRef, WorkspaceRef]:
    """Reserve both durable preparation identities before any filesystem materialization."""
    from oh_goon.runtime.journal import Journal

    with database.transaction(write=True) as connection:
        work = _work_in(connection, work_id)
        lane = connection.execute(
            "SELECT refusal, cancellation_requested FROM parallel_agent_children "
            "WHERE work_id = ? AND fleet_run_id = ?",
            (work_id, fleet_run_id),
        ).fetchone()
        if (
            work is None
            or lane is None
            or lane[0] is not None
            or lane[1]
            or work.closed
            or work.cancelled
            or observe_process_birth() != work.controller
        ):
            raise StateConflict("Parallel candidate preparation lacks an open original lane")
        intent = work.intent
        if (
            intent.repository_id != repository_id
            or intent.base_revision != base_revision
            or intent.source != expected_source
            or fleet_run_id not in {child.fleet_run_id for child in intent.children}
            or Journal(database).read_view_from_connection(connection).run_detail(fleet_run_id)
            is not None
        ):
            raise StateConflict("Parallel candidate preparation differs from original intent")
        candidate, source = (
            workspaces.request_in(
                connection,
                repository_id=repository_id,
                base_revision=base_revision,
                kind=kind,
                workspace_id=_planned_workspace(intent, fleet_run_id, kind).workspace_id,
            )
            for kind in (WorkspaceKind.WORKTREE, WorkspaceKind.READ_SNAPSHOT)
        )
    return candidate, source


def parallel_notes_enabled_in(
    connection: sqlite3.Connection, work_id: str, fleet_run_id: str
) -> bool:
    work = _work_in(connection, work_id)
    member = connection.execute(
        "SELECT cancellation_requested FROM parallel_agent_children "
        "WHERE work_id = ? AND fleet_run_id = ?",
        (work_id, fleet_run_id),
    ).fetchone()
    return bool(
        work is not None
        and work.intent.notes_enabled
        and not work.cancelled
        and member is not None
        and not member[0]
    )


def read_parallel_verifier_in(
    connection: sqlite3.Connection, work_id: str
) -> ParallelCandidateVerifier:
    row = connection.execute(
        "SELECT intent_json, verifier_json FROM parallel_agent_work WHERE work_id = ?",
        (work_id,),
    ).fetchone()
    if row is None:
        raise StateConflict("Parallel work verification authority is unavailable")
    intent = ParallelAgentWorkIntent.model_validate_json(row[0])
    verifier = ParallelCandidateVerifier.model_validate_json(row[1])
    if verifier.identity != intent.verifier_id:
        raise StateConflict("Parallel work verification authority differs from admitted intent")
    return verifier


def read_parallel_verifier(database: LocalDatabase, work_id: str) -> ParallelCandidateVerifier:
    with database.transaction() as connection:
        return read_parallel_verifier_in(connection, work_id)


def require_parallel_candidate_binding_in(
    connection: sqlite3.Connection, *, fleet_run_id: str, target: object
) -> str | None:
    """Verify an original admission against exact planned membership in its transaction."""
    from oh_goon.core.models.fleet_plan import (
        ResolvedAgentStep,
        ResolvedCommandStep,
        ResolvedNativeAgentWorkload,
    )

    work_id = _member_work_id_in(connection, fleet_run_id)
    candidate = target.candidate_workspace if isinstance(target, FleetPlanTarget) else None
    if work_id is None:
        if candidate is not None and candidate.parallel_work_id is not None:
            raise StateConflict("Parallel candidate lacks planned Run membership")
        return None
    work = _work_in(connection, work_id)
    if (
        work is None
        or work.closed
        or work.cancelled
        or candidate is None
        or not isinstance(target, FleetPlanTarget)
        or candidate.parallel_work_id != work_id
        or observe_process_birth() != work.controller
    ):
        raise StateConflict(
            "Parallel candidate dispatch is closed or differs from its original authority"
        )
    intent = work.intent
    lane = connection.execute(
        "SELECT refusal, cancellation_requested FROM parallel_agent_children WHERE fleet_run_id = ?",
        (fleet_run_id,),
    ).fetchone()
    if lane is None or lane[0] is not None or lane[1]:
        raise StateConflict("Parallel child dispatch has already been settled or cancelled")
    child = next(item for item in intent.children if item.fleet_run_id == fleet_run_id)
    agents = tuple(step for step in target.plan.steps if isinstance(step, ResolvedAgentStep))
    commands = tuple(step for step in target.plan.steps if isinstance(step, ResolvedCommandStep))
    verifier = read_parallel_verifier_in(connection, work_id)
    if (
        candidate.fleet_run_id != fleet_run_id
        or candidate.workspace.repository_id != intent.repository_id
        or candidate.workspace.base_revision != intent.base_revision
        or candidate.source != intent.source
        or candidate.workspace != _planned_workspace(intent, fleet_run_id, WorkspaceKind.WORKTREE)
        or candidate.source_workspace
        != _planned_workspace(intent, fleet_run_id, WorkspaceKind.READ_SNAPSHOT)
        or len(agents) != 1
        or agents[0].task_sha256 != child.task_sha256
        or not isinstance(agents[0].workload, ResolvedNativeAgentWorkload)
        or parallel_native_selection_id(agents[0].workload.native_agent)
        != child.native_selection_id
        or len(target.plan.steps) != 2
        or len(commands) != 1
        or not is_parallel_verifier_step_id(commands[0].step_id)
        or commands[0].argv != verifier.argv
    ):
        raise StateConflict(
            "Parallel candidate differs from the planned task, repository or exact base"
        )
    return work_id


def record_child_refusal(
    database: LocalDatabase, work_id: str, fleet_run_id: str, reason: str
) -> None:
    from oh_goon.runtime.journal import Journal

    with database.transaction(write=True) as connection:
        if Journal(database).read_view_from_connection(connection).run_detail(fleet_run_id) is None:
            connection.execute(
                "UPDATE parallel_agent_children SET refusal = ? "
                "WHERE work_id = ? AND fleet_run_id = ? AND refusal IS NULL",
                (reason, work_id, fleet_run_id),
            )


def close_parallel_work(database: LocalDatabase, work_id: str) -> None:
    with database.transaction(write=True) as connection:
        work = _work_in(connection, work_id)
        if work is None or observe_process_birth() != work.controller:
            raise StateConflict("Parallel work dispatch requires the original controller")
        connection.execute(
            "UPDATE parallel_agent_work SET closed = 1 WHERE work_id = ?", (work_id,)
        )
    maintain_parallel_note_retention(database, work_id=work_id)


def cancel_parallel_work(
    database: LocalDatabase, work_id: str, *, fleet_run_id: str | None = None
) -> ParallelAgentWorkReadback:
    """Fence later admissions and cancel exact live Runs in the same write cut."""
    from oh_goon.runtime.execution import ExecutionCoordinator
    from oh_goon.runtime.journal import Journal

    execution = ExecutionCoordinator(database)
    journal = Journal(database)
    with database.transaction(write=True) as connection:
        work = _work_in(connection, work_id)
        if work is None:
            raise StateConflict("Parallel work request was not found")
        if fleet_run_id is None:
            connection.execute(
                "UPDATE parallel_agent_work SET closed = 1, cancelled = 1 WHERE work_id = ?",
                (work_id,),
            )
        elif _member_work_id_in(connection, fleet_run_id) == work_id:
            connection.execute(
                "UPDATE parallel_agent_children SET cancellation_requested = 1 WHERE fleet_run_id = ?",
                (fleet_run_id,),
            )
        else:
            raise StateConflict("Run is not a member of this parallel work request")
        view = journal.read_view_from_connection(connection)
        for child in work.intent.children:
            run_id = child.fleet_run_id
            if fleet_run_id is not None and run_id != fleet_run_id:
                continue
            detail = view.run_detail(run_id)
            if detail is None or not detail.run.status.can_request_cancel:
                continue
            target = detail.run.target
            admission = detail.execution
            if (
                not isinstance(target, FleetPlanTarget)
                or target.candidate_workspace is None
                or target.candidate_workspace.parallel_work_id != work_id
                or not isinstance(admission, DirectFleetExecutionAdmission)
                or admission.concurrency is None
            ):
                raise StateConflict("Parallel cancellation differs from the original Run authority")
            if execution.request_fleet_cancel_in(
                connection, run_id, concurrent_live_admission=admission.concurrency
            ):
                journal.request_cancel_in(connection, run_id)
        work = _work_in(connection, work_id)
        assert work is not None
        return _readback_in(database, connection, work)


def _notes_settled_at_in(
    connection: sqlite3.Connection,
    work: _ParallelWork,
    execution: ExecutionCoordinator,
    view: JournalReadView,
    workspaces: RepositoryWorkspaceAuthority,
) -> datetime | None:
    if not work.intent.notes_enabled or not _dispatch_is_closed(work):
        return None
    observed_settlements = []
    for child in work.intent.children:
        fleet_run_id = child.fleet_run_id
        if execution.retained_fleet_lease_in(connection, fleet_run_id) is not None:
            return None
        detail = view.run_detail(fleet_run_id)
        if detail is None:
            for kind in (WorkspaceKind.WORKTREE, WorkspaceKind.READ_SNAPSHOT):
                state = workspaces.observe_requested_state_in(
                    connection, _planned_workspace(work.intent, fleet_run_id, kind)
                )
                if (
                    state is not None
                    and state.lifecycle_state is not WorkspaceLifecycleState.RELEASED
                ):
                    return None
            continue
        target = detail.run.target
        if not detail.run.status.terminal or not isinstance(target, FleetPlanTarget):
            return None
        candidate = target.candidate_workspace
        if candidate is None or candidate.parallel_work_id != work.intent.work_id:
            return None
        row = connection.execute(
            "SELECT current_lease_id FROM workspace_state WHERE workspace_id = ?",
            (candidate.workspace.workspace_id,),
        ).fetchone()
        if row is None or row[0] is not None:
            return None
        # The original resource-close observation, when present, follows worker
        # terminal state in this verified cut. Startup does not reset this clock.
        observed_settlements.append(view.run_events(fleet_run_id).events[-1].generated_at)
    return max(observed_settlements, default=None)


def maintain_parallel_note_retention(
    database: LocalDatabase, *, work_id: str | None = None, fleet_run_id: str | None = None
) -> None:
    """Converge note expiry independently of an already committed Run outcome."""
    from oh_goon.runtime.agent_notes import AgentNotesStore
    from oh_goon.runtime.execution import ExecutionCoordinator
    from oh_goon.runtime.journal import Journal

    try:
        if fleet_run_id is not None:
            with database.transaction() as connection:
                work_id = _member_work_id_in(connection, fleet_run_id)
                if work_id is None:
                    return
        journal = Journal(database)
        notes = AgentNotesStore(database, journal)
        with database.transaction(write=True) as connection:
            if work_id is not None:
                work_ids = (work_id,)
            else:
                work_ids = tuple(
                    row[0]
                    for row in connection.execute(
                        """
                        SELECT work.work_id FROM parallel_agent_work AS work
                        WHERE EXISTS (
                            SELECT 1 FROM parallel_agent_notes WHERE work_id=work.work_id
                        ) AND NOT EXISTS (
                            SELECT 1 FROM parallel_agent_note_expiry WHERE work_id=work.work_id
                        ) AND NOT EXISTS (
                            SELECT 1 FROM parallel_agent_children AS child
                            JOIN execution_lease AS lease USING (fleet_run_id)
                            WHERE child.work_id=work.work_id
                        )
                        ORDER BY work.closed DESC, work.work_id LIMIT ?
                        """,
                        (_NOTE_MAINTENANCE_GROUPS,),
                    )
                )
            if work_ids:
                view = journal.read_view_from_connection(connection)
                execution = ExecutionCoordinator(database)
                workspaces = RepositoryWorkspaceAuthority(database, initialize=False)
                for selected_id in work_ids:
                    work = _work_in(connection, selected_id)
                    if work is None:
                        continue
                    settled_at = _notes_settled_at_in(connection, work, execution, view, workspaces)
                    if settled_at is not None:
                        notes.settle_in(connection, selected_id, settled_at=settled_at)
            notes.expire_in(connection)
    except (FleetDomainError, OSError, ValueError, sqlite3.Error) as error:
        logging.getLogger(__name__).warning(
            "Parallel note retention could not be observed (%s)", type(error).__name__
        )
