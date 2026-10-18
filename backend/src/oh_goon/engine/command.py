from __future__ import annotations

import codecs
import hashlib
import io
import json
import os
import signal
import subprocess
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from threading import Thread
from typing import Literal

from oh_goon.engine.command_capture import CommandCapture, TextCapture
from oh_goon.engine.command_process_tree import (
    _finish_process_output, _process_group_exists, _terminate_process_group, terminate_owned_process_tree,
)

from oh_goon.core.models.execution_authority_economics import (
    ExecutionAuthorityEconomicsReceipt,
)
from oh_goon.core.models.termination import TerminationSignal
from oh_goon.core.product_identity import new_physical_execution_id
from oh_goon.engine.command_runtime_authority import (
    RuntimeAuthorityBinding,
    RuntimeAuthorityReceipt,
    bind_command_runtime_authority,
    sanitized_child_environment,
)
from oh_goon.engine.command_supervision import CommandRuntimeSupervision, CommandWatchdog
from oh_goon.engine.command_watchdog_execution import evaluate_owned_process_watchdog
from oh_goon.runtime import exec_guard_protocol
from oh_goon.runtime.recovery_evidence import ExecutionRecoveryEvent
from oh_goon.runtime.termination import ProcessTreeTerminationReceipt, TerminationRequest

POLL_INTERVAL_SECONDS = 0.1
TERMINATION_GRACE_SECONDS = 1.0
OUTPUT_DRAIN_TIMEOUT_SECONDS = 5.0
_EXEC_GUARD_PATH = Path(__file__).resolve().parents[1] / "runtime" / "exec_guard.py"




def _sha_identity(payload: object) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
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
    def __init__(self, cmd, timeout, *, output=None, stderr=None, supervisor: CommandSupervisorReceipt, runtime_authority: RuntimeAuthorityReceipt | None = None):
        super().__init__(cmd, timeout, output=output, stderr=stderr)
        self.supervisor = supervisor
        self.runtime_authority = runtime_authority


class CommandSupervisionTerminated(RuntimeError):
    """Non-deadline watchdog termination with canonical decision evidence."""

    def __init__(self, message: str, *, decision, supervisor: CommandSupervisorReceipt, runtime_authority: RuntimeAuthorityReceipt | None = None) -> None:
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
    ) -> None:
        super().__init__(message)
        self.supervisor = supervisor
        self.runtime_authority = runtime_authority


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
    ) -> None:
        super().__init__(f"command {stream} exceeded {limit_bytes} captured bytes")
        self.stream = stream
        self.limit_bytes = limit_bytes
        self.stdout = stdout
        self.stderr = stderr
        self.supervisor = supervisor
        self.runtime_authority = runtime_authority


CommandOutputObserver = Callable[[Literal["stdout", "stderr"], str], None]
CommandProcessObserver = Callable[[CommandProcessLifecycleEvent], None]
CommandTerminationObserver = Callable[[], TerminationSignal | None]


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


