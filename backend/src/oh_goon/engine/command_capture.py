from __future__ import annotations

from threading import Event
from typing import Literal


class TextCapture:
    """Keep exact command text up to an optional byte ceiling while always draining pipes."""

    def __init__(self, limit_bytes: int | None, overflow: Event) -> None:
        self._limit_bytes = limit_bytes
        self._overflow_event = overflow
        self._parts: list[str] = []
        self._captured_bytes = 0
        self.overflow_stream: Literal["stdout", "stderr"] | None = None

    @property
    def limit_bytes(self) -> int | None:
        return self._limit_bytes

    @property
    def text(self) -> str:
        return "".join(self._parts)

    def append(self, chunk: str, *, stream: Literal["stdout", "stderr"]) -> None:
        if self._limit_bytes is None:
            self._parts.append(chunk)
            return
        if self.overflow_stream is not None:
            return
        encoded = chunk.encode("utf-8")
        remaining = self._limit_bytes - self._captured_bytes
        if len(encoded) <= remaining:
            self._parts.append(chunk)
            self._captured_bytes += len(encoded)
            return
        if remaining > 0:
            prefix = encoded[:remaining].decode("utf-8", errors="ignore")
            if prefix:
                self._parts.append(prefix)
                self._captured_bytes += len(prefix.encode("utf-8"))
        self.overflow_stream = stream
        self._overflow_event.set()


class CommandCapture:
    def __init__(self, limit_bytes: int | None) -> None:
        self.overflow = Event()
        self.stdout = TextCapture(limit_bytes, self.overflow)
        self.stderr = TextCapture(limit_bytes, self.overflow)
