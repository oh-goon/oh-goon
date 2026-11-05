"""Internal, capability-only bootstrap for one already admitted FleetRun."""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path
from threading import Lock
from time import monotonic
from typing import TYPE_CHECKING, Literal

from pydantic import ConfigDict, Field, JsonValue

from oh_goon._application_worker_runtime import (
    RunWorkerConfiguration,
    build_claimed_run_execution,
    capture_runtime_worker_configuration,
)
from oh_goon.core.errors import FleetPlanInvalid, OperationCancelled
from oh_goon.core.models.background import RunWorkerExitCode
from oh_goon.core.models.base import ImmutableModel
from oh_goon.core.models.diagnostic import DiagnosticEvent
from oh_goon.core.process_birth import observe_process_birth
from oh_goon.engine.atomic_execution_bytes import (
    SealedExecutionBytes,
    atomic_execution_bytes_supported,
    seal_execution_bytes,
    verify_sealed_execution_descriptor,
)
from oh_goon.engine.command import bind_process_child_custody, native_child_custody_supported
from oh_goon.integrations.hashmarks import require_loaded_hashmarks_runtime
from oh_goon.runtime.database import DatabasePathIdentityLost, LocalDatabase, WorkerDatabaseContext
from oh_goon.runtime.execution import ExecutionCoordinator, ExecutionTicket
from oh_goon.runtime.execution.run_supervisor import RunWorkerLaunch
from oh_goon.runtime.execution.worker_observation import (
    ParentRunObservations,
    WorkerRunObservations,
)

if TYPE_CHECKING:
    from oh_goon._application_foundation import RuntimeFoundation

MAX_RUN_WORKER_HANDOFF_BYTES = 4 * 1024 * 1024


class _WorkerHandoff(ImmutableModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)
    schema_version: Literal["fleet-run-worker/v1"] = "fleet-run-worker/v1"
    database: WorkerDatabaseContext
    ticket: dict[str, JsonValue] = Field(repr=False)
    configuration: RunWorkerConfiguration = Field(repr=False)
    observations_descriptor: int = Field(ge=3)
    terminal_size: tuple[int, int] | None
    submitted_at: float


class _WorkerHeader(ImmutableModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, hide_input_in_errors=True)
    descriptor: int = Field(ge=3)
    identity: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    byte_count: int = Field(ge=1, le=MAX_RUN_WORKER_HANDOFF_BYTES)
    mode: Literal[0o400]
    kernel_device: int = Field(ge=0)
    kernel_inode: int = Field(ge=1)

    def sealed(self) -> SealedExecutionBytes:
        return SealedExecutionBytes(**self.model_dump())


