"""RUN-3: one indexed immutable journal cut owns event pages and continuation."""

from __future__ import annotations

from pathlib import Path

import pytest

from oh_goon.core.errors import InvalidRequest, RunNotFound
from oh_goon.core.models.repository import RepositoryIdentity
from oh_goon.core.product_identity import repository_attempt_operation_id
from oh_goon.core.vocabulary import FleetRunEventType, OperationAction
from oh_goon.runtime.database import LocalDatabase
from oh_goon.runtime.journal import Journal
from oh_goon.services.fleet_run_control_plane import FleetRunControlPlane
from tests.factories.capabilities import admit_test_run
from tests.factories.journal import start_repository, start_run


def _journal(tmp_path: Path) -> tuple[Journal, RepositoryIdentity]:
    journal = Journal(LocalDatabase(tmp_path / "run3.db"))
    identity = RepositoryIdentity(name="agent", path=tmp_path / "agent")
    admit_test_run(journal, "run-3", OperationAction.INIT, [identity])
    start_run(journal, "run-3")
    start_repository(journal, "run-3", identity)
    return journal, identity


def test_bounded_pages_resume_exactly_without_duplicates_or_skips(tmp_path: Path) -> None:
    journal, _ = _journal(tmp_path)
    full = journal.run_events("run-3")
    assert len(full.events) >= 3
    assert full.has_more is False
    after = 0
    observed = []
    for _ in range(len(full.events)):
        page = journal.run_events("run-3", after=after, limit=1)
        assert len(page.events) == 1
        assert page.next_sequence == page.events[-1].sequence
        observed.extend(page.events)
        after = page.next_sequence
        assert page.has_more == (len(observed) < len(full.events))
        assert page.journal_revision == full.journal_revision
    assert tuple(observed) == full.events
    exhausted = journal.run_events("run-3", after=after, limit=1)
    assert exhausted.events == ()
    assert exhausted.has_more is False
    assert exhausted.next_sequence == after


def test_immutable_read_cut_uses_original_snapshot_after_new_commit(tmp_path: Path) -> None:
    journal, identity = _journal(tmp_path)
    before = journal.read_view()
    original = before.run_events("run-3", limit=2)
    # Later commit in the same Run must not mutate the original read cut.
    journal.record_execution_lifecycle(
        fleet_run_id="run-3",
        repository_id=identity.repository_id,
        operation_id=repository_attempt_operation_id("run-3", identity.repository_id),
        event_type=FleetRunEventType.STEP_REACHED,
        step_id="build",
        step_order=1,
    )
    assert before.run_events("run-3", limit=2) == original
    assert journal.run_events("run-3", limit=2).events == original.events
    assert journal.run_events("run-3", limit=2).journal_revision > original.journal_revision
    assert len(journal.run_events("run-3").events) > len(before.run_events("run-3").events)


def test_default_unbounded_call_preserves_legacy_internal_read(tmp_path: Path) -> None:
    journal, _ = _journal(tmp_path)
    legacy = journal.run_events("run-3")
    pinned = journal.read_view().run_events("run-3")
    assert pinned == legacy
    assert journal.run_events("run-3", after=legacy.next_sequence).events == ()
    assert journal.run_events("run-3", after=999, limit=2).next_sequence == legacy.next_sequence
    assert journal.run_events("run-3", limit=100).events == legacy.events


def test_fleet_control_plane_forwards_bounds_to_original_journal(tmp_path: Path) -> None:
    journal, _ = _journal(tmp_path)
    control = object.__new__(FleetRunControlPlane)
    control._journal = journal
    page = control.events("run-3", after=1, limit=1)
    assert page == journal.run_events("run-3", after=1, limit=1)
    assert page.has_more is True


def test_resume_after_new_durable_event_uses_original_run_sequence(tmp_path: Path) -> None:
    journal, identity = _journal(tmp_path)
    previous = journal.run_events("run-3", limit=100)
    assert not previous.has_more
    journal.record_execution_lifecycle(
        fleet_run_id="run-3",
        repository_id=identity.repository_id,
        operation_id=repository_attempt_operation_id("run-3", identity.repository_id),
        event_type=FleetRunEventType.STEP_REACHED,
        step_id="build",
        step_order=1,
    )
    resumed = journal.run_events("run-3", after=previous.next_sequence, limit=1)
    assert len(resumed.events) == 1
    assert resumed.events[0].sequence > previous.next_sequence
    assert resumed.next_sequence == resumed.events[0].sequence
    assert resumed.journal_revision > previous.journal_revision


@pytest.mark.parametrize(
    "after,limit", [(-1, 1), (0, 0), (0, 101), (0, -1), (True, 1), (0, True), (0, 1.5)]
)
def test_invalid_cursors_and_page_limits_fail_at_canonical_owner(
    tmp_path: Path, after: int, limit: int
) -> None:
    journal, _ = _journal(tmp_path)
    before = journal.run_events("run-3").journal_revision
    with pytest.raises(InvalidRequest):
        journal.run_events("run-3", after=after, limit=limit)
    assert journal.run_events("run-3").journal_revision == before


def test_unknown_run_is_not_an_empty_page(tmp_path: Path) -> None:
    journal, _ = _journal(tmp_path)
    with pytest.raises(RunNotFound):
        journal.run_events("unknown", limit=2)
