"""Internal, capability-only bootstrap for one already admitted FleetRun."""

from __future__ import annotations

import argparse
import json
import os
import socket
import sqlite3
import sys
import tempfile
from pathlib import Path
from threading import Lock
from time import monotonic
from typing import TYPE_CHECKING, Literal

from pydantic import ConfigDict, Field, JsonValue, PrivateAttr, model_validator

from oh_goon._application_worker_runtime import (
    RunWorkerConfiguration,
    build_claimed_run_execution,
    capture_runtime_worker_configuration,
)
from oh_goon.core.errors import FleetPlanInvalid, OperationCancelled
from oh_goon.core.models.background import RunWorkerExitCode
from oh_goon.core.models.base import ImmutableModel
from oh_goon.core.models.diagnostic import DiagnosticEvent
from oh_goon.core.models.run_resources import RunResourceEnvelopeEvidence
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
from oh_goon.runtime.execution.run_supervisor import RunWorkerLaunch, RunWorkerPreparation
from oh_goon.runtime.execution.worker_observation import (
    ParentRunObservations,
    WorkerRunObservations,
)

if TYPE_CHECKING:
    from oh_goon._application_foundation import RuntimeFoundation
    from oh_goon.core.models.agent_providers import ResolvedNativeAgent
    from oh_goon.integrations.run_resource_envelope import (
        DeferredWorkerResourceScope,
        PinnedRunResourceScope,
        VerifiedWorkerResourceClaim,
        VerifiedWorkerResourceClosure,
    )

MAX_RUN_WORKER_HANDOFF_BYTES = 4 * 1024 * 1024
_RESOURCE_READY_BYTES = 64 * 1024
_RESOURCE_READY_TIMEOUT_SECONDS = 30.0


class _WorkerHandoff(ImmutableModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)
    schema_version: Literal["fleet-run-worker/v1"] = "fleet-run-worker/v1"
    database: WorkerDatabaseContext
    ticket: dict[str, JsonValue] = Field(repr=False)
    configuration: RunWorkerConfiguration = Field(repr=False)
    observations_descriptor: int = Field(ge=3)
    terminal_size: tuple[int, int] | None
    submitted_at: float
    resource_descriptor: int | None = Field(
        default=None, ge=3, exclude_if=lambda value: value is None
    )
    _verified_transport_digest: str | None = PrivateAttr(default=None)

    @model_validator(mode="after")
    def require_resource_transport(self) -> _WorkerHandoff:
        if (
            (self.resource_descriptor is None)
            != (self.configuration.deferred_worker_resource_scope is None)
            or self.resource_descriptor == self.observations_descriptor
            or (self.resource_descriptor is not None and self.terminal_size is not None)
        ):
            raise ValueError("Deferred resources require their own original worker channel")
        return self


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


