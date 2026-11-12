from __future__ import annotations

import array
import codecs
import errno
import hashlib
import io
import json
import os
import select
import socket
import subprocess
import sys
import time
from collections.abc import Callable, Generator, Mapping, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from threading import Thread
from typing import TYPE_CHECKING, Literal, Protocol, cast

from oh_goon.core.models.execution_authority_economics import (
    ExecutionAuthorityEconomicsReceipt,
)
from oh_goon.core.models.runtime_supervision import WatchdogDecision
from oh_goon.core.models.termination import TerminationSignal
from oh_goon.core.product_identity import new_physical_execution_id
from oh_goon.engine.command_capture import CommandCapture, TextCapture
from oh_goon.engine.command_process_tree import (
    CommandParentGuard,
    _finish_process_output,
    _process_group_exists,
    _terminate_process_group,
    enable_native_child_reaping,
    native_child_reaping_supported,
    owns_native_child_reaping,
    terminate_owned_process_tree,
)
from oh_goon.engine.command_runtime_authority import (
    RuntimeAuthorityBinding as RuntimeAuthorityBinding,
)
from oh_goon.engine.command_runtime_authority import (
    RuntimeAuthorityReceipt as RuntimeAuthorityReceipt,
)
from oh_goon.engine.command_runtime_authority import (
    bind_command_runtime_authority,
    sanitized_child_environment,
)
from oh_goon.engine.command_supervision import CommandRuntimeSupervision, CommandWatchdog
from oh_goon.engine.command_watchdog_execution import evaluate_owned_process_watchdog
from oh_goon.runtime import exec_guard_protocol
from oh_goon.runtime.recovery_evidence import ExecutionRecoveryEvent
from oh_goon.runtime.termination import ProcessTreeTerminationReceipt, TerminationRequest

if TYPE_CHECKING:
    from oh_goon.core.models.fleet_plan_evidence import (
        PlanOutputEvidence,
        PlanStandardIsolationEvidence,
    )

POLL_INTERVAL_SECONDS = 0.1
TERMINATION_GRACE_SECONDS = 1.0
OUTPUT_DRAIN_TIMEOUT_SECONDS = 5.0
MAX_COMMAND_INPUT_BYTES = 1024 * 1024
_TERMINAL_CHUNK_BYTES = 64 * 1024
_EXEC_GUARD_PATH = Path(__file__).resolve().parents[1] / "runtime" / "exec_guard.py"
_child_custody_owner_pid: int | None = None
_command_process_observer: ContextVar[CommandProcessObserver | None] = ContextVar(
    "oh_goon_command_process_observer", default=None
)


@contextmanager
def observe_command_processes(observer: CommandProcessObserver) -> Generator[None, None, None]:
    """Transport one existing effect owner's observer through its target thread."""
    if not callable(observer):
        raise ValueError("Command process observation requires a callable owner")
    token = _command_process_observer.set(observer)
    try:
        yield
    finally:
        _command_process_observer.reset(token)


def native_child_custody_supported() -> bool:
    """Probe the unprivileged native handles required by this command owner."""
    if (
        not sys.platform.startswith("linux")
        or not hasattr(os, "pidfd_open")
        or not native_child_reaping_supported()
    ):
        return False
    descriptor: int | None = None
    parent = child = None
    received: list[int] = []
    try:
        descriptor = os.pidfd_open(os.getpid())
        try:
            os.waitid(os.P_PIDFD, descriptor, os.WEXITED | os.WNOHANG)
        except ChildProcessError:
            pass  # P_PIDFD is supported; this process is intentionally not its own child.
        parent, child = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
        parent.sendmsg(
            [b"P"], [(socket.SOL_SOCKET, socket.SCM_RIGHTS, array.array("i", [descriptor]))]
        )
        packet, ancillary, flags, _ = child.recvmsg(
            1, socket.CMSG_SPACE(array.array("i").itemsize), socket.MSG_CMSG_CLOEXEC
        )
        for level, kind, data in ancillary:
            if level == socket.SOL_SOCKET and kind == socket.SCM_RIGHTS:
                values = array.array("i")
                values.frombytes(data)
                received.extend(values)
        return (
            packet == b"P"
            and not flags & (socket.MSG_TRUNC | socket.MSG_CTRUNC)
            and len(received) == 1
        )
    except (OSError, AttributeError, ValueError):
        return False
    finally:
        for value in received:
            os.close(value)
        if descriptor is not None:
            os.close(descriptor)
        if parent is not None:
            parent.close()
        if child is not None:
            child.close()


def bind_process_child_custody() -> None:
    """Bind native child lifetime to this already claimed, single-Run process.

    This is a process-local command default, not execution or Run authority.
    An inherited marker never silently configures another forked process.
    """
    global _child_custody_owner_pid
    if not native_child_custody_supported():
        raise RuntimeError("Native command child custody is unavailable")
    enable_native_child_reaping()
    _child_custody_owner_pid = os.getpid()


def _sha_identity(payload: object) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )
    return "sha256:" + hashlib.sha256(raw).hexdigest()


@dataclass(frozen=True, slots=True)
class CommandSupervisorReceipt:
    leader_exited_before_group_quiescence: bool = False
    process_group_cleanup_requested: bool = False
    cleanup_reason: Literal[
        "none", "timeout", "cancel", "authority", "output_cleanup", "output_limit"
    ] = "none"
    forced_termination: bool = False
    final_process_group_quiescent: bool = True
    recovery_events: tuple[ExecutionRecoveryEvent, ...] = ()
    termination_receipt: ProcessTreeTerminationReceipt | None = None

    @property
    def receipt_identity(self) -> str:
        from dataclasses import asdict

        return _sha_identity({"schema": "oh-goon.command-supervisor-receipt/v1", **asdict(self)})


