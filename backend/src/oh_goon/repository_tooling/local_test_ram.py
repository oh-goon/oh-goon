"""Own an optional bounded Node compile cache after native test CPU admission."""

from __future__ import annotations

import argparse
import json
import os
import select
import shutil
import signal
import stat
import sys
import tempfile
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from threading import Event, Thread
from types import FrameType

from oh_goon.core.process_birth import observe_process_birth
from oh_goon.engine.command import (
    CommandCancelledError,
    CommandProcessLifecycleEvent,
    CommandResult,
    run_command,
)
from oh_goon.repository_tooling.pytest_resource_budget import (
    ESTIMATED_WORKER_MEMORY_BYTES,
    configured_cpu_budget,
    resolve_pytest_resource_budget,
)

CACHE_BYTES = 512 * 1024**2
HEADROOM_BYTES = 4 * 1024**3
_MODULE = "oh_goon.repository_tooling.local_test_ram"


@dataclass(frozen=True, slots=True)
class RamCacheResult:
    returncode: int
    cache: str
    cleanup: str
    native_process_cleanup: str = "verified"


def _stream(name: str, text: str) -> None:
    print(text, end="", file=sys.stdout if name == "stdout" else sys.stderr, flush=True)


def _cleanup(root: Path, identity: tuple[int, int]) -> bool:
    try:
        current = root.lstat()
        if root.is_symlink() or (current.st_dev, current.st_ino) != identity:
            raise OSError("owned scratch identity changed")
        entries = set(root.iterdir())
        if entries - {root / "cache"}:
            raise OSError("unexpected entries in owned scratch")
        if root / "cache" in entries:
            (root / "cache").rmdir()
        root.rmdir()
        return True
    except OSError:
        print(
            f"RAM compile cache cleanup incomplete; owned scratch retained: {root}", file=sys.stderr
        )
        return False


def _caller_descriptors() -> tuple[tuple[int, tuple[int, int, int]], ...]:
    """Preserve inherited native FD numbers, without interpreting Make authority."""
    descriptors = []
    for entry in Path("/proc/self/fd").iterdir():
        descriptor = int(entry.name)
        if descriptor <= 2:
            continue
        try:
            if os.get_inheritable(descriptor):
                metadata = os.fstat(descriptor)
                descriptors.append(
                    (descriptor, (metadata.st_dev, metadata.st_ino, metadata.st_mode))
                )
        except OSError:
            continue
    return tuple(descriptors)


def _cache_access(path: Path) -> bool:
    """Observe the actual bounded mount from the original workload namespace."""
    try:
        directory = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            capacity = os.fstatvfs(directory)
            if not 0 < capacity.f_blocks * capacity.f_frsize <= CACHE_BYTES:
                return False
            probe = os.open(
                ".admission-probe",
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                0o600,
                dir_fd=directory,
            )
            try:
                os.write(probe, b"1")
            finally:
                os.close(probe)
                os.unlink(".admission-probe", dir_fd=directory)
            return True
        finally:
            os.close(directory)
    except OSError:
        return False