class _WorkerResourceHandoff:
    """Pin the actual original worker scope before its canonical claim can run."""

    def __init__(self, deferred: DeferredWorkerResourceScope) -> None:
        self.deferred = deferred
        self._parent, self._child = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
        try:
            self._parent.settimeout(_RESOURCE_READY_TIMEOUT_SECONDS)
            self._child.set_inheritable(False)
        except BaseException:
            self._parent.close()
            self._child.close()
            raise
        self._digest: str | None = None
        self._envelope: RunResourceEnvelopeEvidence | None = None
        self._pinned: PinnedRunResourceScope | None = None
        self._closure: VerifiedWorkerResourceClosure | None = None

    @property
    def child_descriptor(self) -> int:
        return self._child.fileno()

    def bind_transport(self, digest: str) -> None:
        if self._digest is not None:
            raise FleetPlanInvalid("Worker resource transport is already bound")
        self._digest = digest

    def started(self) -> None:
        from oh_goon.integrations.run_resource_envelope import pin_started_run_resource_scope

        self._child.close()
        try:
            packet = self._parent.recv(_RESOURCE_READY_BYTES + 1)
            if not packet or len(packet) > _RESOURCE_READY_BYTES:
                raise ValueError("Invalid readiness packet")
            payload = json.loads(packet)
            if (
                not isinstance(payload, dict)
                or set(payload) != {"handoff_sha256", "envelope"}
                or self._digest is None
                or payload["handoff_sha256"] != self._digest
            ):
                raise ValueError("Worker readiness differs from its original transport")
            envelope = RunResourceEnvelopeEvidence.model_validate(payload["envelope"])
            if envelope.subject != "worker":
                raise ValueError("Worker readiness has another physical subject")
            self._envelope = envelope
            self._pinned = pin_started_run_resource_scope(
                self.deferred.scope,
                envelope,
                expected_birth=envelope.process_birth,
                handoff_sha256=self._digest,
            )
            acknowledgement = b"PINNED " + self._digest.encode("ascii")
            if self._parent.send(acknowledgement) != len(acknowledgement):
                raise OSError("Worker resource acknowledgement was interrupted")
        except (OSError, ValueError, UnicodeError, RecursionError):
            raise FleetPlanInvalid("Original worker resource readiness was refused") from None

    def finish(self) -> object:
        from oh_goon.integrations.run_resource_envelope import finish_worker_resource_scope

        if self._closure is None:
            # The native owner's receipt closes this exact unit generation; it
            # remains the original proof during an idempotent DB-release retry.
            self._closure = finish_worker_resource_scope(
                self.deferred, self._envelope, pinned=self._pinned
            )
        return self._closure

    def close_transport(self) -> None:
        self._parent.close()
        self._child.close()

    def close_pin(self) -> None:
        if self._pinned is not None:
            self._pinned.close()


def _observe_worker_resources(
    handoff: _WorkerHandoff,
) -> tuple[RunResourceEnvelopeEvidence, VerifiedWorkerResourceClaim]:
    from oh_goon.integrations.run_resource_envelope import (
        DeferredWorkerResourceScope,
        observe_worker_resource_scope,
        verify_worker_resource_claim,
    )

    descriptor, digest = handoff.resource_descriptor, handoff._verified_transport_digest
    payload = handoff.configuration.deferred_worker_resource_scope
    if descriptor is None or digest is None or payload is None:
        raise FleetPlanInvalid("Original deferred worker resources are unavailable")
    deferred = DeferredWorkerResourceScope.from_payload(payload)
    with socket.socket(fileno=descriptor) as channel:
        channel.set_inheritable(False)
        if channel.type != socket.SOCK_SEQPACKET:
            raise FleetPlanInvalid("Original worker resource channel is unavailable")
        channel.settimeout(_RESOURCE_READY_TIMEOUT_SECONDS)
        envelope = observe_worker_resource_scope(deferred, handoff_sha256=digest)
        packet = json.dumps(
            {"handoff_sha256": digest, "envelope": envelope.model_dump(mode="json")},
            ensure_ascii=True,
            separators=(",", ":"),
        ).encode()
        if len(packet) > _RESOURCE_READY_BYTES or channel.send(packet) != len(packet):
            raise FleetPlanInvalid("Original worker resource readiness was interrupted")
        if channel.recv(128) != b"PINNED " + digest.encode("ascii"):
            raise FleetPlanInvalid("Original worker resource readiness was not pinned")
    claim = verify_worker_resource_claim(deferred, envelope)
    return envelope, claim


