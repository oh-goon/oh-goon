"""M178: a scheduled Fleet child must belong to the live durable occurrence.

The original Schedule store owns occurrence authority; Fleet's original
admission planner/lease transaction still owns repository and execution work.
"""

from __future__ import annotations

from contextlib import nullcontext
from pathlib import Path

import pytest

from oh_goon.core.capability_registry import resolve_capability_intent
from oh_goon.core.errors import CapabilityUnavailable, ScheduleExecutionStale, StateConflict
from oh_goon.core.models.run_admission import ExplicitRepositoryScope
from oh_goon.core.product_identity import schedule_repository_fleet_run_id
from oh_goon.core.schedule_transitions import mark_repository_running
from oh_goon.core.vocabulary import FleetRunSource, OperationAction
from oh_goon.runtime.database import LocalDatabase
from oh_goon.runtime.journal import Journal
from oh_goon.runtime.schedule_definitions import ScheduleDefinitionStore
from oh_goon.runtime.schedule_executions import ScheduleExecutionStore
from oh_goon.services.fleet_run_control_plane import FleetRunControlPlane
from tests.factories.schedules import persist_active_schedule, schedule_create


class PlannerReached(Exception):
    pass


class Prework:
    def __init__(self) -> None:
        self.calls = 0

    def require_fleet_prework_available(self, *args, **kwargs) -> None:
        self.calls += 1

    def serialize_admission(self, identity: str):
        return nullcontext()


class Planner:
    def __init__(self) -> None:
        self.calls = 0
        self.during_plan = None

    def plan(self, *args, **kwargs):
        self.calls += 1
        if self.during_plan is not None:
            self.during_plan()
            return object()
        raise PlannerReached


def _fixture(tmp_path: Path, *, store_wired: bool = True):
    database = LocalDatabase(tmp_path / "schedule-owned.db")
    definitions = ScheduleDefinitionStore(database)
    executions = ScheduleExecutionStore(database)
    definition = definitions.create("schedule-owned", schedule_create(repository_ids=("repo-a",)))
    occurrence = persist_active_schedule(database, executions, definition)
    prework = Prework()
    planner = Planner()
    journal = Journal(database)
    control = FleetRunControlPlane(
        planner,
        journal,
        prework,
        object(),
        object(),
        object(),
        schedule_executions=executions if store_wired else None,
    )
    return database, executions, occurrence, journal, prework, planner, control


def _start(control, occurrence, **changes):
    repository_id = "repo-a"
    params = dict(
        capability=occurrence.intent.capability,
        target=occurrence.intent.target,
        repository_scope=ExplicitRepositoryScope(repository_ids=(repository_id,)),
        fleet_run_id=schedule_repository_fleet_run_id(occurrence.schedule_run_id, repository_id),
        source=FleetRunSource.SCHEDULED,
        schedule_run_id=occurrence.schedule_run_id,
        expected_repository_instance_ids={
            repository_id: occurrence.intent.repository_instance_ids[repository_id]
        },
    )
    params.update(changes)
    return control.start_bound(**params)


@pytest.mark.parametrize(
    "change",
    [
        {"schedule_run_id": None},
        {"schedule_run_id": "unknown-occurrence"},
        {"fleet_run_id": "forged-child"},
        {"expected_repository_instance_ids": None},
        {"expected_repository_instance_ids": {"repo-a": "other-instance"}},
        {"expected_repository_instance_ids": {"repo-a": "instance-repo-a", "repo-b": "other"}},
        {"repository_scope": ExplicitRepositoryScope(repository_ids=("repo-b",))},
    ],
)
def test_unbound_or_stale_child_fails_before_prework_planning_and_journal(tmp_path, change):
    database, _store, occurrence, journal, prework, planner, control = _fixture(tmp_path)
    before = database.durable_revision()
    with pytest.raises((StateConflict, ScheduleExecutionStale)):
        _start(control, occurrence, **change)
    assert database.durable_revision() == before
    assert journal.read_view().run_history(limit=10).items == ()
    assert prework.calls == planner.calls == 0


