"""CO-R3C: original supervisor process effects, conservative Linux observation.

These are real host process effects, NOT independent Enforcer/LACE custody.
An escaped session demonstrates why an owned-PGID receipt is insufficient to
release a workspace whose physical writer absence has not been proved.
"""

from __future__ import annotations

import os
import signal
import sys
import time
from pathlib import Path

import pytest

from oh_goon.core.models.termination import TerminationSignal
from oh_goon.engine.command import CommandAuthorityTerminatedError, run_command
from oh_goon.runtime import process_group as original_observer


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux /proc is required")
def test_partial_proc_inventory_never_asserts_absent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    good = tmp_path / "101"
    good.mkdir()
    (good / "stat").write_text("101 (unrelated) S 1 991 0 0\n", encoding="ascii")
    missing = tmp_path / "102"
    missing.mkdir()  # Permission-denied/raced entry: group cannot be excluded.
    monkeypatch.setattr(original_observer, "Path", lambda _root: tmp_path)
    assert original_observer._linux_process_group_has_live_member(772) is None
    probes = []

    def denied_probe(pgid: int, sig: int) -> None:
        probes.append((pgid, sig))
        raise PermissionError("unobservable group")

    monkeypatch.setattr(original_observer.os, "killpg", denied_probe)
    assert original_observer.process_group_exists(772) is True
    assert probes == [(772, 0)]


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux /proc is required")
def test_malformed_proc_member_also_falls_back_to_kernel(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    item = tmp_path / "900"
    item.mkdir()
    (item / "stat").write_text("incomplete", encoding="ascii")
    monkeypatch.setattr(original_observer, "Path", lambda _root: tmp_path)
    assert original_observer._linux_process_group_has_live_member(772) is None


# The leader starts a second writer in the SAME supervised process group.
# The writer ignores TERM, forcing the original bounded TERM -> KILL path.
_WRITER = r"""
import os, signal, sys, time
from pathlib import Path
signal.signal(signal.SIGTERM, signal.SIG_IGN)
path = Path(sys.argv[1])
while True:
    with path.open('a', encoding='ascii') as output:
        output.write('W\n')
        output.flush()
        os.fsync(output.fileno())
    time.sleep(0.02)
"""
_LEADER = r"""
import subprocess, sys, time
from pathlib import Path
writer = subprocess.Popen([sys.executable, '-S', '-c', sys.argv[2], sys.argv[1]],
                          close_fds=True)
path = Path(sys.argv[1])
while not path.exists() or not path.read_text(encoding='ascii'):
    time.sleep(0.01)
print('OWNED_WRITER_READY', flush=True)
time.sleep(30)
"""


@pytest.mark.process
@pytest.mark.skipif(
    not sys.platform.startswith("linux"), reason="requires native Linux process group"
)
def test_real_trust_termination_quiesces_uncooperative_owned_writer(tmp_path: Path) -> None:
    marker = tmp_path / "owned-writes"
    seen: list[str] = []

    def capture(stream: str, data: str) -> None:
        if stream == "stdout":
            seen.append(data)

    with pytest.raises(CommandAuthorityTerminatedError) as raised:
        run_command(
            sys.executable,
            "-S",
            "-c",
            _LEADER,
            str(marker),
            _WRITER,
            timeout=7,
            stream_output=capture,
            termination_requested=lambda: (
                TerminationSignal(cause="trust_revoke", reason="co-r3c-trust-epoch-lost")
                if "OWNED_WRITER_READY" in "".join(seen)
                else None
            ),
            require_process_group_quiescence=True,
        )
    receipt = raised.value.supervisor
    assert raised.value.request.cause == "trust_revoke"
    assert receipt.cleanup_reason == "authority"
    assert receipt.termination_receipt is not None
    assert receipt.termination_receipt.term_requested
    assert receipt.final_process_group_quiescent
    assert marker.read_text(encoding="ascii").count("W") > 0
    settled = marker.read_bytes()
    time.sleep(0.25)
    assert marker.read_bytes() == settled, (
        "owned writer continued physical effects after revocation"
    )


# An explicit escaped session does not belong to the owned process group.
# Seeing its effects after the group stops proves the boundary is NOT an
# independently complete writer-absence oracle. Always clean the witness.
_ESCAPED_LEADER = r"""
import subprocess, sys, time
from pathlib import Path
subprocess.Popen([sys.executable, '-S', '-c', sys.argv[2], sys.argv[1]],
                 start_new_session=True, close_fds=True,
                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
path = Path(sys.argv[1])
while not path.exists() or not path.read_text(encoding='ascii'):
    time.sleep(0.01)
print('ESCAPED_WRITER_READY', flush=True)
time.sleep(30)
"""
_ESCAPED_WRITER = r"""
import os, signal, sys, time
from pathlib import Path
signal.signal(signal.SIGTERM, signal.SIG_IGN)
path = Path(sys.argv[1])
with path.open('w', encoding='ascii') as output:
    output.write(str(os.getpid()) + '\n')
    output.flush()
    os.fsync(output.fileno())
while True:
    with path.open('a', encoding='ascii') as output:
        output.write('still-writing\n')
        output.flush()
    time.sleep(0.03)
"""


@pytest.mark.process
@pytest.mark.skipif(
    not sys.platform.startswith("linux"), reason="requires native Linux process groups"
)
def test_escaped_writer_disproves_process_group_as_independent_absence(tmp_path: Path) -> None:
    marker = tmp_path / "escaped-writes"
    seen: list[str] = []
    escaped_pid: int | None = None
    try:
        with pytest.raises(CommandAuthorityTerminatedError) as raised:
            run_command(
                sys.executable,
                "-S",
                "-c",
                _ESCAPED_LEADER,
                str(marker),
                _ESCAPED_WRITER,
                timeout=8,
                stream_output=lambda stream, chunk: seen.append(chunk)
                if stream == "stdout"
                else None,
                termination_requested=lambda: (
                    TerminationSignal(cause="trust_revoke", reason="co-r3c-escape-negative-control")
                    if "ESCAPED_WRITER_READY" in "".join(seen)
                    else None
                ),
                require_process_group_quiescence=True,
            )
        assert raised.value.supervisor.final_process_group_quiescent
        escaped_pid = int(marker.read_text(encoding="ascii").splitlines()[0])
        assert escaped_pid > 0
        before = marker.stat().st_size
        deadline = time.monotonic() + 1.5
        while marker.stat().st_size <= before and time.monotonic() < deadline:
            time.sleep(0.05)
        assert marker.stat().st_size > before, (
            "negative control must observe the independent writer still running"
        )
    finally:
        # The escaped writer is the fixture's own session leader. Never leave
        # a detached process behind, including on assertion or timeout failure.
        if escaped_pid is None and marker.exists():
            try:
                escaped_pid = int(marker.read_text(encoding="ascii").splitlines()[0])
            except (ValueError, IndexError):
                pass
        if escaped_pid is not None and escaped_pid > 0:
            try:
                os.killpg(escaped_pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
