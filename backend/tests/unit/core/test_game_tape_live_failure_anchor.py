from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

from oh_goon.core.game_tape_live_failure_anchor import project_live_failure_anchors
from oh_goon.core.models.game_tape_live_output import (
    GameTapeLiveOutputBatch,
    GameTapeLiveOutputChunk,
)
from oh_goon.core.vocabulary import GameTapeFailureNavigationState
from oh_goon.services.game_tape_live_output import read_game_tape_live_output


def test_live_failure_anchor_reuses_durable_failure_stream_attempt_and_line() -> None:
    failure = SimpleNamespace(attempt=2, evidence_stream="stderr", evidence_line_count=37)
    step = SimpleNamespace(step_execution_id="step-execution-1", step_id="test", failure=failure)
    repository = SimpleNamespace(
        repository=SimpleNamespace(repository_id="repo-a"),
        steps=(step,),
    )
    tape = SimpleNamespace(repositories=(repository,))
    retained = (
        GameTapeLiveOutputChunk(
            sequence=91,
            repository_id="repo-a",
            step_id="test",
            attempt=2,
            stream="stderr",
            line_start=35,
            line_end=38,
            text="failure\n",
            emitted_at=datetime.now(UTC),
        ),
    )

    anchors = project_live_failure_anchors(tape, retained)  # type: ignore[arg-type]

    assert len(anchors) == 1
    assert anchors[0].line == 37
    assert anchors[0].sequence == 91
    assert anchors[0].retained is True


def test_live_failure_anchor_preserves_unretained_line_without_inventing_sequence() -> None:
    failure = SimpleNamespace(attempt=1, evidence_stream="stdout", evidence_line_count=4)
    step = SimpleNamespace(step_execution_id="step-execution-2", step_id="build", failure=failure)
    repository = SimpleNamespace(
        repository=SimpleNamespace(repository_id="repo-a"),
        steps=(step,),
    )
    tape = SimpleNamespace(repositories=(repository,))

    anchors = project_live_failure_anchors(tape, ())  # type: ignore[arg-type]

    assert anchors[0].line == 4
    assert anchors[0].sequence is None
    assert anchors[0].retained is False


def test_live_failure_anchor_uses_latest_sequence_from_one_retained_cut() -> None:
    failure = SimpleNamespace(attempt=1, evidence_stream="stderr", evidence_line_count=2)
    step = SimpleNamespace(step_execution_id="step-execution-3", step_id="test", failure=failure)
    tape = SimpleNamespace(
        repositories=(
            SimpleNamespace(repository=SimpleNamespace(repository_id="repo-a"), steps=(step,)),
        )
    )
    retained = tuple(
        GameTapeLiveOutputChunk(
            sequence=sequence,
            repository_id="repo-a",
            step_id="test",
            attempt=1,
            stream="stderr",
            line_start=1 if sequence == 1 else 2,
            line_end=2,
            text="x",
            emitted_at=datetime.now(UTC),
        )
        for sequence in (1, 2)
    )

    anchors = project_live_failure_anchors(tape, retained)  # type: ignore[arg-type]

    assert anchors[0].sequence == 2
    assert anchors[0].retained is True


def test_live_output_reader_derives_failure_anchors_from_the_same_snapshot_cut(monkeypatch) -> None:
    retained = (
        GameTapeLiveOutputChunk(
            sequence=7,
            repository_id="repo-a",
            step_id="test",
            attempt=1,
            stream="stderr",
            line_start=4,
            line_end=4,
            text="boom",
            emitted_at=datetime.now(UTC),
        ),
    )
    batch = GameTapeLiveOutputBatch(
        fleet_run_id="run-1",
        state="ACTIVE",
        available=True,
        chunks=(),
        next_sequence=7,
        retained_from_sequence=7,
        dropped_chunk_count=0,
    )

    class Store:
        def snapshot_cut(self, fleet_run_id: str, after: int):
            assert (fleet_run_id, after) == ("run-1", 7)
            return batch, retained

        def locate(self, *args, **kwargs):
            raise AssertionError("live-output reader must not reread mutable store state")

    observed = {}

    def project(tape, chunks):
        observed["tape"] = tape
        observed["chunks"] = chunks
        return ()

    monkeypatch.setattr(
        "oh_goon.services.game_tape_live_output.project_live_failure_anchors", project
    )
    detail = SimpleNamespace(run=SimpleNamespace(fleet_run_id="run-1"), game_tape=object())

    result = read_game_tape_live_output(Store(), detail, after=7)  # type: ignore[arg-type]

    assert result == batch
    assert observed == {"tape": detail.game_tape, "chunks": retained}


