"""Birth-bound observations of one child below an original Journal process."""

from __future__ import annotations

import os
import select
import signal
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from threading import Event
from time import monotonic

from oh_goon.core.models.run_events import ProcessStartedEvent
from oh_goon.core.process_birth import ProcessBirthIdentity, observe_process_birth


@dataclass(frozen=True)
class NativeDescendant:
    root: ProcessBirthIdentity
    parent: ProcessBirthIdentity
    child: ProcessBirthIdentity
    parent_fd: int
    child_fd: int
    parent_pgid: int
    child_pgid: int


def _child_with_token(root: int, token: bytes) -> tuple[int, int] | None:
    pending = [root]
    seen = {root}
    matches: list[tuple[int, int]] = []
    task_count = 0
    while pending:
        parent = pending.pop()
        try:
            tasks = os.scandir(Path("/proc") / str(parent) / "task")
        except FileNotFoundError:
            continue
        with tasks:
            for task in tasks:
                task_count += 1
                assert task_count <= 256, "native fixture task inventory exceeded its bound"
                try:
                    with (Path(task.path) / "children").open("rb") as source:
                        raw = source.read(4097)
                except FileNotFoundError:
                    continue
                assert len(raw) <= 4096
                for value in raw.split():
                    assert value.isdigit()
                    child = int(value)
                    if child in seen:
                        continue
                    seen.add(child)
                    assert len(seen) <= 64, "native fixture descendant inventory exceeded its bound"
                    pending.append(child)
                    try:
                        with (Path("/proc") / str(child) / "cmdline").open("rb") as source:
                            arguments = source.read(65537)
                    except FileNotFoundError:
                        continue
                    assert len(arguments) <= 65536
                    if token in arguments.split(b"\0"):
                        matches.append((parent, child))
    assert len(matches) <= 1, "native fixture child token is not unique in its owned tree"
    return matches[0] if matches else None


@contextmanager
def bind_native_descendant(
    root_event: ProcessStartedEvent, child_token: str, *, timeout_seconds: float = 10
) -> Iterator[NativeDescendant]:
    """Keep exact kernel handles through terminal readback, without normal-path effects.

    Only the supplied Journal process tree is inspected. An exceptional caller
    exit kills the already birth-bound fixture child; this cannot make a failed
    terminal-death assertion pass. Normal exit only closes observation handles.
    """
    descriptors: list[int] = []
    child_fd: int | None = None
    child_birth: ProcessBirthIdentity | None = None

    def bind(pid: int) -> tuple[int, ProcessBirthIdentity]:
        descriptor = os.pidfd_open(pid)
        descriptors.append(descriptor)
        birth = observe_process_birth(pid)
        assert birth is not None, "native lifetime cut has no observable process birth"
        assert not select.select([descriptor], [], [], 0)[0], "native lifetime cut already exited"
        return descriptor, birth

    try:
        root_fd, root_birth = bind(root_event.pid)
        assert root_event.process_identity == f"{root_birth.boot_id}:{root_birth.start_ticks}"
        deadline = monotonic() + timeout_seconds
        while True:
            assert not select.select([root_fd], [], [], 0)[0], (
                "original Journal process exited before child binding"
            )
            assert observe_process_birth(root_event.pid) == root_birth
            match = _child_with_token(root_event.pid, child_token.encode())
            if match is not None:
                parent_fd, parent_birth = bind(match[0])
                child_fd, child_birth = bind(match[1])
                assert observe_process_birth(root_event.pid) == root_birth
                yield NativeDescendant(
                    root_birth,
                    parent_birth,
                    child_birth,
                    parent_fd,
                    child_fd,
                    os.getpgid(parent_birth.pid),
                    os.getpgid(child_birth.pid),
                )
                return
            assert monotonic() < deadline, "native lifetime child cut not reached"
            Event().wait(0.01)
    except BaseException as failure:
        if child_fd is not None and child_birth is not None:
            try:
                if not select.select([child_fd], [], [], 0)[0]:
                    signal.pidfd_send_signal(child_fd, signal.SIGKILL)
                    assert select.select([child_fd], [], [], 5)[0], (
                        "fixture child cleanup is incomplete"
                    )
            except BaseException as cleanup:
                failure.add_note(f"Exact fixture-child cleanup failed: {type(cleanup).__name__}")
        raise
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)