def capture_run_worker_submission(
    foundation: RuntimeFoundation, fleet_run_id: str
) -> Callable[[ExecutionTicket], RunWorkerLaunch]:
    """Resolve transportability once before admission; never capture parent live handles."""
    if (
        not atomic_execution_bytes_supported()
        or observe_process_birth() is None
        or not native_child_custody_supported()
    ):
        raise FleetPlanInvalid(
            "Run workers require protected native transport and process identity"
        )
    configuration = capture_runtime_worker_configuration(foundation, fleet_run_id)
    if len(configuration.model_dump_json().encode("utf-8")) > MAX_RUN_WORKER_HANDOFF_BYTES - 65536:
        raise FleetPlanInvalid("Resolved Run worker configuration exceeds its protected transport")
    environment = dict(os.environ)
    cwd = Path.cwd().resolve()
    executable = sys.executable
    if not executable or not Path(executable).is_absolute():
        raise FleetPlanInvalid("Run worker requires the selected absolute Python executable")

    def prepare(ticket: ExecutionTicket) -> RunWorkerLaunch:
        if ticket.require_fleet_run_id() != fleet_run_id:
            raise FleetPlanInvalid("Run worker submission differs from captured Run")
        observations = ParentRunObservations(
            fleet_run_id, foundation.live_output, foundation.diagnostics, foundation.terminal
        )
        sealed: SealedExecutionBytes | None = None
        descriptor_lock = Lock()

        def close_descriptor() -> None:
            nonlocal sealed
            with descriptor_lock:
                selected, sealed = sealed, None
            if selected is not None:
                os.close(selected.descriptor)

        def cleanup() -> None:
            try:
                close_descriptor()
            finally:
                observations.close()

        def started() -> None:
            close_descriptor()
            observations.start()

        try:
            handoff = _WorkerHandoff(
                database=foundation.database.capture_worker_context(),
                ticket=ticket.export_worker_handoff(),
                configuration=configuration,
                observations_descriptor=observations.child_descriptor,
                terminal_size=foundation.terminal.initial_size if foundation.terminal else None,
                submitted_at=monotonic(),
            )
            packet = handoff.model_dump_json().encode("utf-8")
            if len(packet) > MAX_RUN_WORKER_HANDOFF_BYTES:
                raise FleetPlanInvalid("Run worker authority handoff exceeds its bounded transport")
            # Private scratch is removed before launch. The sealed descriptor is
            # the only retained copy of the transported ticket and enrollment.
            with tempfile.TemporaryDirectory(prefix="oh-goon-worker-") as directory:
                source = Path(directory) / "authority.json"
                descriptor = os.open(source, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(descriptor, "wb") as stream:
                    stream.write(packet)
                source.chmod(0o400)
                sealed = seal_execution_bytes(source, label="Run worker authority")
            header = _WorkerHeader(
                **{key: getattr(sealed, key) for key in _WorkerHeader.model_fields}
            ).model_dump_json()
            return RunWorkerLaunch(
                argv=(executable, "-I", "-m", "oh_goon._application_worker", "--handoff", header),
                cwd=cwd,
                environment=environment,
                pass_fds=(sealed.descriptor, observations.child_descriptor),
                on_started=started,
                cleanup=cleanup,
            )
        except BaseException:
            cleanup()
            raise

    return prepare


def _restore_handoff(header: str) -> _WorkerHandoff:
    if len(header) > 2048:
        raise FleetPlanInvalid("Run worker header exceeds its bound")
    try:
        selected = _WorkerHeader.model_validate_json(header)
        sealed = selected.sealed()
        try:
            verify_sealed_execution_descriptor(sealed)
            packet = os.pread(selected.descriptor, selected.byte_count, 0)
        finally:
            os.close(selected.descriptor)
        handoff = _WorkerHandoff.model_validate_json(packet)
        ticket = ExecutionTicket.from_worker_handoff(handoff.ticket)
        if ticket.require_fleet_run_id() != handoff.configuration.fleet_run_id:
            raise ValueError("Run identity mismatch")
        return handoff
    except (ValueError, OSError, RecursionError):
        raise FleetPlanInvalid("Run worker protected handoff is invalid") from None


def execute_handoff(header: str) -> int:
    """The sole fresh-process Run entry: reopen, atomically claim, then compose and execute."""
    observations: WorkerRunObservations | None = None
    fleet_run_id: str | None = None
    failure_cut = RunWorkerExitCode.HANDOFF_REFUSED
    try:
        handoff = _restore_handoff(header)
        fleet_run_id = handoff.configuration.fleet_run_id
        observations = WorkerRunObservations(
            handoff.configuration.fleet_run_id, handoff.observations_descriptor
        )
        failure_cut = RunWorkerExitCode.RUNTIME_IDENTITY_REFUSED
        require_loaded_hashmarks_runtime(
            expected_implementation_identity=handoff.configuration.hashmarks_implementation_identity
        )
        failure_cut = RunWorkerExitCode.DATABASE_UNAVAILABLE
        database = LocalDatabase.open_worker(handoff.database)
        failure_cut = RunWorkerExitCode.CLAIM_REFUSED
        claimed = ExecutionCoordinator(database).claim_fleet_worker(
            ExecutionTicket.from_worker_handoff(handoff.ticket)
        )
        claimed_at = monotonic()
        failure_cut = RunWorkerExitCode.BOOTSTRAP_FAILED
        bind_process_child_custody()
        execution = build_claimed_run_execution(
            database,
            claimed,
            handoff.configuration,
            terminal=observations.terminal(handoff.terminal_size),
            live_output=observations,
            diagnostics=observations,
        )
        ready_at = monotonic()
        observations.emit(
            DiagnosticEvent(
                severity="INFO",
                source="run_worker",
                owner="RunWorker",
                operation="start_execution",
                message=(
                    f"spawn_to_claim_ms={(claimed_at - handoff.submitted_at) * 1000:.1f}; "
                    f"claimed_bootstrap_ms={(ready_at - claimed_at) * 1000:.1f}"
                ),
                execution_affected=False,
                fleet_run_id=claimed.require_fleet_run_id(),
            )
        )
        failure_cut = RunWorkerExitCode.EXECUTION_FAILED
        execution.execute(claimed)
        return 0
    except BaseException as error:
        if isinstance(error, (DatabasePathIdentityLost, sqlite3.OperationalError)):
            failure_cut = RunWorkerExitCode.DATABASE_UNAVAILABLE
        elif isinstance(error, OperationCancelled):
            failure_cut = RunWorkerExitCode.CANCELLATION_OBSERVED
        # Protected authority and arbitrary exception text must not reach argv,
        # stderr, observation frames, or the parent failure mailbox.
        if observations is not None:
            observations.emit(
                DiagnosticEvent(
                    severity="INFO"
                    if failure_cut is RunWorkerExitCode.CANCELLATION_OBSERVED
                    else "ERROR",
                    source="run_worker",
                    owner="RunWorker",
                    operation="stop_execution",
                    message=failure_cut.name,
                    execution_affected=True,
                    failure_class=failure_cut.name,
                    fleet_run_id=fleet_run_id,
                )
            )
        print(
            f"Run worker stopped: {failure_cut.name}; inspect canonical Run evidence",
            file=sys.stderr,
        )
        return int(failure_cut)
    finally:
        if observations is not None:
            try:
                observations.close()
            except Exception:
                # The parent reports absent final observation accounting. A
                # display-channel cleanup failure cannot replace Run truth.
                print("Run worker observation cleanup incomplete", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Internal admitted Run worker")
    parser.add_argument("--handoff", required=True)
    arguments = parser.parse_args(argv)
    return execute_handoff(arguments.handoff)


if __name__ == "__main__":
    raise SystemExit(main())
