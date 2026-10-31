"""Transport one captured invocation into the same CLI under a native Run envelope."""

from __future__ import annotations

import argparse
import json
import os
import select
import signal
import sqlite3
import stat
import sys
import tempfile
import threading
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from types import FrameType
from typing import TYPE_CHECKING, Any, Literal

from pydantic import ConfigDict, Field, field_validator

from oh_goon.cli_runtime_plans import _RUN_RESULT_SCHEMA
from oh_goon.core.errors import FleetDomainError, FleetPlanInvalid, RunConfigurationInvalid
from oh_goon.core.models.base import ImmutableModel
from oh_goon.core.models.run_resources import (
    RunResourceEnvelopeEvidence,
    RunResourceEnvelopeObservation,
)
from oh_goon.core.process_birth import ProcessBirthIdentity, observe_process_birth
from oh_goon.core.product_identity import new_fleet_run_id
from oh_goon.core.sqlite_journal_vocabulary import SQLITE_JOURNAL_MODE_ENV
from oh_goon.engine.atomic_execution_bytes import (
    SealedExecutionBytes,
    seal_execution_bytes,
    verify_sealed_execution_descriptor,
)
from oh_goon.engine.command import CommandProcessLifecycleEvent, run_command
from oh_goon.integrations.exact_file_write import publish_text_file
from oh_goon.integrations.run_resource_envelope import (
    PinnedRunResourceScope,
    ResolvedRunResourceScope,
    finish_run_resource_scope,
    pin_started_run_resource_scope,
    prepare_run_resource_scope,
    verify_current_run_resource_scope,
)
from oh_goon.runtime._database_policy import sqlite_journal_mode_for_path
from oh_goon.runtime.database import database_path_for_environment
from oh_goon.runtime.journal import Journal, RunResourceObservationContext
from oh_goon.runtime.league_folders import league_preferences_path_for_environment
from oh_goon.services.run_configuration import (
    MAX_RUN_CONFIGURATION_HANDOFF_BYTES,
    RunConfigurationSnapshot,
    decode_run_configuration_snapshot,
    encode_run_configuration_snapshot,
)

MAX_CLI_RESOURCE_HANDOFF_BYTES = 512 * 1024
MAX_RUN_CONTROL_OBSERVATION_BYTES = 32 * 1024

if TYPE_CHECKING:
    from oh_goon.application import ApplicationRuntime


class ResolvedCliState(ImmutableModel):
    database: Path
    league_preferences: Path
    sqlite_journal_mode: Literal["DELETE", "WAL"]

    @field_validator("database", "league_preferences")
    @classmethod
    def require_resolved_path(cls, value: Path) -> Path:
        if not value.is_absolute() or ".." in value.parts:
            raise ValueError("CLI runtime state requires explicitly resolved absolute paths")
        return value

    @classmethod
    def capture(cls, environment: Mapping[str, str]) -> ResolvedCliState:
        database = database_path_for_environment(environment)
        mode = sqlite_journal_mode_for_path(
            database, requested=environment.get(SQLITE_JOURNAL_MODE_ENV, "AUTO")
        )
        return cls.model_validate(
            {
                "database": database,
                "league_preferences": league_preferences_path_for_environment(environment),
                "sqlite_journal_mode": mode,
            }
        )

    def restore(self) -> None:
        # These values are control-plane configuration, deliberately removed by
        # the original command owner from ordinary Step subprocess environments.
        os.environ["OH_GOON_DB"] = str(self.database)
        os.environ["OH_GOON_LEAGUE_FILE"] = str(self.league_preferences)
        os.environ[SQLITE_JOURNAL_MODE_ENV] = self.sqlite_journal_mode


