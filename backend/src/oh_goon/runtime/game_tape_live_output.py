from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from threading import Lock
from typing import Literal

from oh_goon.core.game_tape_policy import (
    GAME_TAPE_MAX_CHUNK_CHARS,
    GAME_TAPE_MAX_CHUNKS_PER_RUN,
    GAME_TAPE_TERMINAL_RETENTION_SECONDS,
)
from oh_goon.core.models.game_tape_live_output import (
    GameTapeLiveOutputBatch,
    GameTapeLiveOutputChunk,
)
from oh_goon.core.ports.step_output import StepOutputObserver
from oh_goon.core.time import utc_now
from oh_goon.core.vocabulary import GameTapeFailureNavigationState

_TERMINAL_RETENTION = timedelta(seconds=GAME_TAPE_TERMINAL_RETENTION_SECONDS)

_StreamKey = tuple[str, str, int, Literal["stdout", "stderr"]]


@dataclass(slots=True)
class _RunBuffer:
    next_sequence: int = 0
    dropped_chunk_count: int = 0
    chunks: deque[GameTapeLiveOutputChunk] = field(default_factory=deque)
    next_line: dict[_StreamKey, int] = field(default_factory=dict)
    activity_sequence: int = 0
    activities: deque[tuple[int, str]] = field(default_factory=deque)
    expires_at: datetime | None = None