def test_child_cannot_forge_another_plan_snapshot_or_capability(tmp_path):
    _db, _store, occurrence, _journal, prework, planner, control = _fixture(tmp_path)
    wrong = schedule_create(name="different Plan", repository_ids=("repo-a",)).target
    with pytest.raises(ScheduleExecutionStale, match="frozen occurrence"):
        _start(control, occurrence, target=wrong)
    # Another valid capability snapshot cannot be submitted as the frozen one.
    other = resolve_capability_intent(OperationAction.INIT, None).definition.durable_snapshot()
    with pytest.raises((CapabilityUnavailable, ScheduleExecutionStale, StateConflict, ValueError)):
        _start(control, occurrence, capability=other)
    assert prework.calls == planner.calls == 0


def test_schedule_child_needs_the_original_occurrence_store(tmp_path):
    _db, _store, occurrence, _journal, prework, planner, control = _fixture(
        tmp_path,
        store_wired=False,
    )
    with pytest.raises(StateConflict, match="durable occurrence authority"):
        _start(control, occurrence)
    assert prework.calls == planner.calls == 0


def test_consumed_child_cannot_reenter_planning_even_when_occurrence_is_live(tmp_path):
    _db, store, occurrence, _journal, prework, planner, control = _fixture(tmp_path)
    store.compare_and_set(
        occurrence,
        mark_repository_running(
            occurrence,
            "repo-a",
            schedule_repository_fleet_run_id(occurrence.schedule_run_id, "repo-a"),
        ),
    )
    with pytest.raises(ScheduleExecutionStale, match="no longer pending"):
        _start(control, occurrence)
    assert prework.calls == planner.calls == 0


def test_child_consumed_during_planning_is_not_admitted(tmp_path):
    database, store, occurrence, journal, prework, planner, control = _fixture(tmp_path)

    def change_during_planning():
        store.compare_and_set(
            occurrence,
            mark_repository_running(
                occurrence,
                "repo-a",
                schedule_repository_fleet_run_id(occurrence.schedule_run_id, "repo-a"),
            ),
        )

    planner.during_plan = change_during_planning
    with pytest.raises(ScheduleExecutionStale, match="no longer pending"):
        _start(control, occurrence)
    assert planner.calls == prework.calls == 1
    assert journal.read_view().run_history(limit=10).items == ()
    assert database.durable_revision() > 0


def test_valid_queued_child_reaches_one_original_planner(tmp_path):
    _db, _store, occurrence, _journal, prework, planner, control = _fixture(tmp_path)
    with pytest.raises(PlannerReached):
        _start(control, occurrence)
    assert prework.calls == planner.calls == 1


def test_valid_pending_child_idempotent_existing_run_skips_second_planning(
    tmp_path,
    monkeypatch,
):
    from oh_goon.services import fleet_run_control_plane as module

    _db, _store, occurrence, journal, prework, planner, control = _fixture(tmp_path)
    existing_run = object()
    reads = []

    def matching(original_journal, fleet_run_id, intent, **kwargs):
        reads.append((original_journal, fleet_run_id, kwargs["source"]))
        return existing_run

    monkeypatch.setattr(module, "_matching_existing_run", matching)
    assert _start(control, occurrence) is existing_run
    assert reads == [
        (
            journal,
            schedule_repository_fleet_run_id(occurrence.schedule_run_id, "repo-a"),
            FleetRunSource.SCHEDULED,
        )
    ]
    assert prework.calls == 1
    assert planner.calls == 0


def test_invalid_schedule_child_does_not_even_observe_idempotent_journal(
    tmp_path,
    monkeypatch,
):
    _db, _store, occurrence, journal, prework, planner, control = _fixture(tmp_path)
    monkeypatch.setattr(journal, "read_view", lambda: pytest.fail("journal observed too early"))
    with pytest.raises(ScheduleExecutionStale):
        _start(control, occurrence, fleet_run_id="not-the-scheduled-child")
    assert prework.calls == planner.calls == 0


def test_scheduled_source_cannot_smuggle_manual_occurrence(tmp_path):
    _db, _store, occurrence, _journal, prework, planner, control = _fixture(tmp_path)
    with pytest.raises(StateConflict, match="Only scheduled"):
        _start(control, occurrence, source=FleetRunSource.MANUAL)
    assert prework.calls == planner.calls == 0