def test_live_output_reader_preserves_raw_chunks_when_failure_navigation_projection_fails(
    monkeypatch,
) -> None:
    retained = (
        GameTapeLiveOutputChunk(
            sequence=11,
            repository_id="repo-a",
            step_id="test",
            attempt=1,
            stream="stderr",
            line_start=9,
            line_end=9,
            text="raw failure",
            emitted_at=datetime.now(UTC),
        ),
    )
    batch = GameTapeLiveOutputBatch(
        fleet_run_id="run-degraded",
        state="ACTIVE",
        available=True,
        chunks=retained,
        next_sequence=11,
        retained_from_sequence=11,
        dropped_chunk_count=0,
    )

    class Store:
        def snapshot_cut(self, fleet_run_id: str, after: int):
            assert (fleet_run_id, after) == ("run-degraded", 0)
            return batch, retained

    def fail_projection(*_args, **_kwargs):
        raise RuntimeError("derived failure navigation broke")

    monkeypatch.setattr(
        "oh_goon.services.game_tape_live_output.project_live_failure_anchors",
        fail_projection,
    )
    detail = SimpleNamespace(run=SimpleNamespace(fleet_run_id="run-degraded"), game_tape=object())

    result = read_game_tape_live_output(Store(), detail, after=0)  # type: ignore[arg-type]

    assert result.available is True
    assert result.state == "ACTIVE"
    assert result.chunks == retained
    assert result.failure_anchors == ()
    assert result.failure_navigation_state is GameTapeFailureNavigationState.DEGRADED


def test_unavailable_live_output_marks_failure_navigation_unavailable() -> None:
    batch = GameTapeLiveOutputBatch(
        fleet_run_id="run-unavailable",
        state="UNAVAILABLE",
        available=False,
        failure_navigation_state=GameTapeFailureNavigationState.UNAVAILABLE,
        next_sequence=0,
        retained_from_sequence=0,
        dropped_chunk_count=0,
    )

    assert batch.failure_navigation_state is GameTapeFailureNavigationState.UNAVAILABLE


def test_live_output_reader_emits_identity_bound_diagnostic_when_projection_degrades(
    monkeypatch,
) -> None:
    batch = GameTapeLiveOutputBatch(
        fleet_run_id="run-diagnostic",
        state="ACTIVE",
        available=True,
        chunks=(),
        next_sequence=0,
        retained_from_sequence=0,
        dropped_chunk_count=0,
    )

    class Store:
        def snapshot_cut(self, fleet_run_id: str, after: int):
            assert (fleet_run_id, after) == ("run-diagnostic", 0)
            return batch, ()

    class Diagnostics:
        def __init__(self) -> None:
            self.events = []

        def emit(self, event) -> None:
            self.events.append(event)

    def fail_projection(*_args, **_kwargs):
        raise RuntimeError("anchor projection failed")

    monkeypatch.setattr(
        "oh_goon.services.game_tape_live_output.project_live_failure_anchors",
        fail_projection,
    )
    diagnostics = Diagnostics()
    detail = SimpleNamespace(
        run=SimpleNamespace(fleet_run_id="run-diagnostic"),
        game_tape=object(),
    )

    result = read_game_tape_live_output(
        Store(),
        detail,
        after=0,
        diagnostics=diagnostics,  # type: ignore[arg-type]
    )

    assert result.failure_navigation_state is GameTapeFailureNavigationState.DEGRADED
    assert len(diagnostics.events) == 1
    event = diagnostics.events[0]
    assert event.fleet_run_id == "run-diagnostic"
    assert event.owner == "GameTapeLiveOutputProjection"
    assert event.operation == "project_failure_anchors"
    assert event.execution_affected is False
    assert event.diagnostic_capability_affected == "failure_navigation"
