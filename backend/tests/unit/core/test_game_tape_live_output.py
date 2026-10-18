from __future__ import annotations

from datetime import UTC, datetime, timedelta

import oh_goon.runtime.game_tape_live_output as live_output_module
from oh_goon.runtime.game_tape_live_output import GameTapeLiveOutputStore


def test_live_output_is_bounded_queryable_and_sealed_for_terminal_handoff() -> None:
    store = GameTapeLiveOutputStore()

    unavailable = store.snapshot("run-1")
    assert unavailable.available is False
    assert unavailable.state == "UNAVAILABLE"

    store.open_run("run-1")
    store.append("run-1", "repo-a", "test", 1, "stdout", "one\n")
    store.append("run-1", "repo-a", "test", 1, "stderr", "two\n")

    snapshot = store.snapshot("run-1")
    assert snapshot.available is True
    assert snapshot.state == "ACTIVE"
    assert [chunk.sequence for chunk in snapshot.chunks] == [1, 2]
    assert [chunk.stream for chunk in snapshot.chunks] == ["stdout", "stderr"]
    assert [chunk.line_start for chunk in snapshot.chunks] == [1, 1]
    assert [chunk.line_end for chunk in snapshot.chunks] == [1, 1]
    assert store.snapshot("run-1", after=1).chunks == (snapshot.chunks[1],)

    store.seal("run-1")

    sealed = store.snapshot("run-1")
    assert sealed.available is True
    assert sealed.state == "SEALED"
    assert sealed.expires_at is not None
    assert sealed.chunks == snapshot.chunks

    store.clear("run-1")
    assert store.snapshot("run-1").state == "UNAVAILABLE"


def test_live_output_expires_lazily_after_terminal_retention(monkeypatch) -> None:
    now = datetime(2026, 8, 18, 12, 0, tzinfo=UTC)
    current = [now]
    monkeypatch.setattr(live_output_module, "utc_now", lambda: current[0])
    store = GameTapeLiveOutputStore()
    store.open_run("run-1")
    store.append("run-1", "repo-a", "test", 1, "stderr", "failed\n")
    store.seal("run-1")

    assert store.snapshot("run-1").state == "SEALED"

    current[0] = now + timedelta(minutes=10, seconds=1)
    expired = store.snapshot("run-1")
    assert expired.available is False
    assert expired.state == "UNAVAILABLE"


def test_live_output_tracks_stream_lines_and_locates_retained_failure_line() -> None:
    store = GameTapeLiveOutputStore()
    store.open_run("run-1")

    store.append("run-1", "repo-a", "test", 2, "stderr", "line one\nline")
    store.append("run-1", "repo-a", "test", 2, "stderr", " two\nline three")

    batch, retained = store.snapshot_cut("run-1", after=1)
    assert [(item.line_start, item.line_end) for item in retained] == [(1, 2), (2, 3)]
    assert tuple(item.sequence for item in batch.chunks) == (2,)
    assert tuple(item.sequence for item in retained) == (1, 2)


def test_live_output_splits_large_chunks_before_model_validation() -> None:
    store = GameTapeLiveOutputStore()
    store.open_run("run-1")

    store.append("run-1", "repo-a", "test", 1, "stdout", "x" * 20_000)

    snapshot = store.snapshot("run-1")
    assert len(snapshot.chunks) == 2
    assert len(snapshot.chunks[0].text) == 16_384
    assert len(snapshot.chunks[1].text) == 3_616
    assert snapshot.chunks[0].line_start == snapshot.chunks[1].line_start == 1


def test_live_output_isolated_by_fleet_run_identity() -> None:
    store = GameTapeLiveOutputStore()
    store.open_run("run-a")
    store.open_run("run-b")
    store.append("run-a", "repo-a", "test", 1, "stderr", "secret-a\n")
    store.append("run-b", "repo-b", "test", 1, "stdout", "output-b\n")

    run_a = store.snapshot("run-a")
    run_b = store.snapshot("run-b")

    assert [chunk.text for chunk in run_a.chunks] == ["secret-a\n"]
    assert [chunk.repository_id for chunk in run_a.chunks] == ["repo-a"]
    assert [chunk.text for chunk in run_b.chunks] == ["output-b\n"]
    assert [chunk.repository_id for chunk in run_b.chunks] == ["repo-b"]


def test_run_activity_is_bounded_and_separate_from_step_output() -> None:
    store = GameTapeLiveOutputStore()
    store.open_run("run-1")
    store.append_activity("run-1", "Preparing immutable execution inputs")
    store.append_activity("run-1", "Execution inputs ready")

    sequence, activities = store.activity_snapshot("run-1", after=0)
    assert sequence == 2
    assert activities == (
        (1, "Preparing immutable execution inputs"),
        (2, "Execution inputs ready"),
    )
    assert store.snapshot("run-1").chunks == ()
    assert store.activity_snapshot("run-1", after=1)[1] == (
        (2, "Execution inputs ready"),
    )


def test_duplicate_open_does_not_erase_active_or_terminal_output() -> None:
    """Activation replay must not destroy diagnostic evidence already observed."""
    store = GameTapeLiveOutputStore()
    store.open_run("run-1")
    store.append("run-1", "repo-a", "test", 1, "stdout", "before replay\n")

    store.open_run("run-1")
    active = store.snapshot("run-1")
    assert [chunk.text for chunk in active.chunks] == ["before replay\n"]
    assert active.next_sequence == 1

    store.seal("run-1")
    store.open_run("run-1")
    terminal = store.snapshot("run-1")
    assert terminal.state == "SEALED"
    assert [chunk.text for chunk in terminal.chunks] == ["before replay\n"]
    assert terminal.next_sequence == 1


def test_live_output_projects_latest_run_activity_without_step_output() -> None:
    store = GameTapeLiveOutputStore()
    store.open_run("run-1")
    store.append_activity("run-1", "Repository target worker entered for repo")
    store.append_activity("run-1", "Repository execution authority ready for repo")

    snapshot = store.snapshot("run-1")

    assert snapshot.latest_activity == "Repository execution authority ready for repo"
    assert snapshot.activity_sequence == 2
    assert snapshot.chunks == ()
