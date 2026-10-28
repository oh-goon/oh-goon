from __future__ import annotations

import hashlib
from threading import Event
from typing import Literal

from oh_goon.core.models.fleet_plan_evidence import PlanOutputEvidence

_DEFAULT_TAIL_BYTES = 1024 * 1024
_TAIL_OMISSION = b"[Earlier output omitted; retained tail follows]\n"
_LINE_BREAKS = "\n\r\v\f\x1c\x1d\x1e\x85\u2028\u2029"


class TextCapture:
    """Bound retained text while draining; terminal evidence hashes the whole raw stream.

    Ordinary output fails at its byte ceiling. Explicit tail mode keeps a bounded
    UTF-8 tail with an omission marker, without treating session volume as failure.
    Its evidence describes original raw bytes, not the retained diagnostic tail.
    """

    def __init__(
        self, limit_bytes: int | None, overflow: Event, *, retain_tail: bool = False
    ) -> None:
        self._limit_bytes = (
            _DEFAULT_TAIL_BYTES if retain_tail and limit_bytes is None else limit_bytes
        )
        self._overflow_event = overflow
        self._parts: list[str] = []
        self._captured_bytes = 0
        self.overflow_stream: Literal["stdout", "stderr"] | None = None
        self._retain_tail = retain_tail
        self._tail = bytearray()
        self._omitted = False
        self._digest = hashlib.sha256()
        self._observed_bytes = 0
        self._line_breaks = 0
        self._last_cr = False
        self._last_break = False
        self._has_text = False

    @property
    def limit_bytes(self) -> int | None:
        return self._limit_bytes

    @property
    def truncated(self) -> bool:
        return self._omitted or self.overflow_stream is not None

    @property
    def text(self) -> str:
        if self._retain_tail:
            assert self._limit_bytes is not None
            marker = _TAIL_OMISSION[: self._limit_bytes] if self._omitted else b""
            remaining = self._limit_bytes - len(marker)
            tail = bytes(self._tail[-remaining:]) if remaining else b""
            return marker.decode() + tail.decode("utf-8", errors="ignore")
        return "".join(self._parts)

    @property
    def evidence(self) -> PlanOutputEvidence | None:
        if not self._retain_tail:
            return None
        return PlanOutputEvidence(
            sha256=self._digest.hexdigest(),
            byte_count=self._observed_bytes,
            line_count=self._line_breaks + int(self._has_text and not self._last_break),
        )

    def append(
        self,
        chunk: str,
        *,
        stream: Literal["stdout", "stderr"],
        raw: bytes | None = None,
    ) -> None:
        if self._retain_tail:
            encoded = chunk.encode("utf-8")
            observed = encoded if raw is None else raw
            self._digest.update(observed)
            self._observed_bytes += len(observed)
            if chunk:
                self._line_breaks += (
                    sum(chunk.count(mark) for mark in _LINE_BREAKS)
                    - chunk.count("\r\n")
                    - int(self._last_cr and chunk.startswith("\n"))
                )
                self._last_cr = chunk.endswith("\r")
                self._last_break = chunk[-1] in _LINE_BREAKS
                self._has_text = True
            self._tail.extend(encoded)
            assert self._limit_bytes is not None
            if len(self._tail) > self._limit_bytes:
                del self._tail[: -self._limit_bytes]
                self._omitted = True
            return
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
    def __init__(
        self, limit_bytes: int | None, *, terminal: bool = False, retain_output_tail: bool = False
    ) -> None:
        self.overflow = Event()
        self.stdout = TextCapture(
            limit_bytes, self.overflow, retain_tail=terminal or retain_output_tail
        )
        self.stderr = TextCapture(limit_bytes, self.overflow, retain_tail=retain_output_tail)