class GameTapeLiveOutputStore:
    """Bounded process-local output with an explicit short terminal handoff window."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._runs: dict[str, _RunBuffer] = {}

    def open_run(self, fleet_run_id: str) -> None:
        """Open transient capture without erasing an already observed run.

        Worker activation can be redelivered around control-plane recovery. The live
        buffer is diagnostic rather than execution authority, but replay must not erase
        output already captured for the same durable FleetRun identity.
        """
        with self._lock:
            self._expire_locked()
            if fleet_run_id not in self._runs:
                self._runs[fleet_run_id] = _RunBuffer()

    def append(
        self,
        fleet_run_id: str,
        repository_id: str,
        step_id: str,
        attempt: int,
        stream: str,
        text: str,
    ) -> None:
        if not text:
            return
        if stream not in {"stdout", "stderr"}:
            raise ValueError(f"Unsupported Game Tape live output stream: {stream}")
        typed_stream: Literal["stdout", "stderr"] = stream
        with self._lock:
            self._expire_locked()
            buffer = self._runs.get(fleet_run_id)
            if buffer is None or buffer.expires_at is not None:
                return
            key: _StreamKey = (repository_id, step_id, attempt, typed_stream)
            line = buffer.next_line.get(key, 1)
            for fragment in _fragments(text):
                line_start = line
                newline_count = fragment.count("\n")
                line_end = line + newline_count
                if newline_count > 0 and fragment.endswith("\n"):
                    line_end -= 1
                line_end = max(line_start, line_end)
                buffer.next_sequence += 1
                buffer.chunks.append(
                    GameTapeLiveOutputChunk(
                        sequence=buffer.next_sequence,
                        repository_id=repository_id,
                        step_id=step_id,
                        attempt=attempt,
                        stream=typed_stream,
                        line_start=line_start,
                        line_end=line_end,
                        text=fragment,
                        emitted_at=utc_now(),
                    )
                )
                line += newline_count
                while len(buffer.chunks) > GAME_TAPE_MAX_CHUNKS_PER_RUN:
                    buffer.chunks.popleft()
                    buffer.dropped_chunk_count += 1
            buffer.next_line[key] = line

    def append_activity(self, fleet_run_id: str, message: str) -> None:
        """Record one bounded process-local run activity hint.

        Activity is deliberately separate from Step stdout/stderr: preparation can be
        expensive before any Step process exists, and pretending those lifecycle facts
        are Step output would create a second false execution history.
        """

        rendered = message.strip()
        if not rendered:
            return
        with self._lock:
            self._expire_locked()
            buffer = self._runs.get(fleet_run_id)
            if buffer is None or buffer.expires_at is not None:
                return
            buffer.activity_sequence += 1
            buffer.activities.append((buffer.activity_sequence, rendered[:512]))
            while len(buffer.activities) > GAME_TAPE_MAX_CHUNKS_PER_RUN:
                buffer.activities.popleft()

    def activity_snapshot(
        self, fleet_run_id: str, *, after: int = 0
    ) -> tuple[int, tuple[tuple[int, str], ...]]:
        """Return incremental transient lifecycle hints for local wait/progress UX."""

        with self._lock:
            self._expire_locked()
            buffer = self._runs.get(fleet_run_id)
            if buffer is None:
                return after, ()
            return (
                buffer.activity_sequence,
                tuple(item for item in buffer.activities if item[0] > after),
            )

    def seal(self, fleet_run_id: str) -> None:
        """Retain terminal output briefly for failure navigation, then expire it lazily."""
        with self._lock:
            self._expire_locked()
            buffer = self._runs.get(fleet_run_id)
            if buffer is not None and buffer.expires_at is None:
                buffer.expires_at = utc_now() + _TERMINAL_RETENTION

    def snapshot(self, fleet_run_id: str, after: int = 0) -> GameTapeLiveOutputBatch:
        return self.snapshot_cut(fleet_run_id, after)[0]

    def snapshot_cut(
        self, fleet_run_id: str, after: int = 0
    ) -> tuple[GameTapeLiveOutputBatch, tuple[GameTapeLiveOutputChunk, ...]]:
        """Return one exact retained-buffer cut plus the requested incremental batch."""
        with self._lock:
            self._expire_locked()
            buffer = self._runs.get(fleet_run_id)
            if buffer is None:
                return (
                    GameTapeLiveOutputBatch(
                        fleet_run_id=fleet_run_id,
                        state="UNAVAILABLE",
                        available=False,
                        failure_navigation_state=GameTapeFailureNavigationState.UNAVAILABLE,
                        next_sequence=0,
                        retained_from_sequence=0,
                        dropped_chunk_count=0,
                    ),
                    (),
                )
            retained = tuple(buffer.chunks)
            retained_from = retained[0].sequence if retained else buffer.next_sequence
            chunks = tuple(item for item in retained if item.sequence > after)
            latest_activity = buffer.activities[-1][1] if buffer.activities else None
            state = "SEALED" if buffer.expires_at is not None else "ACTIVE"
            return (
                GameTapeLiveOutputBatch(
                    fleet_run_id=fleet_run_id,
                    state=state,
                    available=True,
                    expires_at=buffer.expires_at,
                    chunks=chunks,
                    latest_activity=latest_activity,
                    activity_sequence=buffer.activity_sequence,
                    next_sequence=buffer.next_sequence,
                    retained_from_sequence=retained_from,
                    dropped_chunk_count=buffer.dropped_chunk_count,
                ),
                retained,
            )

    def observer(self, fleet_run_id: str, repository_id: str) -> StepOutputObserver:
        def observe(step_id: str, attempt: int, stream: str, text: str) -> None:
            self.append(fleet_run_id, repository_id, step_id, attempt, stream, text)

        return observe

    def clear(self, fleet_run_id: str) -> None:
        with self._lock:
            self._runs.pop(fleet_run_id, None)

    def _expire_locked(self) -> None:
        now = utc_now()
        expired = [
            run_id
            for run_id, buffer in self._runs.items()
            if buffer.expires_at is not None and buffer.expires_at <= now
        ]
        for run_id in expired:
            self._runs.pop(run_id, None)


def _fragments(text: str) -> tuple[str, ...]:
    return tuple(
        text[index : index + GAME_TAPE_MAX_CHUNK_CHARS]
        for index in range(0, len(text), GAME_TAPE_MAX_CHUNK_CHARS)
    )
