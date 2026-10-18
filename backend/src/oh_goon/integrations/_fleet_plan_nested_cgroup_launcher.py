from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.integrations._fleet_plan_cgroup_resources import (
    _refuse_unprovisioned_kernel_effect,
    _write_process_to_cgroup,
)


def _move_process(cgroup: Path, pid: int, *, identity: tuple[int, int]) -> None:
    _write_process_to_cgroup(cgroup, pid, expected_identity=identity)


def _bwrap_with_coordination_fds(
    argv: Sequence[str], *, info_fd: int, block_fd: int
) -> tuple[str, ...]:
    if not argv:
        raise FleetPlanInvalid("Nested strict execution launcher received an empty command")
    return (
        argv[0],
        "--info-fd",
        str(info_fd),
        "--block-fd",
        str(block_fd),
        "--as-pid-1",
        *argv[1:],
    )


def _read_child_pid(process: subprocess.Popen[bytes], info_read: int) -> int:
    with os.fdopen(info_read, "r", encoding="utf-8", closefd=True) as stream:
        line = stream.readline()
    try:
        child_pid = int(json.loads(line)["child-pid"])
    except (ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
        process.terminate()
        process.wait()
        raise FleetPlanInvalid(
            "Nested strict execution could not obtain Bubblewrap child identity"
        ) from exc
    if child_pid <= 0:
        process.terminate()
        process.wait()
        raise FleetPlanInvalid(
            f"Nested strict execution received invalid Bubblewrap child pid {child_pid}"
        )
    return child_pid


def _close_fds(*fds: int) -> None:
    for fd in fds:
        if fd < 0:
            continue
        try:
            os.close(fd)
        except OSError:
            pass


def launch_nested_bubblewrap(
    nested_root: Path,
    monitor_root: Path,
    argv: Sequence[str],
    *,
    nested_identity: tuple[int, int],
    monitor_identity: tuple[int, int],
    popen: Callable[..., subprocess.Popen[bytes]] = subprocess.Popen,
    move_process: Callable[..., None] = _move_process,
) -> int:
    """Launch bwrap with an empty delegated root before COMMAND is released.

    Bubblewrap itself must enter ``nested_root`` before ``--unshare-cgroup`` so the
    sandbox's cgroup namespace is rooted at exactly the delegated child mounted at
    /sys/fs/cgroup.  But bwrap also retains an outer supervisor process.  If that
    supervisor remains in ``nested_root``, cgroup-v2's no-internal-process rule makes
    it impossible for COMMAND to enable cpu/memory/pids for its own child executor.

    ``--block-fd`` holds COMMAND after sandbox setup.  Once ``--info-fd`` reports the
    sandbox child, this launcher moves itself and the bwrap supervisor into the
    attempt-owned sibling ``monitor_root``.  ``--as-pid-1`` avoids a separate reaper
    process inside the delegated root.  Only then is COMMAND released; it can move
    itself into its declared process subgroup and establish child controllers.
    """

    # A caller-supplied move_process, including one that silently succeeds,
    # cannot bypass the original LACE kernel custody boundary. Check BEFORE
    # pipe creation, process launch, or cgroup.procs writes.
    _refuse_unprovisioned_kernel_effect(
        nested_root, operation="native nested cgroup process launch",
    )
    _refuse_unprovisioned_kernel_effect(
        monitor_root, operation="native monitor cgroup process launch",
    )
    # Do not resolve a substituted pathname into the new owner's authority.
    move_process(nested_root, os.getpid(), identity=nested_identity)

    info_read, info_write = os.pipe()
    block_read, block_write = os.pipe()
    try:
        command = _bwrap_with_coordination_fds(
            argv, info_fd=info_write, block_fd=block_read
        )
        process = popen(command, pass_fds=(info_write, block_read))
        os.close(info_write)
        info_write = -1
        os.close(block_read)
        block_read = -1

        _read_child_pid(process, info_read)
        info_read = -1

        # The info payload proves sandbox setup has created COMMAND but --block-fd
        # still prevents it from running repository code.  Empty the delegated root
        # before releasing that code.
        move_process(monitor_root, os.getpid(), identity=monitor_identity)
        move_process(monitor_root, process.pid, identity=monitor_identity)
        os.write(block_write, b"1")
        os.close(block_write)
        block_write = -1

        return_code = process.wait()
        if return_code < 0:
            os.kill(os.getpid(), -return_code)
            raise AssertionError("signal propagation returned unexpectedly")
        return return_code
    finally:
        _close_fds(info_read, info_write, block_read, block_write)


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if "--" not in args:
        raise FleetPlanInvalid(
            "Nested strict execution launcher requires '<nested-root> <monitor-root> <dev> <ino> <dev> <ino> -- <bwrap...>'"
        )
    separator = args.index("--")
    if separator != 6 or len(args) <= 7:
        raise FleetPlanInvalid("Nested strict execution launcher lacks creation-bound authority")
    try:
        dev, ino, monitor_dev, monitor_ino = (int(item) for item in args[2:6])
    except ValueError as exc:
        raise FleetPlanInvalid("Nested strict execution launcher identity invalid") from exc
    if min(dev, ino, monitor_dev, monitor_ino) <= 0:
        raise FleetPlanInvalid("Nested strict execution launcher identity unproven")
    return launch_nested_bubblewrap(
        Path(args[0]), Path(args[1]), args[separator + 1 :],
        nested_identity=(dev, ino), monitor_identity=(monitor_dev, monitor_ino),
    )


if __name__ == "__main__":
    raise SystemExit(main())