class _CliResourceHandoff(ImmutableModel):
    schema_version: Literal["run-resource-cli/v1"] = "run-resource-cli/v1"
    configuration_snapshot: str = Field(max_length=MAX_RUN_CONFIGURATION_HANDOFF_BYTES)
    state: ResolvedCliState
    scope: dict[str, Any]
    observation_path: Path
    acknowledgement_descriptor: int = Field(ge=3)

    @field_validator("observation_path")
    @classmethod
    def require_private_absolute_path(cls, value: Path) -> Path:
        if not value.is_absolute() or ".." in value.parts:
            raise ValueError("Run observation transport requires an absolute private path")
        return value


class _SealedHandoffHeader(ImmutableModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    descriptor: int = Field(ge=3)
    identity: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    byte_count: int = Field(ge=1, le=MAX_CLI_RESOURCE_HANDOFF_BYTES)
    mode: Literal[0o400]
    kernel_device: int = Field(ge=0)
    kernel_inode: int = Field(ge=1)

    def sealed(self) -> SealedExecutionBytes:
        return SealedExecutionBytes(**self.model_dump())


@dataclass(frozen=True, slots=True)
class CapturedRunResourceHandoff:
    snapshot: RunConfigurationSnapshot
    state: ResolvedCliState
    scope: ResolvedRunResourceScope
    observation_path: Path
    sha256: str
    acknowledgement_descriptor: int

    @property
    def terminal_path(self) -> Path:
        return self.observation_path.with_name("terminal.json")


@dataclass(slots=True)
class PreparedRunResourceHandoff:
    header: str
    descriptor: int
    sha256: str
    observation_path: Path
    acknowledgement_read: int
    acknowledgement_write: int
    cleanup_errors: list[str] = field(default_factory=list)

    @property
    def terminal_path(self) -> Path:
        return self.observation_path.with_name("terminal.json")


@contextmanager
def prepare_run_resource_handoff(
    snapshot: RunConfigurationSnapshot,
    state: ResolvedCliState,
    scope: ResolvedRunResourceScope,
) -> Iterator[PreparedRunResourceHandoff]:
    """Own private bounded scratch and an immutable FD until the native CLI finishes."""
    temporary: tempfile.TemporaryDirectory[str] | None = None
    sealed: SealedExecutionBytes | None = None
    prepared: PreparedRunResourceHandoff | None = None
    acknowledgement_read: int | None = None
    acknowledgement_write: int | None = None
    primary: BaseException | None = None
    try:
        temporary = tempfile.TemporaryDirectory(prefix="oh-goon-run-control-")
        acknowledgement_read, acknowledgement_write = os.pipe2(os.O_CLOEXEC)
        root = Path(temporary.name)
        observation_path = root / "started.json"
        packet = (
            _CliResourceHandoff(
                configuration_snapshot=encode_run_configuration_snapshot(snapshot).decode("utf-8"),
                state=state,
                scope=scope.to_payload(),
                observation_path=observation_path,
                acknowledgement_descriptor=acknowledgement_read,
            )
            .model_dump_json(exclude_computed_fields=True)
            .encode("utf-8")
        )
        if len(packet) > MAX_CLI_RESOURCE_HANDOFF_BYTES:
            raise RunConfigurationInvalid("Run resource handoff exceeds its bound")
        source = root / "handoff.json"
        with source.open("wb") as stream:
            stream.write(packet)
            stream.flush()
            os.fchmod(stream.fileno(), 0o400)
        sealed = seal_execution_bytes(source, label="Run resource handoff")
        source.unlink()
        header = _SealedHandoffHeader(
            descriptor=sealed.descriptor,
            identity=sealed.identity,
            byte_count=sealed.byte_count,
            mode=0o400,
            kernel_device=sealed.kernel_device,
            kernel_inode=sealed.kernel_inode,
        ).model_dump_json()
        prepared = PreparedRunResourceHandoff(
            header,
            sealed.descriptor,
            sealed.identity,
            observation_path,
            acknowledgement_read,
            acknowledgement_write,
        )
        yield prepared
    except BaseException as error:
        primary = error
        raise
    finally:
        failures: list[str] = []
        for descriptor, code in (
            (sealed.descriptor if sealed is not None else None, "HANDOFF_DESCRIPTOR_CLOSE_FAILED"),
            (acknowledgement_read, "HANDOFF_ACK_READ_CLOSE_FAILED"),
            (acknowledgement_write, "HANDOFF_ACK_WRITE_CLOSE_FAILED"),
        ):
            if descriptor is not None:
                try:
                    os.close(descriptor)
                except OSError:
                    failures.append(code)
        if temporary is not None:
            try:
                temporary.cleanup()
            except OSError:
                failures.append("HANDOFF_SCRATCH_CLEANUP_FAILED")
        if prepared is not None:
            prepared.cleanup_errors.extend(failures)
        if primary is not None:
            for code in failures:
                primary.add_note(code)


def restore_run_resource_handoff(header: str) -> CapturedRunResourceHandoff:
    """Check FD custody and full payload identity; native unit verification still follows."""
    if len(header) > 2048:
        raise RunConfigurationInvalid("Run resource handoff header exceeds its bound")
    try:
        selected = _SealedHandoffHeader.model_validate_json(header)
    except ValueError as exc:
        raise RunConfigurationInvalid("Run resource handoff header is invalid") from exc
    try:
        sealed = selected.sealed()
        verify_sealed_execution_descriptor(sealed)
        packet = os.pread(selected.descriptor, selected.byte_count, 0)
    finally:
        os.close(selected.descriptor)
    try:
        parsed = _CliResourceHandoff.model_validate_json(packet)
        snapshot = decode_run_configuration_snapshot(parsed.configuration_snapshot.encode("utf-8"))
        scope = ResolvedRunResourceScope.from_payload(parsed.scope)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise RunConfigurationInvalid("Run resource handoff payload is invalid") from exc
    if snapshot.selection.resources is None or snapshot.selection.resources != scope.resources:
        raise FleetPlanInvalid("Captured Run resource request differs from native scope selection")
    return CapturedRunResourceHandoff(
        snapshot,
        parsed.state,
        scope,
        parsed.observation_path,
        selected.identity,
        parsed.acknowledgement_descriptor,
    )


def verify_run_resource_handoff(
    header: str, configuration: Path
) -> tuple[CapturedRunResourceHandoff, RunResourceEnvelopeEvidence]:
    """Bind the entire restored packet to its actual native unit before application work."""
    captured = restore_run_resource_handoff(header)
    try:
        # The original loader admits either this exact file or its containing
        # directory. Verify that same selection against the sealed capture;
        # consulting the filesystem here would resolve the invocation again.
        if configuration.absolute() not in {
            captured.snapshot.path,
            captured.snapshot.path.parent,
        }:
            raise FleetPlanInvalid("Run resource handoff selects another configuration")
        evidence = verify_current_run_resource_scope(captured.scope, handoff_sha256=captured.sha256)
        _require_private_control_parent(captured.observation_path)
        publish_text_file(captured.observation_path, evidence.model_dump_json())
        available, _, _ = select.select([captured.acknowledgement_descriptor], [], [], 30)
        if not available or os.read(captured.acknowledgement_descriptor, 1) != b"1":
            raise FleetPlanInvalid(
                "Original Run resource parent did not acknowledge pinned custody"
            )
        return captured, evidence
    finally:
        os.close(captured.acknowledgement_descriptor)


def _require_private_control_parent(path: Path) -> None:
    parent = path.parent
    metadata = parent.stat(follow_symlinks=False)
    if (
        not path.is_absolute()
        or ".." in path.parts
        or parent.resolve(strict=True) != parent
        or not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or stat.S_IMODE(metadata.st_mode) != 0o700
        or parent.is_symlink()
    ):
        raise FleetPlanInvalid("Run resource observation parent is not original private scratch")


class RunResourceSignalForwarder:
    """Forward original caller signals through one birth-matched Linux pidfd."""

    def __init__(self) -> None:
        self.descriptor: int | None = None
        self.birth: ProcessBirthIdentity | None = None
        self.pending: int | None = None
        self.requested: int | None = None
        self.failure: str | None = None
        self.delivered = False
        self.cleanup_errors: list[str] = []

    def request(self, number: int, _frame: FrameType | None) -> None:
        self.pending = number
        if self.requested is None:
            self.requested = number

    def observe(self, event: CommandProcessLifecycleEvent) -> None:
        if event.kind != "PROCESS_STARTED":
            return
        if event.pid is None or event.process_identity is None or self.descriptor is not None:
            raise FleetPlanInvalid("Run resource launcher has no unique original process birth")
        descriptor = os.pidfd_open(event.pid)
        try:
            birth = observe_process_birth(event.pid)
            if birth is None or f"{birth.boot_id}:{birth.start_ticks}" != event.process_identity:
                raise FleetPlanInvalid("Run resource launcher process birth changed")
        except BaseException:
            os.close(descriptor)
            raise
        self.descriptor = descriptor
        self.birth = birth
        self.pump()

    def pump(self) -> None:
        if self.pending is None or self.descriptor is None:
            return
        number, self.pending = self.pending, None
        try:
            signal.pidfd_send_signal(self.descriptor, number)
            self.delivered = True
        except ProcessLookupError:
            self.failure = "ORIGINAL_PROCESS_ALREADY_EXITED"
        except OSError:
            self.failure = "ORIGINAL_PROCESS_SIGNAL_UNCONFIRMED"

    def close(self) -> None:
        if self.descriptor is not None:
            descriptor, self.descriptor = self.descriptor, None
            try:
                os.close(descriptor)
            except OSError:
                self.cleanup_errors.append("RUN_RESOURCE_PIDFD_CLOSE_FAILED")


@contextmanager
def original_run_resource_signals() -> Iterator[RunResourceSignalForwarder]:
    if (
        threading.current_thread() is not threading.main_thread()
        or not hasattr(os, "pidfd_open")
        or not hasattr(signal, "pidfd_send_signal")
        or signal.getsignal(signal.SIGINT) is not signal.default_int_handler
        or signal.getsignal(signal.SIGTERM) != signal.SIG_DFL
    ):
        raise FleetPlanInvalid("Whole-Run resources require the original Linux CLI signal owner")
    # Establish host support before any resource-scoped child can start work.
    probe = os.pidfd_open(os.getpid())
    os.close(probe)
    forwarder = RunResourceSignalForwarder()
    previous = {
        number: signal.signal(number, forwarder.request)
        for number in (signal.SIGINT, signal.SIGTERM)
    }
    try:
        yield forwarder
    finally:
        for number, handler in previous.items():
            if signal.getsignal(number) == forwarder.request:
                signal.signal(number, handler)
        forwarder.close()


def _read_control_json(path: Path) -> bytes:
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    with os.fdopen(descriptor, "rb") as stream:
        before = os.fstat(stream.fileno())
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != os.geteuid()
            or stat.S_IMODE(before.st_mode) & 0o077
            or before.st_size > MAX_RUN_CONTROL_OBSERVATION_BYTES
        ):
            raise FleetPlanInvalid("Run control observation is not bounded private evidence")
        payload = stream.read(MAX_RUN_CONTROL_OBSERVATION_BYTES + 1)
        after = os.fstat(stream.fileno())
        if len(payload) > MAX_RUN_CONTROL_OBSERVATION_BYTES or (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
        ) != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
            raise FleetPlanInvalid("Run control observation changed during its bounded read")
        return payload