def run_profiled_command(
    command: Sequence[str],
    *,
    resolved_cpus: int,
    environment: Mapping[str, str] | None = None,
    cancel_requested: Callable[[], bool] | None = None,
    cancellation_signal: Callable[[], int | None] | None = None,
) -> RamCacheResult:
    """Give one unchanged native workload an optional cache, never a new context.

    Only a keeper enters a rootless user/mount namespace. The original command
    retains its UID, groups and namespaces and accesses that mount through procfs.
    Native Node owns cache validity; native command supervision owns both groups.
    """
    cpus = configured_cpu_budget({"OH_GOON_TEST_CPUS": str(resolved_cpus)})
    if not cpus or not command:
        raise ValueError("RAM test profile requires resolved positive CPUs and a command")
    caller_descriptors = _caller_descriptors()
    env = dict(os.environ if environment is None else environment)
    budget = resolve_pytest_resource_budget(cpus, environ={"OH_GOON_TEST_CPUS": str(cpus)})
    if (
        budget.cgroup_usage_started is None
        or not budget.cgroup_usage_started.pid_bound
        or budget.cgroup_cpu_capacity is None
        or budget.cgroup_cpu_capacity > cpus
    ):
        raise ValueError("RAM test profile CPU quota is unverified in the receiving process")

    def cancelled() -> bool:
        return (cancel_requested is not None and cancel_requested()) or (
            cancellation_signal is not None and cancellation_signal() is not None
        )

    def cancellation_returncode() -> int:
        requested = cancellation_signal() if cancellation_signal is not None else None
        return -(signal.SIGTERM if requested is None else requested)

    def execute(
        argv: Sequence[str],
        selected_env: Mapping[str, str],
        *,
        abort_if: Callable[[], bool] | None = None,
    ) -> tuple[int, bool]:
        for descriptor, identity in caller_descriptors:
            metadata = os.fstat(descriptor)
            if (metadata.st_dev, metadata.st_ino, metadata.st_mode) != identity:
                raise ValueError("Inherited native command descriptor changed")
        try:
            result = run_command(
                *argv,
                env=selected_env,
                environment_context="trusted_developer",
                timeout=None,
                cancel_requested=lambda: cancelled() or (abort_if is not None and abort_if()),
                cancellation_signal=cancellation_signal,
                stream_output=_stream,
                require_process_group_quiescence=True,
                parent_death_signal=True,
                pass_fds=tuple(descriptor for descriptor, _ in caller_descriptors),
                max_capture_bytes_per_stream=64 * 1024,
                retain_output_tail=True,
            )
        except CommandCancelledError as error:
            return -error.signal_number, error.supervisor.final_process_group_quiescent
        except OSError as error:
            print(f"Native command could not start: {error.strerror}", file=sys.stderr)
            return (127 if isinstance(error, FileNotFoundError) else 126), True
        return result.returncode, result.supervisor.final_process_group_quiescent

    if cancelled():
        return RamCacheResult(cancellation_returncode(), "cancelled", "not-required")
    reason = "caller-native-setting"
    eligible = not any(name in env for name in ("NODE_COMPILE_CACHE", "NODE_DISABLE_COMPILE_CACHE"))
    needed = HEADROOM_BYTES + budget.worker_ceiling * ESTIMATED_WORKER_MEMORY_BYTES + CACHE_BYTES
    if eligible:
        eligible = (
            budget.host_available_memory_bytes is not None
            and budget.available_memory_bytes is not None
            and budget.available_memory_bytes >= needed
            and bool(budget.cgroups)
            and all(
                row.memory_limit_observed
                and row.memory_high_observed
                and (row.memory_max_bytes is None or row.memory_current_bytes is not None)
                and (
                    row.memory_high_bytes is None
                    or (
                        row.memory_current_bytes is not None
                        and row.memory_high_bytes - row.memory_current_bytes >= needed
                    )
                )
                for row in budget.cgroups
            )
        )
        reason = "insufficient-or-unknown-memory-headroom"
    unshare = shutil.which("unshare", path=env.get("PATH", os.defpath))
    mount = shutil.which("mount", path=env.get("PATH", os.defpath))
    if eligible and (
        unshare is None or mount is None or sys.platform != "linux" or not hasattr(os, "pidfd_open")
    ):
        eligible = False
        reason = "native-user-namespace-unavailable"
    if not eligible:
        print(f"RAM compile cache declined: {reason}; original command unchanged", file=sys.stderr)
        code, settled = execute(command, env)
        return RamCacheResult(
            code,
            "declined",
            "verified" if settled else "incomplete",
            "verified" if settled else "incomplete",
        )

    identity: tuple[int, int] | None = None
    stop = Event()
    done = Event()
    started = Event()
    keeper_results: list[CommandResult | CommandCancelledError | BaseException] = []
    keeper_pid: list[int] = []
    keeper_fds: list[int] = []
    keeper_identity: list[str] = []
    read_fd: int | None = None
    write_fd: int | None = None
    thread: Thread | None = None
    native_settled = True
    cleanup = False
    code = 1
    cache = "fallback"

    try:
        root = Path(tempfile.mkdtemp(prefix="oh-goon-node-cache-", dir="/tmp"))
    except OSError:
        print(
            "RAM compile cache unavailable before work; original command unchanged", file=sys.stderr
        )
        code, settled = execute(command, env)
        return RamCacheResult(
            code,
            "fallback",
            "verified" if settled else "incomplete",
            "verified" if settled else "incomplete",
        )

    def process(event: CommandProcessLifecycleEvent) -> None:
        if event.kind == "PROCESS_STARTED":
            if event.pid is None or event.process_identity is None:
                raise ValueError("Keeper process has no original native birth identity")
            keeper_pid.append(event.pid)
            keeper_identity.append(event.process_identity)
            keeper_fds.append(os.pidfd_open(event.pid))
            started.set()

    def keep() -> None:
        try:
            assert unshare is not None and mount is not None
            assert identity is not None and write_fd is not None
            keeper_results.append(
                run_command(
                    unshare,
                    "--user",
                    "--map-root-user",
                    "--mount",
                    "--propagation",
                    "private",
                    "--",
                    sys.executable,
                    "-I",
                    "-m",
                    _MODULE,
                    "--namespace-keeper",
                    str(root),
                    str(write_fd),
                    str(identity[0]),
                    str(identity[1]),
                    mount,
                    env=env,
                    timeout=None,
                    pass_fds=(write_fd,),
                    process_observer=process,
                    # Keep the cache alive while the native workload handles
                    # its signal and settles. Only its owner closes this lease.
                    cancel_requested=stop.is_set,
                    require_process_group_quiescence=True,
                    parent_death_signal=True,
                    max_capture_bytes_per_stream=8192,
                )
            )
        except BaseException as error:
            keeper_results.append(error)
        finally:
            done.set()

    try:
        valid = False
        try:
            metadata = root.lstat()
            identity = metadata.st_dev, metadata.st_ino
            read_fd, write_fd = os.pipe2(os.O_CLOEXEC | os.O_NONBLOCK)
            (root / "cache").mkdir(mode=0o700)
            thread = Thread(target=keep, name="oh-goon-node-cache-keeper")
            thread.start()
            deadline = time.monotonic() + 15
            ready = b""
            while not done.is_set() and time.monotonic() < deadline:
                if cancelled():
                    break
                if select.select([read_fd], [], [], 0.02)[0]:
                    ready = os.read(read_fd, 1024)
                    break
            if ready and started.is_set() and not done.is_set():
                binding = json.loads(ready)
                birth = observe_process_birth(keeper_pid[0])
                poll = select.poll()
                poll.register(keeper_fds[0], select.POLLIN)
                valid = (
                    binding == {"pid": keeper_pid[0]}
                    and birth is not None
                    and f"{birth.boot_id}:{birth.start_ticks}" == keeper_identity[0]
                    and not poll.poll(0)
                )
                valid = (
                    valid
                    and _cache_access(Path(f"/proc/{keeper_pid[0]}/root{root}/cache"))
                    and not poll.poll(0)
                    and not done.is_set()
                )
        except (OSError, RuntimeError) as error:
            # This handler covers optional preflight only. A native execution
            # failure below must never be turned into another invocation.
            valid = False
            print(
                f"RAM compile cache setup unavailable before work ({type(error).__name__})",
                file=sys.stderr,
            )
        keeper_cancelled = bool(keeper_results) and (
            isinstance(keeper_results[0], CommandCancelledError)
            or (isinstance(keeper_results[0], CommandResult) and keeper_results[0].returncode < 0)
        )
        if keeper_cancelled or cancelled():
            code, cache = cancellation_returncode(), "cancelled"
        elif valid:
            cache = "owned"
            selected_env = {**env, "NODE_COMPILE_CACHE": f"/proc/{keeper_pid[0]}/root{root}/cache"}
            print(
                f"RAM compile cache: private tmpfs, {CACHE_BYTES} bytes maximum; native Node cache validity",
                file=sys.stderr,
            )
            native_settled = False
            code, native_settled = execute(command, selected_env, abort_if=done.is_set)
            if done.is_set() and not cancelled():
                cache = "lost"
                print("RAM compile cache keeper lost during native work; no retry", file=sys.stderr)
        else:
            print(
                "RAM compile cache preflight unavailable; original command will run once",
                file=sys.stderr,
            )
    finally:
        # The native owner has completed its bounded cleanup attempt. On an
        # unqualified result, close this evictable cache but retain its root and
        # report incomplete cleanup; do not invent a background lease/reaper.
        stop.set()
        if thread is not None and thread.ident is not None:
            thread.join()
        descriptor_cleanup = True
        for descriptor in (read_fd, write_fd, *keeper_fds):
            if descriptor is not None:
                try:
                    os.close(descriptor)
                except OSError:
                    descriptor_cleanup = False
                    print("RAM compile cache descriptor cleanup incomplete", file=sys.stderr)
        never_started = thread is None or thread.ident is None
        settled = never_started or (
            bool(keeper_results)
            and isinstance(keeper_results[0], (CommandResult, CommandCancelledError))
            and keeper_results[0].supervisor.final_process_group_quiescent
        )
        if native_settled and settled and identity is not None:
            cleanup = _cleanup(root, identity) and descriptor_cleanup
        else:
            print(
                f"RAM compile cache retained: process cleanup unverified: {root}", file=sys.stderr
            )
    if cache == "fallback":
        if not settled:
            return RamCacheResult(1, "unavailable", "incomplete")
        if cancelled():
            return RamCacheResult(
                cancellation_returncode(), "cancelled", "verified" if cleanup else "incomplete"
            )
        code, native_settled = execute(command, env)
    return RamCacheResult(
        code,
        cache,
        "verified" if cleanup and native_settled else "incomplete",
        "verified" if native_settled else "incomplete",
    )