def capture_run_worker_submission(
    foundation: RuntimeFoundation, fleet_run_id: str
) -> RunWorkerPreparation:
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
    deferred = None
    if configuration.deferred_worker_resource_scope is not None:
        from oh_goon.integrations.run_resource_envelope import DeferredWorkerResourceScope

        deferred = DeferredWorkerResourceScope.from_payload(
            configuration.deferred_worker_resource_scope
        )
    if len(configuration.model_dump_json().encode("utf-8")) > MAX_RUN_WORKER_HANDOFF_BYTES - 65536:
        raise FleetPlanInvalid("Resolved Run worker configuration exceeds its protected transport")
    environment = dict(os.environ)
    cwd = Path.cwd().resolve()
    executable = sys.executable
    if not executable or not Path(executable).is_absolute():
        raise FleetPlanInvalid("Run worker requires the selected absolute Python executable")

    def require_native_binding(repository: Path, selected: ResolvedNativeAgent) -> None:
        isolation = configuration.standard_process_isolation
        if (
            configuration.git.native_repository != repository
            or configuration.git.native_agent != selected
            or isolation is None
            or isolation.kind != selected.profile.backend
            or isolation.configuration_identity != selected.backend_configuration_id
        ):
            raise FleetPlanInvalid(
                "Scheduled native Run requires its original scoped Git and isolation binding"
            )

    def prepare(ticket: ExecutionTicket) -> RunWorkerLaunch:
        if ticket.require_fleet_run_id() != fleet_run_id:
            raise FleetPlanInvalid("Run worker submission differs from captured Run")
        observations = ParentRunObservations(
            fleet_run_id, foundation.live_output, foundation.diagnostics, foundation.terminal
        )
        try:
            resources = _WorkerResourceHandoff(deferred) if deferred is not None else None
        except BaseException:
            observations.close()
            raise
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
                try:
                    observations.close()
                finally:
                    if resources is not None:
                        resources.close_transport()

        def started() -> None:
            close_descriptor()
            observations.start()
            if resources is not None:
                resources.started()

        try:
            handoff = _WorkerHandoff(
                database=foundation.database.capture_worker_context(),
                ticket=ticket.export_worker_handoff(),
                configuration=configuration,
                observations_descriptor=observations.child_descriptor,
                terminal_size=foundation.terminal.initial_size if foundation.terminal else None,
                submitted_at=monotonic(),
                resource_descriptor=resources.child_descriptor if resources is not None else None,
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
            argv: tuple[str, ...] = (
                executable,
                "-I",
                "-m",
                "oh_goon._application_worker",
                "--handoff",
                header,
            )
            pass_fds: tuple[int, ...] = (sealed.descriptor, observations.child_descriptor)
            selected_environment = environment
            if resources is not None:
                assert deferred is not None
                resources.bind_transport(sealed.identity)
                argv = deferred.scope.command(sealed.identity, argv)
                selected_environment = {**environment, **deferred.scope.environment}
                pass_fds = (*pass_fds, resources.child_descriptor)
            return RunWorkerLaunch(
                argv=argv,
                cwd=cwd,
                environment=selected_environment,
                pass_fds=pass_fds,
                on_started=started,
                cleanup=cleanup,
                finalize=resources.finish if resources is not None else None,
                finalization_cleanup=resources.close_pin if resources is not None else None,
            )
        except BaseException:
            cleanup()
            raise

    return RunWorkerPreparation(
        fleet_run_id,
        prepare,
        concurrency=deferred.admission if deferred is not None else None,
        deferred_resource_scope=configuration.deferred_worker_resource_scope,
        require_native_binding=require_native_binding,
    )


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
        handoff._verified_transport_digest = selected.identity
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
        worker_envelope: RunResourceEnvelopeEvidence | None = None
        resource_claim: VerifiedWorkerResourceClaim | None = None
        if handoff.configuration.deferred_worker_resource_scope is not None:
            failure_cut = RunWorkerExitCode.RESOURCE_SCOPE_REFUSED
            worker_envelope, resource_claim = _observe_worker_resources(handoff)
        failure_cut = RunWorkerExitCode.DATABASE_UNAVAILABLE
        database = LocalDatabase.open_worker(handoff.database)
        failure_cut = RunWorkerExitCode.CLAIM_REFUSED
        original_ticket = ExecutionTicket.from_worker_handoff(handoff.ticket)
        coordinator = ExecutionCoordinator(database)
        if worker_envelope is None:
            claimed = coordinator.claim_fleet_worker(original_ticket)
        else:
            claimed = coordinator.claim_fleet_worker(
                original_ticket, resource_envelope=worker_envelope, resource_claim=resource_claim
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
            worker_resource_envelope=worker_envelope,
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