def _spawn_command_process(
    argv: tuple[str, ...],
    *,
    cwd: Path | None,
    child_env: dict[str, str],
    merge_stderr: bool,
    parent_death_signal: bool,
    runtime_authority: RuntimeAuthorityBinding | None,
    pass_fds: tuple[int, ...],
) -> tuple[subprocess.Popen[str], RuntimeAuthorityReceipt]:
    """Bind exact runtime authority and transfer it into one owned process birth."""

    try:
        lease = bind_command_runtime_authority(argv, child_env, cwd, runtime_authority)
    except OSError as exc:
        raise OSError(exc.errno, exc.strerror, argv[0]) from exc
    popen_argv: Sequence[str] = lease.launch_argv
    popen_executable = lease.execution_path
    guarded_parent_death = os.name != "nt" and parent_death_signal
    if guarded_parent_death:
        popen_argv = (sys.executable, "-S", str(_EXEC_GUARD_PATH), *lease.launch_argv)
        popen_executable = None
        child_env[exec_guard_protocol.PARENT_PID_ENV] = str(os.getpid())
        if lease.execution_descriptor is not None:
            child_env[exec_guard_protocol.EXEC_FD_ENV] = str(lease.execution_descriptor)
    inherited_fds = tuple(dict.fromkeys((*pass_fds, *lease.pass_fds)))
    try:
        try:
            process = subprocess.Popen(
                popen_argv,
                executable=popen_executable,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT if merge_stderr else subprocess.PIPE,
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
        except OSError as exc:
            raise OSError(exc.errno, exc.strerror, argv[0]) from exc
    finally:
        lease.close()
    return process, lease.receipt


@dataclass(frozen=True, slots=True)
class _StartedCommand:
    process: subprocess.Popen[str]
    authority_receipt: RuntimeAuthorityReceipt | None
    physical_execution_id: str
    process_identity: str | None
    capture: CommandCapture
    readers: tuple[Thread, Thread]


def _start_supervised_command(argv: tuple[str, ...], *, cwd: Path | None, env: Mapping[str, str] | None, stream_output: CommandOutputObserver | None, process_observer: CommandProcessObserver | None, merge_stderr: bool, parent_death_signal: bool, runtime_authority: RuntimeAuthorityBinding | None, pass_fds: tuple[int, ...], max_capture_bytes_per_stream: int | None) -> _StartedCommand:
    child_env = sanitized_child_environment(argv, env)
    physical_execution_id = new_physical_execution_id()
    if process_observer is not None:
        process_observer(CommandProcessLifecycleEvent(kind="PROCESS_STARTING", physical_execution_id=physical_execution_id))
    process, authority_receipt = _spawn_command_process(argv, cwd=cwd, child_env=child_env, merge_stderr=merge_stderr, parent_death_signal=parent_death_signal, runtime_authority=runtime_authority, pass_fds=pass_fds)
    process_identity = None
    if process_observer is not None:
        try:
            process_identity = _process_identity(process.pid)
            process_observer(CommandProcessLifecycleEvent(kind="PROCESS_STARTED", physical_execution_id=physical_execution_id, pid=process.pid, process_identity=process_identity))
        except BaseException:
            _terminate_process_group(process)
            raise
    if max_capture_bytes_per_stream is not None and max_capture_bytes_per_stream < 1:
        raise ValueError("max_capture_bytes_per_stream must be positive when provided")
    capture = CommandCapture(max_capture_bytes_per_stream)
    stdout_reader = _reader(process.stdout, "stdout", capture.stdout, stream_output)
    stderr_reader = _reader(process.stderr, "stderr", capture.stderr, stream_output) if not merge_stderr else Thread(target=lambda: None, name="oh-goon-stderr-merged", daemon=True)
    readers = (stdout_reader, stderr_reader)
    for reader in readers:
        reader.start()
    return _StartedCommand(process, authority_receipt, physical_execution_id, process_identity, capture, readers)


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
    parent_death_signal: bool = False,
    runtime_authority: RuntimeAuthorityBinding | None = None,
    pass_fds: tuple[int, ...] = (),
    max_capture_bytes_per_stream: int | None = None,
    runtime_supervision: CommandRuntimeSupervision | None = None,
) -> CommandResult:
    """Run one command while optionally projecting transient output as it is read."""

    argv = tuple(args)
    started_command = _start_supervised_command(argv, cwd=cwd, env=env, stream_output=stream_output, process_observer=process_observer, merge_stderr=merge_stderr, parent_death_signal=parent_death_signal, runtime_authority=runtime_authority, pass_fds=pass_fds, max_capture_bytes_per_stream=max_capture_bytes_per_stream)
    process = started_command.process
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
    if watchdog is not None and timeout is not None:
        raise ValueError("runtime_supervision and fixed timeout cannot both own the runtime deadline")
    leader_exited_before_group_quiescence = False
    completion_cleanup_requested = False
    try:
        while True:
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
                    )
            if cancel_requested is not None and cancel_requested():
                raise _cancel_command(process, readers, args, authority_receipt, physical_execution_id=physical_execution_id, process_identity=process_identity, process_observer=process_observer)
            _raise_if_output_limited(process, readers, capture, authority_receipt)
            leader_running = process.poll() is None
            descendants_running = (
                not leader_running
                and require_process_group_quiescence
                and os.name != "nt"
                and _process_group_exists(process.pid)
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
                watchdog, runtime_supervision, process, readers, args, authority_receipt,
                physical_execution_id, process_identity, process_observer, leader_exited_before_group_quiescence,
            )
            if supervision_error is not None:
                raise supervision_error
            if timeout is not None and elapsed >= timeout:
                request = TerminationRequest(execution_id=physical_execution_id, generation=1, cause="watchdog", reason="max_runtime_exceeded", grace_seconds=TERMINATION_GRACE_SECONDS)
                if process_observer is not None:
                    process_observer(CommandProcessLifecycleEvent(kind="TERMINATION_REQUESTED", physical_execution_id=physical_execution_id, pid=process.pid, process_identity=process_identity, termination_reason=request.reason))
                termination_receipt = terminate_owned_process_tree(process, request)
                forced_termination = termination_receipt.escalated_to_kill
                forced_termination = _finish_process_output(process, readers) or forced_termination
                final_quiescent = termination_receipt.quiescent and (os.name == "nt" or not _process_group_exists(process.pid))
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
                        recovery_events=(ExecutionRecoveryEvent(kind="process_group_cleanup", action="terminate_process_group", reason_code="deadline_expired", outcome="requested"),),
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
        forced_termination = _finish_process_output(process, readers)
        _raise_if_output_limited(
            process, readers, capture, authority_receipt, already_finished=True
        )
        final_quiescent = os.name == "nt" or not _process_group_exists(process.pid)
        return CommandResult(
            argv=tuple(args), returncode=process.returncode, runtime_authority=authority_receipt,
            physical_execution_id=physical_execution_id,
            stdout=stdout_capture.text.rstrip(),
            stderr=stderr_capture.text.rstrip(),
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
    except BaseException:
        if process.poll() is None:
            _terminate_process_group(process)
        raise
    finally:
        if any(reader.is_alive() for reader in readers):
            _terminate_process_group(process)
            _finish_process_output(process, readers)
        _report_terminal_process_state(process, process_observer, physical_execution_id, process_identity)




def _runtime_supervision_error(
    watchdog: CommandWatchdog | None, supervision: CommandRuntimeSupervision | None,
    process: subprocess.Popen[str], readers: tuple[Thread, Thread], args: Sequence[str],
    authority_receipt: RuntimeAuthorityReceipt | None, physical_execution_id: str,
    process_identity: str | None, process_observer: CommandProcessObserver | None,
    leader_exited_before_group_quiescence: bool,
) -> BaseException | None:
    if watchdog is None or supervision is None:
        return None

    def record_termination(reason: str) -> None:
        if process_observer is not None:
            process_observer(CommandProcessLifecycleEvent(
                kind="TERMINATION_REQUESTED", physical_execution_id=physical_execution_id, pid=process.pid,
                process_identity=process_identity, termination_reason=reason,
            ))

    termination = evaluate_owned_process_watchdog(
        watchdog, supervision, process, readers, termination_requested=record_termination
    )
    if termination is None:
        return None
    supervisor = CommandSupervisorReceipt(
        leader_exited_before_group_quiescence=leader_exited_before_group_quiescence,
        process_group_cleanup_requested=True, cleanup_reason="timeout",
        forced_termination=termination.forced_termination, final_process_group_quiescent=termination.final_quiescent,
        recovery_events=(ExecutionRecoveryEvent(
            kind="process_group_cleanup", action="terminate_process_group",
            reason_code=termination.decision.reason, outcome="requested",
        ),),
        termination_receipt=termination.receipt,
    )
    if termination.decision.reason == "max_runtime_exceeded":
        return CommandTimeoutExpired(
            args, supervision.policy.max_runtime_seconds, runtime_authority=authority_receipt, supervisor=supervisor
        )
    return CommandSupervisionTerminated(
        f"Command terminated by runtime supervision: {termination.decision.reason}",
        decision=termination.decision, supervisor=supervisor, runtime_authority=authority_receipt,
    )


def _report_terminal_process_state(process: subprocess.Popen[str], observer: CommandProcessObserver | None, physical_execution_id: str, process_identity: str | None) -> None:
    if observer is None or process.poll() is None:
        return
    observer(CommandProcessLifecycleEvent(kind="PROCESS_EXITED", physical_execution_id=physical_execution_id, pid=process.pid, process_identity=process_identity, returncode=process.returncode))
    if os.name == "nt" or not _process_group_exists(process.pid):
        observer(CommandProcessLifecycleEvent(kind="DESCENDANTS_QUIESCENT", physical_execution_id=physical_execution_id, pid=process.pid, process_identity=process_identity))


def _cancel_command(
    process: subprocess.Popen[str], readers: tuple[Thread, Thread], args: Sequence[str],
    runtime_authority: RuntimeAuthorityReceipt | None, *, physical_execution_id: str,
    process_identity: str | None, process_observer: CommandProcessObserver | None,
) -> CommandCancelledError:
    request = TerminationRequest(execution_id=physical_execution_id, generation=1, cause="cancel", reason="cancellation_requested", grace_seconds=TERMINATION_GRACE_SECONDS)
    if process_observer is not None:
        process_observer(CommandProcessLifecycleEvent(kind="TERMINATION_REQUESTED", physical_execution_id=physical_execution_id, pid=process.pid, process_identity=process_identity, termination_reason=request.reason))
    termination_receipt = terminate_owned_process_tree(process, request)
    forced_termination = _finish_process_output(process, readers) or termination_receipt.escalated_to_kill
    final_quiescent = termination_receipt.quiescent and (os.name == "nt" or not _process_group_exists(process.pid))
    return CommandCancelledError(
        f"Command cancelled: {' '.join(args)}", runtime_authority=runtime_authority,
        supervisor=CommandSupervisorReceipt(process_group_cleanup_requested=True, cleanup_reason="cancel",
            forced_termination=forced_termination, final_process_group_quiescent=final_quiescent,
            recovery_events=(ExecutionRecoveryEvent(kind="process_group_cleanup", action="terminate_process_group", reason_code="cancellation_requested", outcome="requested"),),
            termination_receipt=termination_receipt),
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
    termination_receipt = terminate_owned_process_tree(process, request)
    forced_termination = (
        _finish_process_output(process, readers) or termination_receipt.escalated_to_kill
    )
    final_quiescent = termination_receipt.quiescent and (
        os.name == "nt" or not _process_group_exists(process.pid)
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
    runtime_authority: RuntimeAuthorityReceipt,
    *,
    already_finished: bool = False,
) -> None:
    if not capture.overflow.is_set():
        return
    raise _output_limit_error(
        process, readers, capture.stdout, capture.stderr, runtime_authority,
        already_finished=already_finished,
    )


def _output_limit_error(
    process: subprocess.Popen[str],
    readers: tuple[Thread, Thread],
    stdout_capture: TextCapture,
    stderr_capture: TextCapture,
    runtime_authority: RuntimeAuthorityReceipt,
    *,
    already_finished: bool = False,
) -> CommandOutputLimitExceeded:
    forced_termination = False
    if not already_finished:
        forced_termination = _terminate_process_group(process, close_pipes=False)
        forced_termination = _finish_process_output(process, readers) or forced_termination
    stream = stdout_capture.overflow_stream or stderr_capture.overflow_stream or "stdout"
    capture = stdout_capture if stream == "stdout" else stderr_capture
    assert capture.limit_bytes is not None
    return CommandOutputLimitExceeded(
        stream=stream,
        limit_bytes=capture.limit_bytes,
        stdout=stdout_capture.text,
        stderr=stderr_capture.text,
        runtime_authority=runtime_authority,
        supervisor=CommandSupervisorReceipt(
            process_group_cleanup_requested=True,
            cleanup_reason="output_limit",
            forced_termination=forced_termination,
            final_process_group_quiescent=(os.name == "nt" or not _process_group_exists(process.pid)),
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

def _reader(
    pipe,
    stream: Literal["stdout", "stderr"],
    capture: TextCapture,
    observer: CommandOutputObserver | None,
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
                if chunk:
                    capture.append(chunk, stream=stream)
                    if observer is not None:
                        try:
                            observer(stream, chunk)
                        except Exception:
                            # Observability must never stop process output draining.
                            pass
                if not raw:
                    break
        finally:
            if not pipe.closed:
                pipe.close()

    return Thread(target=read, name=f"oh-goon-{stream}", daemon=True)