def _namespace_keeper(arguments: list[str]) -> int:
    root = Path(arguments[0])
    descriptor, device, inode = map(int, arguments[1:4])
    mount = arguments[4]
    metadata = root.lstat()
    if (
        root.resolve(strict=True) != root
        or not stat.S_ISDIR(metadata.st_mode)
        or stat.S_IMODE(metadata.st_mode) != 0o700
        or (metadata.st_dev, metadata.st_ino) != (device, inode)
        or not stat.S_ISFIFO(os.fstat(descriptor).st_mode)
    ):
        raise ValueError("Private RAM cache authority changed")
    result = run_command(
        mount,
        "-t",
        "tmpfs",
        "-o",
        f"size={CACHE_BYTES},noexec,nodev,nosuid,mode=0700",
        "tmpfs",
        str(root / "cache"),
        timeout=10,
        require_process_group_quiescence=True,
        parent_death_signal=True,
        max_capture_bytes_per_stream=8192,
    )
    if result.returncode or not result.supervisor.final_process_group_quiescent:
        return 125
    os.write(descriptor, json.dumps({"pid": os.getpid()}).encode())
    os.close(descriptor)
    while True:
        signal.pause()


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments and arguments[0] == "--namespace-keeper":
        return _namespace_keeper(arguments[1:])
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--resolved-cpus", type=int, required=True)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    selected = parser.parse_args(arguments)
    command = selected.command[1:] if selected.command[:1] == ["--"] else selected.command
    requested = Event()
    received: list[int] = []

    def cancel(signum: int, _frame: FrameType | None) -> None:
        received.append(signum)
        requested.set()

    previous = {number: signal.signal(number, cancel) for number in (signal.SIGINT, signal.SIGTERM)}
    try:
        try:
            outcome = run_profiled_command(
                command,
                resolved_cpus=selected.resolved_cpus,
                cancel_requested=requested.is_set,
                cancellation_signal=lambda: received[0] if received else None,
            )
        except ValueError as error:
            print(f"RAM test profile refused: {error}", file=sys.stderr)
            return 3
    finally:
        for number, handler in previous.items():
            signal.signal(number, handler)
    code = -received[0] if received else outcome.returncode
    print(
        "RAM_TEST_PROFILE: "
        + json.dumps(
            {
                "cache": outcome.cache,
                "cleanup": outcome.cleanup,
                "native_process_cleanup": outcome.native_process_cleanup,
                "native_exit_code": code,
            }
        ),
        file=sys.stderr,
    )
    if code < 0:
        signal.signal(-code, signal.SIG_DFL)
        os.kill(os.getpid(), -code)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
