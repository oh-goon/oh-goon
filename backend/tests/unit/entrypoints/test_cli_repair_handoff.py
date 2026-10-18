from __future__ import annotations

from datetime import datetime, timezone

from oh_goon.cli_runtime_plans import _repair_output_tail
from oh_goon.core.models.game_tape_live_output import (
    GameTapeLiveOutputBatch,
    GameTapeLiveOutputChunk,
)


def _chunk(sequence: int, *, step: str, attempt: int, stream: str, text: str) -> GameTapeLiveOutputChunk:
    return GameTapeLiveOutputChunk(
        sequence=sequence,
        repository_id="repo-test",
        step_id=step,
        attempt=attempt,
        stream=stream,
        line_start=sequence,
        line_end=sequence,
        text=text,
        emitted_at=datetime.now(timezone.utc),
    )


def test_repair_output_tail_uses_only_failed_step_latest_attempt_and_is_bounded() -> None:
    chunks = [
        _chunk(1, step="other", attempt=1, stream="stderr", text="ignore"),
        _chunk(2, step="setup", attempt=1, stream="stderr", text="old-attempt"),
        _chunk(3, step="setup", attempt=2, stream="stdout", text="start"),
    ]
    chunks.extend(
        _chunk(4 + index, step="setup", attempt=2, stream="stderr", text=f"line-{index}")
        for index in range(50)
    )
    batch = GameTapeLiveOutputBatch(
        fleet_run_id="fleet-test",
        state="ACTIVE",
        available=True,
        chunks=tuple(chunks),
        next_sequence=100,
        retained_from_sequence=1,
        dropped_chunk_count=0,
    )

    lines, truncated = _repair_output_tail(batch, "setup")

    assert truncated is True
    assert len(lines) <= 40
    assert all("ignore" not in line and "old-attempt" not in line for line in lines)
    assert lines[-1] == "[stderr] line-49"


def test_repair_output_tail_enforces_character_budget() -> None:
    chunks = tuple(
        _chunk(
            index + 1,
            step="setup",
            attempt=1,
            stream="stderr",
            text="x" * 1000,
        )
        for index in range(20)
    )
    batch = GameTapeLiveOutputBatch(
        fleet_run_id="fleet-test",
        state="ACTIVE",
        available=True,
        chunks=chunks,
        next_sequence=21,
        retained_from_sequence=1,
        dropped_chunk_count=0,
    )

    lines, truncated = _repair_output_tail(batch, "setup")

    assert truncated is True
    assert sum(len(line) + 1 for line in lines) <= 8_000
    assert lines