def publish_terminal_run_resource_context(
    runtime: ApplicationRuntime,
    captured: CapturedRunResourceHandoff,
    evidence: RunResourceEnvelopeEvidence,
) -> None:
    """Publish only the original terminal cut after all child runtime writes settle."""
    _require_private_control_parent(captured.terminal_path)
    context = runtime.runs.capture_run_resource_observation_context(evidence)
    payload = context.model_dump_json()
    if len(payload.encode("utf-8")) > MAX_RUN_CONTROL_OBSERVATION_BYTES:
        raise FleetPlanInvalid("Terminal Run resource context exceeds its bound")
    publish_text_file(captured.terminal_path, payload)


class _RunResourceParent:
    def __init__(
        self,
        scope: ResolvedRunResourceScope,
        handoff: PreparedRunResourceHandoff,
        signals: RunResourceSignalForwarder,
    ) -> None:
        self.scope, self.handoff, self.signals = scope, handoff, signals
        self.started: RunResourceEnvelopeEvidence | None = None
        self.pinned: PinnedRunResourceScope | None = None
        self.cleanup_errors: list[str] = []

    def pump(self) -> None:
        self.signals.pump()
        if self.pinned is not None or not self.handoff.observation_path.exists():
            return
        evidence = RunResourceEnvelopeEvidence.model_validate_json(
            _read_control_json(self.handoff.observation_path)
        )
        if self.signals.birth is None or evidence.process_birth != self.signals.birth:
            raise FleetPlanInvalid("Started resource evidence differs from original process birth")
        self.started = evidence
        self.pinned = pin_started_run_resource_scope(
            self.scope,
            evidence,
            expected_birth=self.signals.birth,
            handoff_sha256=self.handoff.sha256,
        )
        if os.write(self.handoff.acknowledgement_write, b"1") != 1:
            raise FleetPlanInvalid("Run resource parent readiness acknowledgement failed")

    @staticmethod
    def publish_native_evidence(observation: RunResourceEnvelopeObservation) -> None:
        print(
            json.dumps(
                {
                    "schema": "oh-goon.run-resource-observation/v1",
                    "native_resource_observation": observation.model_dump(mode="json"),
                },
                sort_keys=True,
            ),
            file=sys.stderr,
            flush=True,
        )

    def finish(self) -> RunResourceEnvelopeObservation:
        try:
            return finish_run_resource_scope(
                self.scope,
                self.started,
                expected_birth=self.signals.birth,
                handoff_sha256=self.handoff.sha256,
                pinned=self.pinned,
                before_cleanup=self.publish_native_evidence,
            )
        finally:
            if self.pinned is not None:
                try:
                    self.pinned.close()
                except OSError:
                    self.cleanup_errors.append("RUN_RESOURCE_PIN_CLOSE_FAILED")


