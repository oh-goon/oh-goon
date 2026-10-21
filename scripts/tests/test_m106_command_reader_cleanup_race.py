from __future__ import annotations

import threading
from threading import Event

from oh_goon.engine.command import _reader
from oh_goon.engine.command_capture import TextCapture


class _CloseWhileReadIsBlocked:
    def __init__(self) -> None:
        self.entered = Event()
        self.release = Event()
        self.closed = False
        self.encoding = "utf-8"
        self.errors = "strict"
        self.buffer = self

    def read1(self, _size: int) -> bytes:
        self.entered.set()
        assert self.release.wait(timeout=1), "reader barrier was not released"
        if self.closed:
            raise ValueError("I/O operation on closed file")
        return b""

    def close(self) -> None:
        self.closed = True
        self.release.set()


def test_owned_pipe_close_does_not_escape_from_output_reader_thread() -> None:
    pipe = _CloseWhileReadIsBlocked()
    capture = TextCapture(limit_bytes=None, overflow=Event())
    errors: list[BaseException] = []
    uncaught: list[BaseException | None] = []
    previous_hook = threading.excepthook

    def record_uncaught(args: threading.ExceptHookArgs) -> None:
        uncaught.append(args.exc_value)

    threading.excepthook = record_uncaught
    try:
        reader = _reader(pipe, "stdout", capture, observer=None, errors=errors)
        reader.start()
        assert pipe.entered.wait(timeout=1), "reader never entered pipe.read()"
        pipe.close()
        reader.join(timeout=1)
        assert not reader.is_alive(), "reader did not terminate after owned pipe close"
    finally:
        threading.excepthook = previous_hook

    assert uncaught == []
    assert errors == []
