from __future__ import annotations

import signal
from pathlib import Path

import pytest

from oh_goon.core.launch_control import LaunchStopTarget, safe_launch_stop_target, signal_launch_stop
from oh_goon.core.process_birth import ProcessBirthIdentity
from oh_goon.runtime.local_launch_binding import LocalLaunchBindingStore
from oh_goon.runtime.local_launch_owner import LocalLaunchOwner, built_local_intent
from oh_goon.runtime.local_launch_state import LocalLaunchEventStore


def _owner(tmp_path: Path) -> LocalLaunchOwner:
    events = LocalLaunchEventStore(root=tmp_path / "launches")
    bindings = LocalLaunchBindingStore.from_event_store(events)
    return LocalLaunchOwner.create(
        intent=built_local_intent(host="127.0.0.1", port=8787),
        workspace=tmp_path / "repo",
        asserted_by="cli-start",
        store=events,
        binding_store=bindings,
    )


def test_safe_stop_requires_exact_current_birth_identity(tmp_path: Path) -> None:
    (tmp_path / "repo").mkdir()
    owner = _owner(tmp_path)
    manifest = owner.store.manifest(owner.identity.launch_id)
    expected = manifest.observed.owner_process.birth_identity.value  # type: ignore[union-attr]

    target = safe_launch_stop_target(manifest, process_observer=lambda _pid: expected)
    assert target is not None
    assert target.expected_birth == expected

    reused = ProcessBirthIdentity(
        pid=expected.pid,
        boot_id=expected.boot_id,
        start_ticks=expected.start_ticks + 1,
    )
    with pytest.raises(ValueError, match="cannot be re-proven"):
        safe_launch_stop_target(manifest, process_observer=lambda _pid: reused)


def test_signal_stop_uses_pidfd_and_reproves_birth_after_handle_acquisition() -> None:
    expected = ProcessBirthIdentity(pid=123, boot_id="boot", start_ticks=44)
    target = LaunchStopTarget(launch_id="launch-1", owner_generation=1, expected_birth=expected)
    calls: list[object] = []

    signal_launch_stop(
        target,
        process_observer=lambda _pid: expected,
        pidfd_open=lambda pid, flags: calls.append(("open", pid, flags)) or 77,
        pidfd_send_signal=lambda fd, sig, info, flags: calls.append(("signal", fd, sig, info, flags)),
        close_fd=lambda fd: calls.append(("close", fd)),
    )
    assert calls == [
        ("open", 123, 0),
        ("signal", 77, signal.SIGTERM, None, 0),
        ("close", 77),
    ]


def test_signal_stop_refuses_pid_reuse_race_after_pidfd_open() -> None:
    expected = ProcessBirthIdentity(pid=123, boot_id="boot", start_ticks=44)
    reused = ProcessBirthIdentity(pid=123, boot_id="boot", start_ticks=45)
    target = LaunchStopTarget(launch_id="launch-1", owner_generation=1, expected_birth=expected)
    calls: list[object] = []

    with pytest.raises(ValueError, match="changed before stable stop handle"):
        signal_launch_stop(
            target,
            process_observer=lambda _pid: reused,
            pidfd_open=lambda pid, flags: calls.append(("open", pid, flags)) or 77,
            pidfd_send_signal=lambda fd, sig, info, flags: calls.append(("signal", fd)),
            close_fd=lambda fd: calls.append(("close", fd)),
        )
    assert calls == [("open", 123, 0), ("close", 77)]