@dataclass(frozen=True, slots=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str
    argv: tuple[str, ...] = ()
    duration_ms: int = 0
    supervisor: CommandSupervisorReceipt = CommandSupervisorReceipt()
    runtime_authority: RuntimeAuthorityReceipt | None = None
    execution_authority_economics: tuple[ExecutionAuthorityEconomicsReceipt, ...] = ()
    physical_execution_id: str | None = None
    standard_isolation: PlanStandardIsolationEvidence | None = None
    # Tail capture retains diagnostic text; this evidence covers the whole raw stream.
    stdout_evidence: PlanOutputEvidence | None = None
    stdout_truncated: bool = False


@dataclass(frozen=True, slots=True)
class CommandProcessLifecycleEvent:
    kind: Literal[
        "PROCESS_STARTING",
        "PROCESS_STARTED",
        "TERMINATION_REQUESTED",
        "PROCESS_EXITED",
        "DESCENDANTS_QUIESCENT",
    ]
    physical_execution_id: str
    pid: int | None = None
    process_identity: str | None = None
    returncode: int | None = None
    termination_reason: str | None = None


class CommandTimeoutExpired(subprocess.TimeoutExpired):
    # Native command capture retains decoded text as well as subprocess bytes.
    stdout: str | bytes | None  # type: ignore[assignment]  # ty: ignore[invalid-attribute-override]
    stderr: str | bytes | None  # type: ignore[assignment]  # ty: ignore[invalid-attribute-override]

    def __init__(
        self,
        cmd: str | Sequence[str],
        timeout: float,
        *,
        output: str | bytes | None = None,
        stderr: str | bytes | None = None,
        supervisor: CommandSupervisorReceipt,
        runtime_authority: RuntimeAuthorityReceipt | None = None,
        duration_ms: int = 0,
        physical_execution_id: str | None = None,
        standard_isolation: PlanStandardIsolationEvidence | None = None,
    ) -> None:
        super().__init__(cmd, timeout, output=output, stderr=stderr)
        self.supervisor = supervisor
        self.runtime_authority = runtime_authority
        self.duration_ms = duration_ms
        self.physical_execution_id = physical_execution_id
        self.standard_isolation = standard_isolation
        self.stdout_evidence: PlanOutputEvidence | None = None
        self.stdout_truncated = False


class CommandSupervisionTerminated(RuntimeError):
    """Non-deadline watchdog termination with canonical decision evidence."""

    def __init__(
        self,
        message: str,
        *,
        decision: WatchdogDecision,
        supervisor: CommandSupervisorReceipt,
        runtime_authority: RuntimeAuthorityReceipt | None = None,
    ) -> None:
        super().__init__(message)
        self.decision = decision
        self.supervisor = supervisor
        self.runtime_authority = runtime_authority


class CommandCancelledError(RuntimeError):
    """Cancellation after owned process-tree cleanup, with terminal supervisor evidence."""

    def __init__(
        self,
        message: str,
        *,
        supervisor: CommandSupervisorReceipt,
        runtime_authority: RuntimeAuthorityReceipt | None = None,
        duration_ms: int = 0,
        physical_execution_id: str | None = None,
        stdout: str = "",
        stderr: str = "",
        standard_isolation: PlanStandardIsolationEvidence | None = None,
    ) -> None:
        super().__init__(message)
        self.supervisor = supervisor
        self.runtime_authority = runtime_authority
        self.duration_ms = duration_ms
        self.physical_execution_id = physical_execution_id
        self.stdout = stdout
        self.stderr = stderr
        self.standard_isolation = standard_isolation
        self.stdout_evidence: PlanOutputEvidence | None = None
        self.stdout_truncated = False


class CommandAuthorityTerminatedError(RuntimeError):
    """Exact physical execution terminated because its runtime authority was lost."""

    def __init__(
        self,
        message: str,
        *,
        request: TerminationRequest,
        supervisor: CommandSupervisorReceipt,
        runtime_authority: RuntimeAuthorityReceipt | None = None,
    ) -> None:
        super().__init__(message)
        self.request = request
        self.supervisor = supervisor
        self.runtime_authority = runtime_authority


class CommandOutputLimitExceeded(RuntimeError):
    """A bounded command capture crossed its explicit per-stream byte ceiling."""

    def __init__(
        self,
        *,
        stream: Literal["stdout", "stderr"],
        limit_bytes: int,
        stdout: str,
        stderr: str,
        supervisor: CommandSupervisorReceipt,
        runtime_authority: RuntimeAuthorityReceipt | None = None,
        argv: tuple[str, ...] = (),
    ) -> None:
        super().__init__(f"command {stream} exceeded {limit_bytes} captured bytes")
        self.stream = stream
        self.limit_bytes = limit_bytes
        self.stdout = stdout
        self.stderr = stderr
        self.supervisor = supervisor
        self.runtime_authority = runtime_authority
        self.argv = argv


CommandOutputObserver = Callable[[Literal["stdout", "stderr"], str], None]
CommandProcessObserver = Callable[[CommandProcessLifecycleEvent], None]
CommandTerminationObserver = Callable[[], TerminationSignal | None]


def _validate_terminal_size(size: tuple[int, int]) -> None:
    if (
        type(size) is not tuple
        or len(size) != 2
        or any(type(value) is not int or not 1 <= value <= 65535 for value in size)
    ):
        raise ValueError("terminal size must be (rows, columns) between 1 and 65535")


@dataclass(frozen=True, slots=True)
class CommandTerminal:
    """Nonblocking terminal transport; its caller owns the user-facing terminal.

    Input returns at most the requested bytes, None while idle, or empty bytes
    to disconnect and cancel. Output preserves raw PTY bytes, including ANSI and
    carriage returns. Callbacks must not block the command supervisor/readers.
    The engine owns the private PTY; user terminal descriptors never reach children.
    """

    read_input: Callable[[int], bytes | None]
    write_output: Callable[[bytes], None]
    initial_size: tuple[int, int] = (24, 80)
    current_size: Callable[[], tuple[int, int] | None] | None = None

    def __post_init__(self) -> None:
        _validate_terminal_size(self.initial_size)
        if (
            not callable(self.read_input)
            or not callable(self.write_output)
            or (self.current_size is not None and not callable(self.current_size))
        ):
            raise ValueError("terminal transport requires callable input, output and size hooks")


def _resize_terminal(descriptor: int, size: tuple[int, int]) -> None:
    import fcntl
    import struct
    import termios

    _validate_terminal_size(size)
    fcntl.ioctl(descriptor, termios.TIOCSWINSZ, struct.pack("HHHH", *size, 0, 0))


def _process_identity(pid: int) -> str | None:
    """Best available OS identity for one local process, stronger than PID alone."""
    if os.name == "nt":
        return None
    try:
        stat_text = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
        start_ticks = stat_text.rsplit(")", 1)[1].split()[19]
        boot_id = Path("/proc/sys/kernel/random/boot_id").read_text(encoding="utf-8").strip()
    except (OSError, IndexError):
        return None
    return f"{boot_id}:{start_ticks}"


def _popen_pass_fds(pass_fds: tuple[int, ...]) -> tuple[int, ...]:
    if pass_fds and os.name == "nt":
        raise ValueError("pass_fds is only supported for POSIX command execution")
    return pass_fds


def _cleanup_failed_command_start(
    process: subprocess.Popen[str],
    parent_guard: CommandParentGuard | None,
    failure: BaseException,
) -> None:
    """Retire an already-born command without replacing its original failure."""
    try:
        _terminate_process_group(process, parent_guard=parent_guard)
    except BaseException as cleanup_error:
        failure.add_note(f"Command startup cleanup failed: {type(cleanup_error).__name__}")
    finally:
        if parent_guard is not None:
            try:
                if parent_guard.group is not None:
                    parent_guard.finish_quiescent()
            except BaseException as cleanup_error:
                failure.add_note(f"Command guard cleanup failed: {type(cleanup_error).__name__}")
            finally:
                parent_guard.close()


def _require_guard_exec_start(descriptor: int, executable: str, deadline: float) -> None:
    """Read only the guard's private kernel-exec error, never workload output."""
    remaining = deadline - time.monotonic()
    if remaining <= 0 or not select.select([descriptor], [], [], remaining)[0]:
        raise RuntimeError("Command exec guard startup confirmation is unavailable")
    packet = os.read(descriptor, exec_guard_protocol.EXEC_ERROR_BYTES + 1)
    if not packet:
        return
    if len(packet) != exec_guard_protocol.EXEC_ERROR_BYTES:
        raise RuntimeError("Command exec guard startup error is invalid")
    error_number = int.from_bytes(packet, "big")
    if error_number <= 0:
        raise RuntimeError("Command exec guard startup error is invalid")
    raise OSError(error_number, os.strerror(error_number), executable)


def _spawn_command_process(
    argv: tuple[str, ...],
    *,
    cwd: Path | None,
    child_env: dict[str, str],
    merge_stderr: bool,
    parent_death_signal: bool,
    runtime_authority: RuntimeAuthorityBinding | None,
    pass_fds: tuple[int, ...],
    terminal_slave: int | None = None,
    input_data: bytes | None = None,
    preserve_native_executable_path: bool = False,
) -> tuple[subprocess.Popen[str], RuntimeAuthorityReceipt, CommandParentGuard | None]:
    """Bind exact runtime authority and transfer it into one owned process birth."""

    try:
        lease = (
            bind_command_runtime_authority(
                argv, child_env, cwd, runtime_authority, preserve_native_executable_path=True
            )
            if preserve_native_executable_path
            else bind_command_runtime_authority(argv, child_env, cwd, runtime_authority)
        )
    except OSError as exc:
        raise OSError(exc.errno, exc.strerror, argv[0]) from exc
    popen_argv: Sequence[str] = lease.launch_argv
    popen_executable = (
        lease.receipt.executable_path if preserve_native_executable_path else lease.execution_path
    )
    kill_on_owner_death = parent_death_signal or terminal_slave is not None
    guarded_parent_death = os.name != "nt" and (kill_on_owner_death or owns_native_child_reaping())
    parent_guard: CommandParentGuard | None = None
    exec_error_read: int | None = None
    exec_error_write: int | None = None
    if guarded_parent_death:
        # The guard imports only its trusted script directory and stdlib. Ignore
        # Python startup variables here while preserving them for the workload.
        popen_argv = (sys.executable, "-E", "-S", str(_EXEC_GUARD_PATH), *lease.launch_argv)
        popen_executable = None
        child_env[exec_guard_protocol.PARENT_PID_ENV] = str(os.getpid())
        child_env[exec_guard_protocol.OWNER_DEATH_ENV] = "1" if kill_on_owner_death else "0"
        if lease.execution_descriptor is not None:
            child_env[exec_guard_protocol.EXEC_FD_ENV] = str(lease.execution_descriptor)
        elif lease.receipt.execution_materialization_identity is None:
            # Native env shebangs remain native path execution. Observation
            # cannot become a fabricated sealed execution-descriptor claim.
            child_env[exec_guard_protocol.OBSERVED_EXECUTABLE_ENV] = lease.receipt.executable_path
        else:
            lease.close()
            raise ValueError("atomic command authority lacks its execution descriptor")
        if sys.platform.startswith("linux"):
            try:
                parent_guard = CommandParentGuard.create()
            except BaseException:
                lease.close()
                raise
            child_env[exec_guard_protocol.GUARD_CHANNEL_ENV] = str(parent_guard.child_descriptor)
        if terminal_slave is not None:
            child_env[exec_guard_protocol.CONTROLLING_TERMINAL_ENV] = "1"
        try:
            exec_error_read, exec_error_write = os.pipe()
        except BaseException:
            lease.close()
            if parent_guard is not None:
                parent_guard.close()
            raise
        child_env[exec_guard_protocol.EXEC_ERROR_FD_ENV] = str(exec_error_write)
    inherited_fds = tuple(
        dict.fromkeys(
            (
                *pass_fds,
                *lease.pass_fds,
                *((parent_guard.child_descriptor,) if parent_guard is not None else ()),
                *((exec_error_write,) if exec_error_write is not None else ()),
            )
        )
    )
    process: subprocess.Popen[str] | None = None
    startup_deadline = time.monotonic() + exec_guard_protocol.STARTUP_TIMEOUT_SECONDS
    try:
        try:
            process = subprocess.Popen(
                popen_argv,
                executable=popen_executable,
                stdin=terminal_slave
                if terminal_slave is not None
                else (subprocess.PIPE if input_data is not None else None),
                stdout=terminal_slave if terminal_slave is not None else subprocess.PIPE,
                stderr=terminal_slave
                if terminal_slave is not None
                else (subprocess.STDOUT if merge_stderr else subprocess.PIPE),
                text=True,
                cwd=cwd,
                env=child_env,
                start_new_session=os.name != "nt",
                creationflags=(
                    subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
                    if os.name == "nt"
                    else 0
                ),
                pass_fds=_popen_pass_fds(inherited_fds),
            )
            try:
                if exec_error_write is not None:
                    os.close(exec_error_write)
                    exec_error_write = None
                if parent_guard is not None:
                    parent_guard.accept(process)
                if exec_error_read is not None:
                    _require_guard_exec_start(exec_error_read, argv[0], startup_deadline)
            except BaseException as failure:
                _cleanup_failed_command_start(process, parent_guard, failure)
                raise
        except OSError as exc:
            if parent_guard is not None:
                parent_guard.close()
            raise OSError(exc.errno, exc.strerror, argv[0]) from exc
    finally:
        lease.close()
        if exec_error_read is not None:
            os.close(exec_error_read)
        if exec_error_write is not None:
            os.close(exec_error_write)
        if process is None and parent_guard is not None:
            parent_guard.close()
    assert process is not None
    return process, lease.receipt, parent_guard


@dataclass(frozen=True, slots=True)
class _StartedCommand:
    process: subprocess.Popen[str]
    authority_receipt: RuntimeAuthorityReceipt | None
    physical_execution_id: str
    process_identity: str | None
    capture: CommandCapture
    readers: tuple[Thread, Thread]
    terminal_master: int | None
    output_errors: list[BaseException]
    parent_guard: CommandParentGuard | None


def _start_supervised_command(
    argv: tuple[str, ...],
    *,
    cwd: Path | None,
    env: Mapping[str, str] | None,
    stream_output: CommandOutputObserver | None,
    process_observer: CommandProcessObserver | None,
    merge_stderr: bool,
    parent_death_signal: bool,
    runtime_authority: RuntimeAuthorityBinding | None,
    pass_fds: tuple[int, ...],
    max_capture_bytes_per_stream: int | None,
    terminal: CommandTerminal | None,
    input_data: bytes | None,
    preserve_native_executable_path: bool,
    retain_output_tail: bool,
) -> _StartedCommand:
    child_env = sanitized_child_environment(argv, env)
    physical_execution_id = new_physical_execution_id()
    if process_observer is not None:
        process_observer(
            CommandProcessLifecycleEvent(
                kind="PROCESS_STARTING", physical_execution_id=physical_execution_id
            )
        )
    terminal_master = terminal_slave = None
    try:
        if terminal is not None:
            terminal_master, terminal_slave = os.openpty()
            _resize_terminal(terminal_slave, terminal.initial_size)
            os.set_blocking(terminal_master, False)
        process, authority_receipt, parent_guard = _spawn_command_process(
            argv,
            cwd=cwd,
            child_env=child_env,
            merge_stderr=merge_stderr,
            parent_death_signal=parent_death_signal,
            runtime_authority=runtime_authority,
            pass_fds=pass_fds,
            terminal_slave=terminal_slave,
            input_data=input_data,
            preserve_native_executable_path=preserve_native_executable_path,
        )
    except BaseException:
        if terminal_master is not None:
            os.close(terminal_master)
        raise
    finally:
        if terminal_slave is not None:
            os.close(terminal_slave)
    try:
        if terminal_master is not None:
            process.stdout = os.fdopen(terminal_master, "r", encoding="utf-8", errors="replace")
        if process.stdin is not None:
            os.set_blocking(process.stdin.fileno(), False)
    except BaseException as failure:
        _cleanup_failed_command_start(process, parent_guard, failure)
        if terminal_master is not None and process.stdout is None:
            os.close(terminal_master)
        raise
    process_identity = None
    if process_observer is not None:
        try:
            process_identity = _process_identity(process.pid)
            process_observer(
                CommandProcessLifecycleEvent(
                    kind="PROCESS_STARTED",
                    physical_execution_id=physical_execution_id,
                    pid=process.pid,
                    process_identity=process_identity,
                )
            )
        except BaseException as failure:
            _cleanup_failed_command_start(process, parent_guard, failure)
            raise
    capture = CommandCapture(
        max_capture_bytes_per_stream,
        terminal=terminal is not None,
        retain_output_tail=retain_output_tail,
    )
    output_errors: list[BaseException] = []
    stdout_reader = (
        _terminal_reader(
            cast(io.TextIOWrapper, process.stdout),
            terminal,
            capture.stdout,
            stream_output,
            output_errors,
        )
        if terminal is not None
        else _reader(
            cast(_CommandOutputPipe | None, process.stdout),
            "stdout",
            capture.stdout,
            stream_output,
            output_errors,
        )
    )
    stderr_reader = (
        _reader(
            cast(_CommandOutputPipe | None, process.stderr),
            "stderr",
            capture.stderr,
            stream_output,
            output_errors,
        )
        if not merge_stderr and terminal is None
        else Thread(target=lambda: None, name="oh-goon-stderr-merged", daemon=True)
    )
    readers = (stdout_reader, stderr_reader)
    for reader in readers:
        reader.start()
    return _StartedCommand(
        process,
        authority_receipt,
        physical_execution_id,
        process_identity,
        capture,
        readers,
        terminal_master,
        output_errors,
        parent_guard,
    )


def run_command(
    *args: str,
    timeout: float | None = 10.0,
    cwd: Path | None = None,
    env: Mapping[str, str] | None = None,
    cancel_requested: Callable[[], bool] | None = None,
    termination_requested: CommandTerminationObserver | None = None,
    stream_output: CommandOutputObserver | None = None,
    process_observer: CommandProcessObserver | None = None,
    merge_stderr: bool = False,
    require_process_group_quiescence: bool = False,
    parent_death_signal: bool | None = None,
    runtime_authority: RuntimeAuthorityBinding | None = None,
    pass_fds: tuple[int, ...] = (),
    max_capture_bytes_per_stream: int | None = None,
    runtime_supervision: CommandRuntimeSupervision | None = None,
    terminal: CommandTerminal | None = None,
    input_data: bytes | None = None,
    preserve_native_executable_path: bool = True,
    retain_output_tail: bool = False,
) -> CommandResult:
    """Run one owned command with optional live output and terminal transport.

    Ordinary commands retain their native executable path, including native
    invocation-name dispatch and self-respawn. This reports an observation,
    never atomic materialization. Explicit exact-byte execution selects
    preserve_native_executable_path=False and retains the sealed launch owner.
    Terminal output and explicit tail capture retain a bounded diagnostic tail;
    stdout_evidence hashes and counts the original complete raw stream.
    """

    argv = tuple(args)
    if process_observer is None:
        process_observer = _command_process_observer.get()
    if parent_death_signal is None:
        parent_death_signal = _child_custody_owner_pid == os.getpid()
    elif type(parent_death_signal) is not bool:
        raise ValueError("command parent-death selection must be a boolean")
    if type(preserve_native_executable_path) is not bool:
        raise ValueError("native executable path preservation must be a boolean")
    if type(retain_output_tail) is not bool:
        raise ValueError("command output tail retention must be a boolean")
    if terminal is not None and (type(terminal) is not CommandTerminal or os.name == "nt"):
        raise ValueError("command PTY transport requires POSIX and CommandTerminal")
    if input_data is not None and (
        type(input_data) is not bytes or len(input_data) > MAX_COMMAND_INPUT_BYTES
    ):
        raise ValueError("command input_data must be immutable bytes within 1 MiB")
    if terminal is not None and input_data is not None:
        raise ValueError("terminal and input_data are mutually exclusive")
    if max_capture_bytes_per_stream is not None and max_capture_bytes_per_stream < 1:
        raise ValueError("max_capture_bytes_per_stream must be positive when provided")
    if runtime_supervision is not None and timeout is not None:
        raise ValueError(
            "runtime_supervision and fixed timeout cannot both own the runtime deadline"
        )
    started_command = _start_supervised_command(
        argv,
        cwd=cwd,
        env=env,
        stream_output=stream_output,
        process_observer=process_observer,
        merge_stderr=merge_stderr,
        parent_death_signal=parent_death_signal,
        runtime_authority=runtime_authority,
        pass_fds=pass_fds,
        max_capture_bytes_per_stream=max_capture_bytes_per_stream,
        terminal=terminal,
        input_data=input_data,
        preserve_native_executable_path=preserve_native_executable_path,
        retain_output_tail=retain_output_tail,
    )
    process = started_command.process
    parent_guard = started_command.parent_guard
    authority_receipt = started_command.authority_receipt
    physical_execution_id = started_command.physical_execution_id
    process_identity = started_command.process_identity
    capture = started_command.capture
    stdout_capture, stderr_capture = capture.stdout, capture.stderr
    readers = started_command.readers
    started = time.monotonic()
    watchdog_started_at = datetime.now(timezone.utc)
    watchdog = (
        CommandWatchdog(runtime_supervision, physical_execution_id, watchdog_started_at)
        if runtime_supervision is not None
        else None
    )
    input_offset = 0
    pending_terminal_input = b""
    terminal_size = terminal.initial_size if terminal is not None else None
    leader_exited_before_group_quiescence = False
    completion_cleanup_requested = False
    failure: BaseException | None = None
    try:
        while True:
            if parent_guard is not None:
                parent_guard.require_live()
            if started_command.output_errors:
                raise started_command.output_errors[0]
            if termination_requested is not None:
                signal = termination_requested()
                if signal is not None:
                    request = TerminationRequest(
                        execution_id=physical_execution_id,
                        generation=1,
                        cause=signal.cause,
                        reason=signal.reason,
                        grace_seconds=signal.grace_seconds,
                    )
                    raise _terminate_for_authority_loss(
                        process,
                        readers,
                        args,
                        authority_receipt,
                        request=request,
                        process_identity=process_identity,
                        process_observer=process_observer,
                        parent_guard=parent_guard,
                    )
            if cancel_requested is not None and cancel_requested():
                raise _cancel_command(
                    process,
                    readers,
                    args,
                    authority_receipt,
                    physical_execution_id=physical_execution_id,
                    process_identity=process_identity,
                    process_observer=process_observer,
                    parent_guard=parent_guard,
                )
            _raise_if_output_limited(
                process, readers, capture, authority_receipt, argv=argv, parent_guard=parent_guard
            )
            leader_running = process.poll() is None
            if leader_running and input_data is not None and process.stdin is not None:
                if not process.stdin.closed:
                    try:
                        input_offset += os.write(
                            process.stdin.fileno(),
                            memoryview(input_data)[
                                input_offset : input_offset + _TERMINAL_CHUNK_BYTES
                            ],
                        )
                    except BlockingIOError:
                        pass
                    except BrokenPipeError:
                        process.stdin.close()
                    if input_offset == len(input_data) and not process.stdin.closed:
                        process.stdin.close()
            if leader_running and terminal is not None:
                master = started_command.terminal_master
                assert master is not None
                if terminal.current_size is not None:
                    size = terminal.current_size()
                    if size is not None and size != terminal_size:
                        _resize_terminal(master, size)
                        terminal_size = size
                if not pending_terminal_input:
                    available = terminal.read_input(_TERMINAL_CHUNK_BYTES)
                    if available is not None and (
                        type(available) is not bytes or len(available) > _TERMINAL_CHUNK_BYTES
                    ):
                        raise ValueError("terminal input must return bounded bytes or None")
                    if available == b"":
                        raise _cancel_command(
                            process,
                            readers,
                            args,
                            authority_receipt,
                            physical_execution_id=physical_execution_id,
                            process_identity=process_identity,
                            process_observer=process_observer,
                            parent_guard=parent_guard,
                        )
                    pending_terminal_input = available or b""
                if pending_terminal_input:
                    try:
                        count = os.write(master, pending_terminal_input)
                        pending_terminal_input = pending_terminal_input[count:]
                    except BlockingIOError:
                        pass
                    except OSError as exc:
                        if exc.errno not in (errno.EIO, errno.EBADF):
                            raise
                        raise _cancel_command(
                            process,
                            readers,
                            args,
                            authority_receipt,
                            physical_execution_id=physical_execution_id,
                            process_identity=process_identity,
                            process_observer=process_observer,
                            parent_guard=parent_guard,
                        ) from exc
            descendants_running = (
                not leader_running
                and require_process_group_quiescence
                and os.name != "nt"
                and _process_group_exists(process.pid, parent_guard=parent_guard)
            )
            if not leader_running:
                # The admitted command has finished. Any process still in its owned
                # process group is background work and must not extend the command's
                # lifetime until the outer timeout. Earlier versions waited here, so a
                # fast pytest leader that left one helper/descendant behind was reported
                # as a 90/120s timeout even though all test calls had already completed.
                # Transition immediately into owned cleanup; _finish_process_output()
                # terminates the remaining group and drains/closes its pipes.
                if descendants_running:
                    leader_exited_before_group_quiescence = True
                    completion_cleanup_requested = True
                break
            elapsed = time.monotonic() - started
            supervision_error = _runtime_supervision_error(
                watchdog,
                runtime_supervision,
                process,
                readers,
                args,
                authority_receipt,
                physical_execution_id,
                process_identity,
                process_observer,
                leader_exited_before_group_quiescence,
                parent_guard=parent_guard,
            )
            if supervision_error is not None:
                raise supervision_error
            if timeout is not None and elapsed >= timeout:
                request = TerminationRequest(
                    execution_id=physical_execution_id,
                    generation=1,
                    cause="watchdog",
                    reason="max_runtime_exceeded",
                    grace_seconds=TERMINATION_GRACE_SECONDS,
                )
                if process_observer is not None:
                    process_observer(
                        CommandProcessLifecycleEvent(
                            kind="TERMINATION_REQUESTED",
                            physical_execution_id=physical_execution_id,
                            pid=process.pid,
                            process_identity=process_identity,
                            termination_reason=request.reason,
                        )
                    )
                termination_receipt = terminate_owned_process_tree(
                    process, request, parent_guard=parent_guard
                )
                forced_termination = termination_receipt.escalated_to_kill
                forced_termination = (
                    _finish_process_output(process, readers, parent_guard=parent_guard)
                    or forced_termination
                )
                final_quiescent = termination_receipt.quiescent and (
                    os.name == "nt"
                    or not _process_group_exists(process.pid, parent_guard=parent_guard)
                )
                raise CommandTimeoutExpired(
                    args,
                    timeout,
                    output=stdout_capture.text,
                    stderr=stderr_capture.text,
                    runtime_authority=authority_receipt,
                    supervisor=CommandSupervisorReceipt(
                        leader_exited_before_group_quiescence=leader_exited_before_group_quiescence,
                        process_group_cleanup_requested=True,
                        cleanup_reason="timeout",
                        forced_termination=forced_termination,
                        final_process_group_quiescent=final_quiescent,
                        recovery_events=(
                            ExecutionRecoveryEvent(
                                kind="process_group_cleanup",
                                action="terminate_process_group",
                                reason_code="deadline_expired",
                                outcome="requested",
                            ),
                        ),
                        termination_receipt=termination_receipt,
                    ),
                )
            remaining = None if timeout is None else timeout - elapsed
            wait = (
                POLL_INTERVAL_SECONDS
                if remaining is None
                else min(POLL_INTERVAL_SECONDS, remaining)
            )
            if leader_running:
                try:
                    process.wait(timeout=wait)
                except subprocess.TimeoutExpired:
                    continue
            else:
                time.sleep(wait)
        forced_termination = _finish_process_output(process, readers, parent_guard=parent_guard)
        guard_cleanup = parent_guard is not None and parent_guard.cleanup_forced
        completion_cleanup_requested = completion_cleanup_requested or guard_cleanup
        if started_command.output_errors:
            raise started_command.output_errors[0]
        _raise_if_output_limited(
            process,
            readers,
            capture,
            authority_receipt,
            argv=argv,
            already_finished=True,
            parent_guard=parent_guard,
        )
        final_quiescent = os.name == "nt" or not _process_group_exists(
            process.pid, parent_guard=parent_guard
        )
        return CommandResult(
            argv=tuple(args),
            returncode=process.returncode,
            runtime_authority=authority_receipt,
            physical_execution_id=physical_execution_id,
            stdout=stdout_capture.text
            if terminal is not None or retain_output_tail
            else stdout_capture.text.rstrip(),
            stderr=stderr_capture.text.rstrip(),
            stdout_evidence=stdout_capture.evidence,
            stdout_truncated=stdout_capture.truncated,
            duration_ms=int((time.monotonic() - started) * 1000),
            supervisor=CommandSupervisorReceipt(
                leader_exited_before_group_quiescence=leader_exited_before_group_quiescence,
                process_group_cleanup_requested=completion_cleanup_requested,
                cleanup_reason=("output_cleanup" if completion_cleanup_requested else "none"),
                forced_termination=forced_termination,
                final_process_group_quiescent=final_quiescent,
                recovery_events=(
                    (
                        ExecutionRecoveryEvent(
                            kind="process_group_cleanup",
                            action="retire_parent_guard",
                            reason_code="guard_ack_unavailable",
                            outcome="succeeded",
                        ),
                    )
                    if guard_cleanup
                    else (
                        ExecutionRecoveryEvent(
                            kind="process_group_cleanup",
                            action="terminate_process_group",
                            reason_code="leader_exited_with_descendants",
                            outcome="requested",
                        ),
                    )
                    if completion_cleanup_requested
                    else ()
                ),
            ),
        )
    except BaseException as exc:
        failure = exc
        if isinstance(exc, (CommandTimeoutExpired, CommandCancelledError)):
            exc.duration_ms = int((time.monotonic() - started) * 1000)
            exc.physical_execution_id = physical_execution_id
            if isinstance(exc, CommandTimeoutExpired):
                exc.output = stdout_capture.text
            else:
                exc.stdout = stdout_capture.text
            exc.stderr = stderr_capture.text
        if process.poll() is None:
            try:
                _terminate_process_group(process, parent_guard=parent_guard)
            except BaseException:
                exc.add_note("Native process termination cleanup remains unverified")
        raise
    finally:
        try:
            if any(reader.is_alive() for reader in readers) or (
                parent_guard is not None and not parent_guard.settled
            ):
                try:
                    _terminate_process_group(process, parent_guard=parent_guard)
                finally:
                    # Even a failed termination observation must retire native
                    # output/control handles when actual work is now absent.
                    _finish_process_output(process, readers, parent_guard=parent_guard)
        except BaseException:
            if failure is None:
                raise
            failure.add_note("Native command cleanup remains unverified")
            if isinstance(failure, (CommandTimeoutExpired, CommandCancelledError)):
                from dataclasses import replace

                failure.supervisor = replace(
                    failure.supervisor, final_process_group_quiescent=False
                )
        finally:
            if parent_guard is not None:
                parent_guard.close()
        if (terminal is not None or retain_output_tail) and isinstance(
            failure, (CommandTimeoutExpired, CommandCancelledError)
        ):
            failure.stdout_evidence = stdout_capture.evidence
            failure.stdout_truncated = stdout_capture.truncated
            if isinstance(failure, CommandTimeoutExpired):
                failure.output = stdout_capture.text
            else:
                failure.stdout = stdout_capture.text
        if terminal is not None and process.stdout is not None and not process.stdout.closed:
            process.stdout.close()
        if process.stdin is not None and not process.stdin.closed:
            process.stdin.close()
        _report_terminal_process_state(
            process,
            process_observer,
            physical_execution_id,
            process_identity,
            parent_guard=parent_guard,
        )


def _runtime_supervision_error(
    watchdog: CommandWatchdog | None,
    supervision: CommandRuntimeSupervision | None,
    process: subprocess.Popen[str],
    readers: tuple[Thread, Thread],
    args: Sequence[str],
    authority_receipt: RuntimeAuthorityReceipt | None,
    physical_execution_id: str,
    process_identity: str | None,
    process_observer: CommandProcessObserver | None,
    leader_exited_before_group_quiescence: bool,
    parent_guard: CommandParentGuard | None = None,
) -> BaseException | None:
    if watchdog is None or supervision is None:
        return None

    def record_termination(reason: str) -> None:
        if process_observer is not None:
            process_observer(
                CommandProcessLifecycleEvent(
                    kind="TERMINATION_REQUESTED",
                    physical_execution_id=physical_execution_id,
                    pid=process.pid,
                    process_identity=process_identity,
                    termination_reason=reason,
                )
            )

    termination = evaluate_owned_process_watchdog(
        watchdog,
        supervision,
        process,
        readers,
        termination_requested=record_termination,
        parent_guard=parent_guard,
    )
    if termination is None:
        return None
    supervisor = CommandSupervisorReceipt(
        leader_exited_before_group_quiescence=leader_exited_before_group_quiescence,
        process_group_cleanup_requested=True,
        cleanup_reason="timeout",
        forced_termination=termination.forced_termination,
        final_process_group_quiescent=termination.final_quiescent,
        recovery_events=(
            ExecutionRecoveryEvent(
                kind="process_group_cleanup",
                action="terminate_process_group",
                reason_code=termination.decision.reason,
                outcome="requested",
            ),
        ),
        termination_receipt=termination.receipt,
    )
    if termination.decision.reason == "max_runtime_exceeded":
        return CommandTimeoutExpired(
            args,
            supervision.policy.max_runtime_seconds,
            runtime_authority=authority_receipt,
            supervisor=supervisor,
        )
    return CommandSupervisionTerminated(
        f"Command terminated by runtime supervision: {termination.decision.reason}",
        decision=termination.decision,
        supervisor=supervisor,
        runtime_authority=authority_receipt,
    )


def _report_terminal_process_state(
    process: subprocess.Popen[str],
    observer: CommandProcessObserver | None,
    physical_execution_id: str,
    process_identity: str | None,
    parent_guard: CommandParentGuard | None = None,
) -> None:
    if observer is None or process.poll() is None:
        return
    observer(
        CommandProcessLifecycleEvent(
            kind="PROCESS_EXITED",
            physical_execution_id=physical_execution_id,
            pid=process.pid,
            process_identity=process_identity,
            returncode=process.returncode,
        )
    )
    if os.name == "nt" or not _process_group_exists(process.pid, parent_guard=parent_guard):
        observer(
            CommandProcessLifecycleEvent(
                kind="DESCENDANTS_QUIESCENT",
                physical_execution_id=physical_execution_id,
                pid=process.pid,
                process_identity=process_identity,
            )
        )


def _cancel_command(
    process: subprocess.Popen[str],
    readers: tuple[Thread, Thread],
    args: Sequence[str],
    runtime_authority: RuntimeAuthorityReceipt | None,
    *,
    physical_execution_id: str,
    process_identity: str | None,
    process_observer: CommandProcessObserver | None,
    parent_guard: CommandParentGuard | None = None,
) -> CommandCancelledError:
    request = TerminationRequest(
        execution_id=physical_execution_id,
        generation=1,
        cause="cancel",
        reason="cancellation_requested",
        grace_seconds=TERMINATION_GRACE_SECONDS,
    )
    if process_observer is not None:
        process_observer(
            CommandProcessLifecycleEvent(
                kind="TERMINATION_REQUESTED",
                physical_execution_id=physical_execution_id,
                pid=process.pid,
                process_identity=process_identity,
                termination_reason=request.reason,
            )
        )
    termination_receipt = terminate_owned_process_tree(process, request, parent_guard=parent_guard)
    forced_termination = (
        _finish_process_output(process, readers, parent_guard=parent_guard)
        or termination_receipt.escalated_to_kill
    )
    final_quiescent = termination_receipt.quiescent and (
        os.name == "nt" or not _process_group_exists(process.pid, parent_guard=parent_guard)
    )
    return CommandCancelledError(
        f"Command cancelled: {' '.join(args)}",
        runtime_authority=runtime_authority,
        supervisor=CommandSupervisorReceipt(
            process_group_cleanup_requested=True,
            cleanup_reason="cancel",
            forced_termination=forced_termination,
            final_process_group_quiescent=final_quiescent,
            recovery_events=(
                ExecutionRecoveryEvent(
                    kind="process_group_cleanup",
                    action="terminate_process_group",
                    reason_code="cancellation_requested",
                    outcome="requested",
                ),
            ),
            termination_receipt=termination_receipt,
        ),
    )


def _terminate_for_authority_loss(
    process: subprocess.Popen[str],
    readers: tuple[Thread, Thread],
    args: Sequence[str],
    runtime_authority: RuntimeAuthorityReceipt | None,
    *,
    request: TerminationRequest,
    process_identity: str | None,
    process_observer: CommandProcessObserver | None,
    parent_guard: CommandParentGuard | None = None,
) -> CommandAuthorityTerminatedError:
    if process_observer is not None:
        process_observer(
            CommandProcessLifecycleEvent(
                kind="TERMINATION_REQUESTED",
                physical_execution_id=request.execution_id,
                pid=process.pid,
                process_identity=process_identity,
                termination_reason=request.reason,
            )
        )
    termination_receipt = terminate_owned_process_tree(process, request, parent_guard=parent_guard)
    forced_termination = (
        _finish_process_output(process, readers, parent_guard=parent_guard)
        or termination_receipt.escalated_to_kill
    )
    final_quiescent = termination_receipt.quiescent and (
        os.name == "nt" or not _process_group_exists(process.pid, parent_guard=parent_guard)
    )
    return CommandAuthorityTerminatedError(
        f"Command authority terminated: {' '.join(args)}",
        request=request,
        runtime_authority=runtime_authority,
        supervisor=CommandSupervisorReceipt(
            process_group_cleanup_requested=True,
            cleanup_reason="authority",
            forced_termination=forced_termination,
            final_process_group_quiescent=final_quiescent,
            recovery_events=(
                ExecutionRecoveryEvent(
                    kind="process_group_cleanup",
                    action="terminate_process_group",
                    reason_code=request.reason,
                    outcome="requested",
                ),
            ),
            termination_receipt=termination_receipt,
        ),
    )


def _raise_if_output_limited(
    process: subprocess.Popen[str],
    readers: tuple[Thread, Thread],
    capture: CommandCapture,
    runtime_authority: RuntimeAuthorityReceipt | None,
    *,
    argv: tuple[str, ...],
    already_finished: bool = False,
    parent_guard: CommandParentGuard | None = None,
) -> None:
    if not capture.overflow.is_set():
        return
    raise _output_limit_error(
        process,
        readers,
        capture.stdout,
        capture.stderr,
        runtime_authority,
        argv=argv,
        already_finished=already_finished,
        parent_guard=parent_guard,
    )


def _output_limit_error(
    process: subprocess.Popen[str],
    readers: tuple[Thread, Thread],
    stdout_capture: TextCapture,
    stderr_capture: TextCapture,
    runtime_authority: RuntimeAuthorityReceipt | None,
    *,
    argv: tuple[str, ...],
    already_finished: bool = False,
    parent_guard: CommandParentGuard | None = None,
) -> CommandOutputLimitExceeded:
    forced_termination = False
    if not already_finished:
        forced_termination = _terminate_process_group(
            process, close_pipes=False, parent_guard=parent_guard
        )
        forced_termination = (
            _finish_process_output(process, readers, parent_guard=parent_guard)
            or forced_termination
        )
    stream = stdout_capture.overflow_stream or stderr_capture.overflow_stream or "stdout"
    capture = stdout_capture if stream == "stdout" else stderr_capture
    assert capture.limit_bytes is not None
    return CommandOutputLimitExceeded(
        argv=argv,
        stream=stream,
        limit_bytes=capture.limit_bytes,
        stdout=stdout_capture.text,
        stderr=stderr_capture.text,
        runtime_authority=runtime_authority,
        supervisor=CommandSupervisorReceipt(
            process_group_cleanup_requested=True,
            cleanup_reason="output_limit",
            forced_termination=forced_termination,
            final_process_group_quiescent=(
                os.name == "nt" or not _process_group_exists(process.pid, parent_guard=parent_guard)
            ),
            recovery_events=(
                ExecutionRecoveryEvent(
                    kind="process_group_cleanup",
                    action="terminate_process_group",
                    reason_code="output_limit_exceeded",
                    outcome="requested",
                ),
            ),
        ),
    )


def _terminal_reader(
    pipe: io.TextIOWrapper,
    terminal: CommandTerminal,
    capture: TextCapture,
    observer: CommandOutputObserver | None,
    errors: list[BaseException],
) -> Thread:
    def read() -> None:
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        try:
            while not pipe.closed:
                try:
                    ready, _, _ = select.select([pipe], [], [], POLL_INTERVAL_SECONDS)
                    if not ready:
                        continue
                    raw = os.read(pipe.fileno(), _TERMINAL_CHUNK_BYTES)
                except (OSError, ValueError) as exc:
                    if pipe.closed or (isinstance(exc, OSError) and exc.errno == errno.EIO):
                        break
                    raise
                if not raw:
                    break
                text = decoder.decode(raw)
                capture.append(text, stream="stdout", raw=raw)
                terminal.write_output(raw)
                if text:
                    if observer is not None:
                        try:
                            observer("stdout", text)
                        except Exception:
                            pass
            tail = decoder.decode(b"", final=True)
            if tail:
                capture.append(tail, stream="stdout", raw=b"")
        except BaseException as exc:
            errors.append(exc)

    return Thread(target=read, name="oh-goon-terminal-output", daemon=True)


class _CommandOutputBuffer(Protocol):
    def read1(self, size: int) -> bytes: ...


class _CommandOutputPipe(Protocol):
    """Text wrapper guaranteed by the native Popen(text=True) output owner."""

    @property
    def encoding(self) -> str | None: ...
    @property
    def errors(self) -> str | None: ...
    @property
    def buffer(self) -> _CommandOutputBuffer: ...
    @property
    def closed(self) -> bool: ...

    def close(self) -> None: ...


def _reader(
    pipe: _CommandOutputPipe | None,
    stream: Literal["stdout", "stderr"],
    capture: TextCapture,
    observer: CommandOutputObserver | None,
    errors: list[BaseException],
) -> Thread:
    def read() -> None:
        if pipe is None:
            return
        try:
            # TextIOWrapper.read(n) waits for n characters (or EOF), so a
            # live child emitting READY then sleeping can never be cancelled
            # from its output. read1() returns available bytes without waiting
            # for a 64-KiB fill; incremental decoding preserves split UTF-8 and
            # universal-newline semantics across arbitrary kernel chunks.
            decoder = io.IncrementalNewlineDecoder(
                codecs.getincrementaldecoder(pipe.encoding or "utf-8")(
                    errors=pipe.errors or "strict"
                ),
                translate=True,
            )
            while True:
                try:
                    raw = pipe.buffer.read1(64 * 1024)
                except ValueError:
                    if pipe.closed:
                        break
                    raise
                chunk = decoder.decode(raw, final=not raw)
                capture.append(chunk, stream=stream, raw=raw)
                if chunk:
                    if observer is not None:
                        try:
                            observer(stream, chunk)
                        except Exception:
                            # Observability must never stop process output draining.
                            pass
                if not raw:
                    break
        except BaseException as exc:
            # A partial transcript is not a successfully transferred command
            # result. The original supervisor owns cleanup and propagation.
            errors.append(exc)
        finally:
            try:
                if not pipe.closed:
                    pipe.close()
            except BaseException as exc:
                errors.append(exc)

    return Thread(target=read, name=f"oh-goon-{stream}", daemon=True)