def run_with_resource_envelope(
    args: argparse.Namespace, snapshot: RunConfigurationSnapshot, argv: list[str]
) -> int:
    """Launch one fresh original CLI; Fleet remains the sole workload execution owner."""
    resources = snapshot.selection.resources
    if resources is None or args.fleet_run_id is not None:
        raise FleetPlanInvalid("Whole-Run resources require a fresh ordinary waited CLI Run")
    # The original parser selected the literal Run command, possibly after a root '--'.
    handoff_position = argv.index("run") + 1
    environment = dict(os.environ)
    result = None
    observation = None
    persistence = "INCOMPLETE"
    operational_errors: list[str] = []
    with original_run_resource_signals() as signals:
        scope = prepare_run_resource_scope(new_fleet_run_id(), resources)
        state = ResolvedCliState.capture(environment)
        if signals.requested is not None:
            return 128 + signals.requested
        with prepare_run_resource_handoff(snapshot, state, scope) as handoff:
            parent = _RunResourceParent(scope, handoff, signals)
            child_environment = dict(environment)
            for key in ("XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS"):
                child_environment.pop(key, None)
            child_environment.update(scope.environment)

            def stream_output(stream: Literal["stdout", "stderr"], text: str) -> None:
                if stream == "stdout" and args.as_json:
                    return
                output = sys.stdout if stream == "stdout" else sys.stderr
                output.write(text)
                output.flush()

            command = scope.command(
                handoff.sha256,
                (
                    sys.executable,
                    "-I",
                    "-m",
                    "oh_goon",
                    *argv[:handoff_position],
                    # Keep adapter metadata before the Run parser's optional '--'.
                    "--_resource-handoff",
                    handoff.header,
                    *argv[handoff_position:],
                ),
            )
            primary: BaseException | None = None
            try:
                result = run_command(
                    *command,
                    timeout=None,
                    env=child_environment,
                    stream_output=stream_output,
                    process_observer=signals.observe,
                    termination_requested=parent.pump,
                    parent_death_signal=True,
                    require_process_group_quiescence=True,
                    pass_fds=(handoff.descriptor, handoff.acknowledgement_read),
                    max_capture_bytes_per_stream=64 * 1024,
                    retain_output_tail=True,
                )
            except BaseException as error:
                primary = error
            finally:
                try:
                    observation = parent.finish()
                except Exception:
                    operational_errors.append("RUN_RESOURCE_FINISH_INCOMPLETE")
                    observation = RunResourceEnvelopeObservation(
                        fleet_run_id=scope.fleet_run_id,
                        unit_name=scope.unit_name,
                        invocation_id=parent.started.invocation_id if parent.started else None,
                        state="INCOMPLETE",
                        cleanup="INCOMPLETE",
                        reason_code="RUN_RESOURCE_FINISH_INCOMPLETE",
                    )
                operational_errors.extend(parent.cleanup_errors)
            if primary is not None:
                for error_code in operational_errors:
                    primary.add_note(error_code)
                raise primary
            try:
                context = RunResourceObservationContext.model_validate_json(
                    _read_control_json(handoff.terminal_path)
                )
                if parent.started is None or context.expected != parent.started:
                    raise FleetPlanInvalid(
                        "Terminal Run context differs from original native start"
                    )
                Journal.record_run_resource_observation_from_context(context, observation)
                persistence = "RECORDED"
            except (OSError, ValueError, FleetDomainError, sqlite3.DatabaseError):
                operational_errors.append("RUN_RESOURCE_OBSERVATION_PERSISTENCE_INCOMPLETE")
        operational_errors.extend(handoff.cleanup_errors)
    assert result is not None and observation is not None
    operational_errors.extend(signals.cleanup_errors)
    if signals.failure is not None:
        operational_errors.append(signals.failure)
    code = result.returncode if result.returncode >= 0 else 128 - result.returncode
    if args.as_json:
        try:
            if result.stdout_truncated:
                operational_errors.append("ORIGINAL_CLI_RESULT_TRUNCATED")
                raise ValueError("original CLI result exceeded its bound")
            receipt = json.loads(result.stdout)
            if not isinstance(receipt, dict):
                raise ValueError("original CLI result is not an object")
            if result.returncode == 0 and (
                receipt.get("schema") != _RUN_RESULT_SCHEMA
                or receipt.get("fleet_run_id") != scope.fleet_run_id
                or not isinstance(receipt.get("status"), str)
                or not isinstance(receipt.get("outcome"), str)
            ):
                raise ValueError("successful CLI result does not identify the original Run")
        except ValueError:
            operational_errors.append("ORIGINAL_CLI_RESULT_UNAVAILABLE")
            receipt = {
                "schema": _RUN_RESULT_SCHEMA,
                "status": "incomplete",
                "fleet_run_id": None,
                "requested_fleet_run_id": scope.fleet_run_id,
                "admission_status": "UNCONFIRMED",
            }
        receipt["resource_observation"] = observation.model_dump(mode="json")
        receipt["resource_observation_persistence"] = persistence
        if signals.requested is not None:
            receipt["invocation_signal"] = signals.requested
            receipt["resource_signal_status"] = signals.failure or (
                "DELIVERED" if signals.delivered else "UNCONFIRMED"
            )
        if operational_errors:
            receipt["resource_cleanup_errors"] = operational_errors
        print(json.dumps(receipt, sort_keys=True))
    else:
        print(f"Resource observation: {observation.state}; cleanup: {observation.cleanup}")
        if persistence != "RECORDED":
            print(f"Resource history: INCOMPLETE; requested Run ID: {scope.fleet_run_id}")
    if code == 0 and signals.requested is not None:
        return 128 + signals.requested
    if code == 0 and (observation.cleanup != "VERIFIED" or operational_errors):
        return 2
    return code
