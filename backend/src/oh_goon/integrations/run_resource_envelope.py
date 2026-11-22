"""Native resource scope for the original waited CLI, before Run preparation."""

from __future__ import annotations

import errno
import hashlib
import json
import os
import re
import shutil
import stat
from collections.abc import Callable, Mapping, Sequence
from contextlib import ExitStack
from dataclasses import dataclass, field
from pathlib import Path
from types import TracebackType
from typing import Literal, Self
from uuid import uuid4

from pydantic import ValidationError

from oh_goon.core.errors import FleetPlanInvalid, RunResourceScopeUnavailable
from oh_goon.core.models.fleet_plan_isolation import CicdResourcePolicy
from oh_goon.core.models.run_resources import (
    NativeRuntimeSliceEvidence,
    RunConcurrencyAdmission,
    RunConcurrencyPolicy,
    RunResourceEnvelopeEvidence,
    RunResourceEnvelopeObservation,
)
from oh_goon.core.process_birth import (
    ProcessBirthIdentity,
    observe_process_birth,
    observe_process_liveness,
)
from oh_goon.core.product_identity import stable_id
from oh_goon.engine.command import (
    CommandOutputLimitExceeded,
    CommandTimeoutExpired,
    run_command,
)
from oh_goon.integrations import _fleet_plan_cgroup_resources as native_cgroups

_SCHEMA = "oh-goon.run-resource-scope/v1"
_MANAGER_NAMES = frozenset(("XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS"))
_QUERY_PROPERTIES = (
    "Id",
    "LoadState",
    "ActiveState",
    "SubState",
    "InvocationID",
    "ControlGroup",
    "Transient",
    "CollectMode",
    "Description",
    "Result",
    "CPUUsageNSec",
    "MemoryPeak",
    "OOMKills",
    "OOMPolicy",
)
_UINT64_MAX = (1 << 64) - 1
_BUDGET_SCHEMA = "oh-goon.native-runtime-budget/v1"
_WORKER_SCOPE_SCHEMA = "oh-goon.deferred-worker-scope/v1"
_CLOSURE_ISSUANCE = object()
_CLAIM_ISSUANCE = object()


@dataclass(frozen=True, slots=True)
class ResolvedRunResourceScope:
    fleet_run_id: str
    resources: CicdResourcePolicy
    unit_name: str
    systemd_run: str
    systemd_run_sha256: str
    systemctl: str
    systemctl_sha256: str
    manager_environment: tuple[tuple[str, str], ...] = field(repr=False)
    command_prefix: tuple[str, ...]
    target_slice: str = native_cgroups.STANDARD_RESOURCE_SLICE

    @property
    def environment(self) -> dict[str, str]:
        return dict(self.manager_environment)

    def command(self, handoff_sha256: str, argv: Sequence[str]) -> tuple[str, ...]:
        if re.fullmatch(r"sha256:[0-9a-f]{64}", handoff_sha256) is None or not argv:
            raise FleetPlanInvalid("Whole-Run handoff digest and command are required")
        return (
            *self.command_prefix[:-1],
            f"--description=oh-goon-run-handoff-{handoff_sha256}",
            "--",
            *argv,
        )

    def to_payload(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "schema": _SCHEMA,
            "fleet_run_id": self.fleet_run_id,
            "resources": self.resources.model_dump(mode="json"),
            "unit_name": self.unit_name,
            "systemd_run": self.systemd_run,
            "systemd_run_sha256": self.systemd_run_sha256,
            "systemctl": self.systemctl,
            "systemctl_sha256": self.systemctl_sha256,
            "manager_environment": [list(pair) for pair in self.manager_environment],
            "command_prefix": list(self.command_prefix),
        }
        if self.target_slice != native_cgroups.STANDARD_RESOURCE_SLICE:
            payload["target_slice"] = self.target_slice
        return payload

    @classmethod
    def from_payload(cls, payload: object) -> ResolvedRunResourceScope:
        keys = {
            "schema",
            "fleet_run_id",
            "resources",
            "unit_name",
            "systemd_run",
            "systemd_run_sha256",
            "systemctl",
            "systemctl_sha256",
            "manager_environment",
            "command_prefix",
        }
        if (
            not isinstance(payload, dict)
            or set(payload) not in (keys, keys | {"target_slice"})
            or payload["schema"] != _SCHEMA
        ):
            raise FleetPlanInvalid("Whole-Run resource transport is invalid")
        text_names = keys - {"schema", "resources", "manager_environment", "command_prefix"}
        if any(
            not isinstance(payload[name], str)
            or not payload[name]
            or "\x00" in payload[name]
            or len(payload[name]) > 4096
            for name in text_names
        ):
            raise FleetPlanInvalid("Whole-Run resource transport text is invalid")
        environment = payload["manager_environment"]
        prefix = payload["command_prefix"]
        if (
            not isinstance(environment, list)
            or len(environment) > 2
            or any(
                not isinstance(pair, list)
                or len(pair) != 2
                or not isinstance(pair[0], str)
                or pair[0] not in _MANAGER_NAMES
                or not isinstance(pair[1], str)
                or "\x00" in pair[1]
                or len(pair[1]) > 4096
                for pair in environment
            )
            or len({pair[0] for pair in environment}) != len(environment)
            or not isinstance(prefix, list)
            or not 2 <= len(prefix) <= 32
            or any(
                not isinstance(item, str) or "\x00" in item or len(item) > 4096 for item in prefix
            )
        ):
            raise FleetPlanInvalid("Whole-Run resource transport authority is invalid")
        try:
            scope = cls(
                fleet_run_id=payload["fleet_run_id"],
                resources=CicdResourcePolicy.model_validate(payload["resources"], strict=True),
                unit_name=payload["unit_name"],
                systemd_run=payload["systemd_run"],
                systemd_run_sha256=payload["systemd_run_sha256"],
                systemctl=payload["systemctl"],
                systemctl_sha256=payload["systemctl_sha256"],
                manager_environment=tuple((pair[0], pair[1]) for pair in environment),
                command_prefix=tuple(prefix),
                target_slice=payload.get("target_slice", native_cgroups.STANDARD_RESOURCE_SLICE),
            )
        except ValidationError as exc:
            raise FleetPlanInvalid("Whole-Run resource transport policy is invalid") from exc
        if (
            len(scope.fleet_run_id) > 160
            or all(value is None for value in scope.resources.model_dump().values())
            or re.fullmatch(r"oh-goon-run-[0-9a-f]{32}\.scope", scope.unit_name) is None
            or any(
                re.fullmatch(r"[0-9a-f]{64}", value) is None
                for value in (scope.systemd_run_sha256, scope.systemctl_sha256)
            )
            or not Path(scope.systemd_run).is_absolute()
            or not Path(scope.systemctl).is_absolute()
            or prefix[0] != scope.systemd_run
            or prefix[-1] != "--"
            or f"--unit={scope.unit_name}" not in prefix
            or "--collect" in prefix
            or "--expand-environment=no" not in prefix
            or not isinstance(scope.target_slice, str)
            or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,248}\.slice", scope.target_slice) is None
            or f"--slice={scope.target_slice}" not in prefix
        ):
            raise FleetPlanInvalid("Whole-Run resource transport identity is invalid")
        return scope


def _executable_sha256(path: str) -> str:
    try:
        if not Path(path).is_absolute() or not Path(path).is_file() or not os.access(path, os.X_OK):
            raise FleetPlanInvalid("Whole-Run resources require selected native executable bytes")
        with Path(path).open("rb") as source:
            return hashlib.file_digest(source, "sha256").hexdigest()
    except OSError as exc:
        raise FleetPlanInvalid("Whole-Run native executable bytes are unavailable") from exc


def _require_tools_current(scope: ResolvedRunResourceScope) -> None:
    if (
        _executable_sha256(scope.systemd_run) != scope.systemd_run_sha256
        or _executable_sha256(scope.systemctl) != scope.systemctl_sha256
    ):
        raise FleetPlanInvalid("Whole-Run native executable bytes changed")


def prepare_run_resource_scope(
    fleet_run_id: str, resources: CicdResourcePolicy
) -> ResolvedRunResourceScope:
    """Resolve native tools once and refuse unsafe migration before application work."""
    if not fleet_run_id or len(fleet_run_id) > 160:
        raise FleetPlanInvalid("Whole-Run resources require an original Run identity")
    if all(value is None for value in resources.model_dump().values()):
        raise FleetPlanInvalid("Whole-Run resources require at least one selected cap")
    try:
        selected = tuple(shutil.which(name) for name in ("systemd-run", "systemctl"))
        if any(path is None for path in selected):
            raise FleetPlanInvalid("Whole-Run resources require native systemd-run and systemctl")
        systemd_run, systemctl = (str(Path(path).resolve(strict=True)) for path in selected if path)
        run_sha, query_sha = _executable_sha256(systemd_run), _executable_sha256(systemctl)
        environment = {
            name: os.environ[name] for name in sorted(_MANAGER_NAMES) if name in os.environ
        }
        scope = _scope_from_native_tools(
            fleet_run_id,
            resources,
            systemd_run=systemd_run,
            run_sha=run_sha,
            systemctl=systemctl,
            query_sha=query_sha,
            environment=environment,
            target_slice=native_cgroups.STANDARD_RESOURCE_SLICE,
        )
        _require_tools_current(scope)
    except (FleetPlanInvalid, OSError) as exc:
        raise RunResourceScopeUnavailable(
            "Whole-Run native resource context is unavailable"
        ) from exc
    return scope


def _scope_from_native_tools(
    fleet_run_id: str,
    resources: CicdResourcePolicy,
    *,
    systemd_run: str,
    run_sha: str,
    systemctl: str,
    query_sha: str,
    environment: Mapping[str, str],
    target_slice: str,
) -> ResolvedRunResourceScope:
    command = native_cgroups.standard_resource_scope_command(
        resources,
        systemd_run,
        query_executable=systemctl,
        manager_environment=environment,
        target_slice=target_slice,
    )
    unit = f"oh-goon-run-{uuid4().hex}.scope"
    memory_policy = ("--property=OOMPolicy=kill",) if resources.memory_mib is not None else ()
    prefix = (
        tuple(item for item in command[:-1] if item != "--collect")
        + memory_policy
        + (
            f"--unit={unit}",
            "--expand-environment=no",
            "--property=CollectMode=inactive",
            "--",
        )
    )
    return ResolvedRunResourceScope(
        fleet_run_id,
        resources,
        unit,
        systemd_run,
        run_sha,
        systemctl,
        query_sha,
        tuple(environment.items()),
        prefix,
        target_slice,
    )


def _query(scope: ResolvedRunResourceScope) -> dict[str, str]:
    _require_tools_current(scope)
    result = run_command(
        scope.systemctl,
        "--user",
        "--no-pager",
        "show",
        scope.unit_name,
        "--property=" + ",".join(_QUERY_PROPERTIES),
        env=scope.environment,
        timeout=5,
        max_capture_bytes_per_stream=8192,
        require_process_group_quiescence=True,
        parent_death_signal=True,
    )
    if result.returncode != 0:
        raise FleetPlanInvalid("Whole-Run native unit query is unavailable")
    properties: dict[str, str] = {}
    for line in result.stdout.splitlines():
        name, separator, value = line.partition("=")
        if not separator or name not in _QUERY_PROPERTIES or name in properties:
            raise FleetPlanInvalid("Whole-Run native unit query is ambiguous")
        properties[name] = value
    return properties


def _require_unit(scope: ResolvedRunResourceScope, properties: Mapping[str, str]) -> str:
    invocation = properties.get("InvocationID", "")
    if (
        properties.get("Id") != scope.unit_name
        or properties.get("LoadState") != "loaded"
        or re.fullmatch(r"[0-9a-f]{32}", invocation) is None
        or invocation == "0" * 32
        or properties.get("Transient") != "yes"
        or properties.get("CollectMode") != "inactive"
    ):
        raise FleetPlanInvalid("Whole-Run native unit identity is unavailable")
    return invocation


def _require_handoff(properties: Mapping[str, str], handoff_sha256: str) -> None:
    if (
        re.fullmatch(r"sha256:[0-9a-f]{64}", handoff_sha256) is None
        or properties.get("Description") != f"oh-goon-run-handoff-{handoff_sha256}"
    ):
        raise FleetPlanInvalid("Whole-Run native handoff digest changed")


def _native_limit(value: str) -> int | None:
    if value == "max":
        return None
    if not value.isascii() or not value.isdigit():
        raise FleetPlanInvalid("Whole-Run native limit observation is invalid")
    return int(value)


def _read_scope_evidence(
    scope: ResolvedRunResourceScope,
    properties: Mapping[str, str],
    *,
    handle: int,
    birth: ProcessBirthIdentity,
    identity: tuple[int, int],
    invocation: str,
    handoff_sha256: str,
    subject: Literal["cli", "worker"] = "cli",
) -> RunResourceEnvelopeEvidence:
    controls = {
        name: native_cgroups._read_bound_cgroup_control(handle, name, 128).strip()
        for name in ("cpu.max", "memory.max", "memory.swap.max", "pids.max")
    }
    oom_policy: Literal["kill"] | None = None
    memory_oom_group = None
    if scope.resources.memory_mib is not None:
        if properties.get("OOMPolicy") != "kill":
            raise FleetPlanInvalid("Whole-Run native memory failure policy is unsupported")
        if (
            native_cgroups._read_bound_cgroup_control(handle, "memory.oom.group", 128).strip()
            != "1"
        ):
            raise FleetPlanInvalid("Whole-Run native memory failure does not cover the group")
        oom_policy = "kill"
        memory_oom_group = True
    try:
        evidence = RunResourceEnvelopeEvidence(
            subject=subject,
            fleet_run_id=scope.fleet_run_id,
            unit_name=scope.unit_name,
            invocation_id=invocation,
            handoff_sha256=handoff_sha256,
            process_birth=birth,
            resources=scope.resources,
            cgroup_device=identity[0],
            cgroup_inode=identity[1],
            control_group=properties.get("ControlGroup") if subject == "worker" else None,
            cpu_max=controls["cpu.max"],
            memory_max_bytes=_native_limit(controls["memory.max"]),
            memory_swap_max_bytes=_native_limit(controls["memory.swap.max"]),
            pids_max=_native_limit(controls["pids.max"]),
            oom_policy=oom_policy,
            memory_oom_group=memory_oom_group,
        )
    except ValidationError as exc:
        raise FleetPlanInvalid(
            "Whole-Run native limits do not establish the selected caps"
        ) from exc
    return evidence


def verify_current_run_resource_scope(
    scope: ResolvedRunResourceScope,
    *,
    handoff_sha256: str,
    subject: Literal["cli", "worker"] = "cli",
) -> RunResourceEnvelopeEvidence:
    """Bind the actual CLI process and native controls before source capture/runtime."""
    birth = observe_process_birth()
    if birth is None:
        raise FleetPlanInvalid("Whole-Run original process birth is unavailable")
    properties = _query(scope)
    invocation = _require_unit(scope, properties)
    _require_handoff(properties, handoff_sha256)
    if properties.get("ActiveState") != "active" or properties.get("SubState") != "running":
        raise FleetPlanInvalid("Whole-Run native scope is not running")
    root = Path("/sys/fs/cgroup")
    mountinfo = native_cgroups._standard_mountinfo_snapshot()
    caller = native_cgroups._standard_cgroup_directory(root, mountinfo=mountinfo)
    target = native_cgroups._standard_cgroup_path(
        root, properties.get("ControlGroup", ""), mountinfo=mountinfo
    )
    if caller != target or target.name != scope.unit_name:
        raise FleetPlanInvalid("Whole-Run original process is outside its selected native unit")
    identity = native_cgroups._exact_cgroup_identity(target)
    with native_cgroups._bound_cgroup_directory(target, identity) as handle:
        native_cgroups._require_standard_domain(handle)
        native_cgroups._require_standard_caller_membership(handle)
        evidence = _read_scope_evidence(
            scope,
            properties,
            handle=handle,
            birth=birth,
            identity=identity,
            invocation=invocation,
            handoff_sha256=handoff_sha256,
            subject=subject,
        )
        if (
            observe_process_birth() != birth
            or native_cgroups._standard_cgroup_directory(root, mountinfo=mountinfo) != caller
            or native_cgroups._standard_mountinfo_snapshot() != mountinfo
        ):
            raise FleetPlanInvalid("Whole-Run original process context changed")
        native_cgroups._require_original_cgroup(target, identity)
        native_cgroups._require_standard_caller_membership(handle)
    return evidence


def verify_worker_run_resource_scope(
    scope: ResolvedRunResourceScope,
    expected: RunResourceEnvelopeEvidence,
    *,
    worker_birth: ProcessBirthIdentity,
) -> None:
    """Verify a claimed worker remains in the original admitted native envelope.

    The launch observation continues to describe the original CLI process. A
    fresh worker supplies its own positively observed birth; every native unit,
    cgroup and control fact must still equal the original admission.
    """
    if (
        type(worker_birth) is not ProcessBirthIdentity
        or (expected.subject == "cli" and worker_birth == expected.process_birth)
        or (expected.subject == "worker" and worker_birth != expected.process_birth)
        or scope.fleet_run_id != expected.fleet_run_id
        or scope.unit_name != expected.unit_name
        or scope.resources != expected.resources
    ):
        raise FleetPlanInvalid("Run worker resource scope or process binding is invalid")
    observed = verify_current_run_resource_scope(
        scope, handoff_sha256=expected.handoff_sha256, subject=expected.subject
    )
    if observed.process_birth != worker_birth:
        raise FleetPlanInvalid("Run worker process birth changed during resource verification")
    if observed.model_dump(exclude={"process_birth"}) != expected.model_dump(
        exclude={"process_birth"}
    ):
        raise FleetPlanInvalid("Run worker native resource envelope changed after admission")


@dataclass(slots=True)
class PinnedRunResourceScope:
    """Parent-owned readonly original kernel handles, acquired before ready ACK."""

    resolved: ResolvedRunResourceScope
    started: RunResourceEnvelopeEvidence
    _directory: Path = field(repr=False)
    _descriptor: int | None = field(repr=False)
    _events_descriptor: int | None = field(repr=False)
    _events_identity: tuple[int, int] = field(repr=False)
    _lifetime: ExitStack = field(repr=False)

    def __enter__(self) -> Self:
        self._metadata()
        return self

    def __exit__(
        self,
        _kind: type[BaseException] | None,
        _error: BaseException | None,
        _traceback: TracebackType | None,
    ) -> None:
        self.close()

    def fileno(self) -> int:
        self._metadata()
        assert self._descriptor is not None
        return self._descriptor

    def close(self) -> None:
        if self._descriptor is not None:
            self._descriptor = None
            self._events_descriptor = None
            self._lifetime.close()

    def _metadata(self) -> os.stat_result:
        if self._descriptor is None:
            raise FleetPlanInvalid("Whole-Run original cgroup handle is closed")
        observed = os.fstat(self._descriptor)
        if not stat.S_ISDIR(observed.st_mode) or (observed.st_dev, observed.st_ino) != (
            self.started.cgroup_device,
            self.started.cgroup_inode,
        ):
            raise FleetPlanInvalid("Whole-Run original cgroup handle identity changed")
        return observed

    def _require_binding(
        self, scope: ResolvedRunResourceScope, started: RunResourceEnvelopeEvidence
    ) -> None:
        if self.resolved != scope or self.started != started:
            raise FleetPlanInvalid("Whole-Run pinned scope authority changed")
        self._metadata()

    def _retired(self) -> bool:
        self._metadata()
        if self._events_descriptor is None:
            raise FleetPlanInvalid("Whole-Run original event control handle is closed")
        observed = os.fstat(self._events_descriptor)
        if not stat.S_ISREG(observed.st_mode) or (observed.st_dev, observed.st_ino) != (
            self._events_identity
        ):
            raise FleetPlanInvalid("Whole-Run original event control identity changed")
        try:
            os.lseek(self._events_descriptor, 0, os.SEEK_SET)
            os.read(self._events_descriptor, 1)
        except OSError as error:
            # Kernfs retains a directory link count after cgroup retirement. Its
            # already-open mandatory control instead reports the original node's
            # native deactivation. Other read errors do not establish retirement.
            if error.errno == errno.ENODEV:
                return True
            raise
        return False


def pin_started_run_resource_scope(
    scope: ResolvedRunResourceScope,
    started: RunResourceEnvelopeEvidence,
    *,
    expected_birth: ProcessBirthIdentity,
    handoff_sha256: str,
) -> PinnedRunResourceScope:
    """Verify the waiting child and retain its original kernel object before ACK."""
    if (
        started.fleet_run_id != scope.fleet_run_id
        or started.unit_name != scope.unit_name
        or started.resources != scope.resources
        or started.process_birth != expected_birth
        or started.handoff_sha256 != handoff_sha256
        or observe_process_birth(expected_birth.pid) != expected_birth
    ):
        raise FleetPlanInvalid("Whole-Run waiting process authority changed")
    properties = _query(scope)
    if _require_unit(scope, properties) != started.invocation_id:
        raise FleetPlanInvalid("Whole-Run waiting native invocation changed")
    _require_handoff(properties, handoff_sha256)
    if properties.get("ActiveState") != "active" or properties.get("SubState") != "running":
        raise FleetPlanInvalid("Whole-Run waiting native scope is not running")
    mountinfo = native_cgroups._standard_mountinfo_snapshot()
    target = native_cgroups._standard_cgroup_path(
        Path("/sys/fs/cgroup"), properties.get("ControlGroup", ""), mountinfo=mountinfo
    )
    if target.name != scope.unit_name:
        raise FleetPlanInvalid("Whole-Run waiting cgroup mapping changed")
    identity = (started.cgroup_device, started.cgroup_inode)
    with ExitStack() as lifetime:
        handle = lifetime.enter_context(native_cgroups._bound_cgroup_directory(target, identity))
        native_cgroups._require_standard_domain(handle)
        if (
            str(expected_birth.pid)
            not in native_cgroups._read_bound_cgroup_control(
                handle, "cgroup.procs", 65536
            ).splitlines()
        ):
            raise FleetPlanInvalid("Whole-Run waiting cgroup does not contain the original PID")
        current = _read_scope_evidence(
            scope,
            properties,
            handle=handle,
            birth=expected_birth,
            identity=identity,
            invocation=started.invocation_id,
            handoff_sha256=handoff_sha256,
            subject=started.subject,
        )
        if current != started or observe_process_birth(expected_birth.pid) != expected_birth:
            raise FleetPlanInvalid("Whole-Run waiting process observations changed")
        if native_cgroups._standard_mountinfo_snapshot() != mountinfo:
            raise FleetPlanInvalid("Whole-Run waiting mount view changed")
        native_cgroups._require_original_cgroup(target, identity)
        events = os.open(
            "cgroup.events",
            os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK,
            dir_fd=handle,
        )
        lifetime.callback(os.close, events)
        event_metadata = os.fstat(events)
        if (
            not stat.S_ISREG(event_metadata.st_mode)
            or event_metadata.st_dev != started.cgroup_device
            or not os.read(events, 1)
        ):
            raise FleetPlanInvalid("Whole-Run waiting event control is unavailable")
        return PinnedRunResourceScope(
            scope,
            started,
            target,
            handle,
            events,
            (event_metadata.st_dev, event_metadata.st_ino),
            lifetime.pop_all(),
        )


def _native_unit_retired(scope: ResolvedRunResourceScope, properties: Mapping[str, str]) -> bool:
    return (
        properties.get("Id") == scope.unit_name
        and properties.get("LoadState") == "not-found"
        and properties.get("ActiveState") == "inactive"
        and properties.get("InvocationID") == ""
        and properties.get("ControlGroup") == ""
        and properties.get("Transient") == "no"
    )


def _metric(properties: Mapping[str, str], name: str) -> int | None:
    raw = properties.get(name, "")
    if not raw.isascii() or not raw.isdigit() or int(raw) >= _UINT64_MAX:
        return None
    return int(raw)


def finish_run_resource_scope(
    scope: ResolvedRunResourceScope,
    started: RunResourceEnvelopeEvidence | None,
    *,
    expected_birth: ProcessBirthIdentity | None,
    handoff_sha256: str,
    pinned: PinnedRunResourceScope | None = None,
    before_cleanup: Callable[[RunResourceEnvelopeObservation], None] | None = None,
) -> RunResourceEnvelopeObservation:
    """Observe terminal native truth and converge cleanup without changing Run results."""
    invocation: str | None = started.invocation_id if started is not None else None

    def incomplete(reason: str) -> RunResourceEnvelopeObservation:
        return RunResourceEnvelopeObservation(
            fleet_run_id=scope.fleet_run_id,
            unit_name=scope.unit_name,
            invocation_id=invocation,
            state="INCOMPLETE",
            cleanup="INCOMPLETE",
            reason_code=reason,
        )

    if started is None or expected_birth is None:
        return incomplete("STARTED_AUTHORITY_UNAVAILABLE")
    if (
        started.process_birth != expected_birth
        or started.fleet_run_id != scope.fleet_run_id
        or started.unit_name != scope.unit_name
        or started.resources != scope.resources
        or started.handoff_sha256 != handoff_sha256
    ):
        return incomplete("STARTED_AUTHORITY_UNAVAILABLE")
    if pinned is not None:
        try:
            pinned._require_binding(scope, started)
        except (OSError, FleetPlanInvalid):
            return incomplete("PINNED_AUTHORITY_UNAVAILABLE")

    def retired(properties: Mapping[str, str]) -> RunResourceEnvelopeObservation | None:
        if pinned is not None and _native_unit_retired(scope, properties) and pinned._retired():
            return RunResourceEnvelopeObservation(
                fleet_run_id=scope.fleet_run_id,
                unit_name=scope.unit_name,
                invocation_id=started.invocation_id,
                state="INCOMPLETE",
                cleanup="VERIFIED",
                reason_code="NATIVE_UNIT_RETIRED_ACCOUNTING_UNAVAILABLE",
            )
        return None

    def publish(observation: RunResourceEnvelopeObservation) -> bool:
        if before_cleanup is None:
            return True
        try:
            before_cleanup(observation.model_copy(update={"cleanup": "INCOMPLETE"}))
        except Exception:
            return False
        return True

    observation: RunResourceEnvelopeObservation | None = None
    try:
        properties = _query(scope)
        retired_observation = retired(properties)
        if retired_observation is not None:
            return retired_observation
        observed_invocation = _require_unit(scope, properties)
        _require_handoff(properties, handoff_sha256)
        if invocation is not None and observed_invocation != invocation:
            return incomplete("NATIVE_INVOCATION_CHANGED")
        invocation = observed_invocation
        active_state = properties.get("ActiveState")
        if active_state not in ("inactive", "failed"):
            root = Path("/sys/fs/cgroup")
            target = native_cgroups._standard_cgroup_path(root, properties.get("ControlGroup", ""))
            native_cgroups._require_original_cgroup(
                target, (started.cgroup_device, started.cgroup_inode)
            )
            observation = RunResourceEnvelopeObservation(
                fleet_run_id=scope.fleet_run_id,
                unit_name=scope.unit_name,
                invocation_id=invocation,
                state="INCOMPLETE",
                active_state=active_state,
                cleanup="INCOMPLETE",
                reason_code="NATIVE_SCOPE_NOT_TERMINAL",
            )
            if not publish(observation):
                return observation.model_copy(
                    update={"reason_code": "NATIVE_EVIDENCE_PUBLICATION_FAILED"}
                )
            stopped = run_command(
                scope.systemctl,
                "--user",
                "--no-pager",
                "stop",
                scope.unit_name,
                env=scope.environment,
                timeout=10,
                max_capture_bytes_per_stream=4096,
                require_process_group_quiescence=True,
                parent_death_signal=True,
            )
            if stopped.returncode != 0:
                return observation.model_copy(update={"reason_code": "NATIVE_CLEANUP_FAILED"})
            properties = _query(scope)
            retired_observation = retired(properties)
            if retired_observation is not None:
                return retired_observation
            if _require_unit(scope, properties) != invocation:
                return incomplete("NATIVE_FINAL_OBSERVATION_UNAVAILABLE")
            _require_handoff(properties, handoff_sha256)
            active_state = properties.get("ActiveState")
        if active_state not in ("inactive", "failed"):
            return incomplete("NATIVE_SCOPE_NOT_TERMINAL")
        cpu_nsec, peak, oom = (
            _metric(properties, name) for name in ("CPUUsageNSec", "MemoryPeak", "OOMKills")
        )
        result = properties.get("Result")
        if result is not None and re.fullmatch(r"[a-z0-9_-]{1,64}", result) is None:
            result = None
        observation = RunResourceEnvelopeObservation(
            fleet_run_id=scope.fleet_run_id,
            unit_name=scope.unit_name,
            invocation_id=invocation,
            state="TERMINAL",
            active_state=active_state,
            result=result,
            cpu_usage_usec=cpu_nsec // 1000 if cpu_nsec is not None else None,
            memory_peak_bytes=peak,
            oom_kill_count=oom,
            cleanup="VERIFIED",
            reason_code="NATIVE_OPTIONAL_METRICS_UNAVAILABLE",
        )
        if active_state == "failed":
            # Capture the failed invocation's observation before releasing its native state.
            current = _query(scope)
            if _require_unit(scope, current) != invocation:
                return observation.model_copy(
                    update={"cleanup": "INCOMPLETE", "reason_code": "NATIVE_INVOCATION_CHANGED"}
                )
            _require_handoff(current, handoff_sha256)
            if not publish(observation):
                return observation.model_copy(
                    update={
                        "cleanup": "INCOMPLETE",
                        "reason_code": "NATIVE_EVIDENCE_PUBLICATION_FAILED",
                    }
                )
            reset = run_command(
                scope.systemctl,
                "--user",
                "--no-pager",
                "reset-failed",
                scope.unit_name,
                env=scope.environment,
                timeout=5,
                max_capture_bytes_per_stream=4096,
                require_process_group_quiescence=True,
                parent_death_signal=True,
            )
            if reset.returncode != 0:
                return observation.model_copy(
                    update={"cleanup": "INCOMPLETE", "reason_code": "NATIVE_CLEANUP_FAILED"}
                )
        return observation
    except (
        OSError,
        ValueError,
        FleetPlanInvalid,
        CommandTimeoutExpired,
        CommandOutputLimitExceeded,
    ):
        if observation is not None:
            return observation.model_copy(
                update={"cleanup": "INCOMPLETE", "reason_code": "NATIVE_CLEANUP_UNAVAILABLE"}
            )
        return incomplete("NATIVE_OBSERVATION_UNAVAILABLE")


@dataclass(frozen=True, slots=True)
class ResolvedNativeRuntimeBudget:
    """One captured native aggregate and tool selection; not executable work."""

    policy: RunConcurrencyPolicy
    aggregate: NativeRuntimeSliceEvidence
    systemd_run: str
    systemd_run_sha256: str
    systemctl: str
    systemctl_sha256: str
    manager_environment: tuple[tuple[str, str], ...] = field(repr=False)
    journalctl: str | None = None
    journalctl_sha256: str | None = None

    @property
    def environment(self) -> dict[str, str]:
        return dict(self.manager_environment)

    @property
    def identity(self) -> str:
        return stable_id(
            "run-concurrency-budget",
            {
                "policy": self.policy.model_dump(mode="json"),
                "aggregate": self.aggregate.model_dump(mode="json"),
            },
        )

    def to_payload(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "schema": _BUDGET_SCHEMA,
            "policy": self.policy.model_dump(mode="json"),
            "aggregate": self.aggregate.model_dump(mode="json"),
            "systemd_run": self.systemd_run,
            "systemd_run_sha256": self.systemd_run_sha256,
            "systemctl": self.systemctl,
            "systemctl_sha256": self.systemctl_sha256,
            "manager_environment": [list(pair) for pair in self.manager_environment],
        }
        if self.journalctl is not None:
            payload["journalctl"] = self.journalctl
            payload["journalctl_sha256"] = self.journalctl_sha256
        return payload

    @classmethod
    def from_payload(cls, value: object) -> ResolvedNativeRuntimeBudget:
        keys = {
            "schema",
            "policy",
            "aggregate",
            "systemd_run",
            "systemd_run_sha256",
            "systemctl",
            "systemctl_sha256",
            "manager_environment",
        }
        if (
            not isinstance(value, dict)
            or set(value) not in (keys, keys | {"journalctl", "journalctl_sha256"})
            or value["schema"] != _BUDGET_SCHEMA
        ):
            raise FleetPlanInvalid("Native runtime budget transport is invalid")
        environment = value["manager_environment"]
        if (
            not isinstance(environment, list)
            or len(environment) > 2
            or any(
                not isinstance(pair, list)
                or len(pair) != 2
                or not isinstance(pair[0], str)
                or pair[0] not in _MANAGER_NAMES
                or not isinstance(pair[1], str)
                or "\x00" in pair[1]
                or len(pair[1]) > 4096
                for pair in environment
            )
            or len({pair[0] for pair in environment}) != len(environment)
        ):
            raise FleetPlanInvalid("Native runtime manager transport is invalid")
        for name in ("systemd_run", "systemctl"):
            if not isinstance(value[name], str) or not Path(value[name]).is_absolute():
                raise FleetPlanInvalid("Native runtime executable selection is invalid")
        for name in ("systemd_run_sha256", "systemctl_sha256"):
            if (
                not isinstance(value[name], str)
                or re.fullmatch(r"[0-9a-f]{64}", value[name]) is None
            ):
                raise FleetPlanInvalid("Native runtime executable identity is invalid")
        journalctl, journal_sha = value.get("journalctl"), value.get("journalctl_sha256")
        if journalctl is not None and (
            not isinstance(journalctl, str)
            or not Path(journalctl).is_absolute()
            or not isinstance(journal_sha, str)
            or re.fullmatch(r"[0-9a-f]{64}", journal_sha) is None
        ):
            raise FleetPlanInvalid("Native journal executable selection is invalid")
        try:
            policy = RunConcurrencyPolicy.model_validate(value["policy"], strict=True)
            aggregate = NativeRuntimeSliceEvidence.model_validate(value["aggregate"])
        except ValidationError as error:
            raise FleetPlanInvalid("Native runtime budget facts are invalid") from error
        if (
            policy.aggregate_slice != aggregate.unit_name
            or policy.aggregate_resources != aggregate.resources
        ):
            raise FleetPlanInvalid("Native runtime budget and aggregate differ")
        return cls(
            policy,
            aggregate,
            value["systemd_run"],
            value["systemd_run_sha256"],
            value["systemctl"],
            value["systemctl_sha256"],
            tuple(tuple(pair) for pair in environment),
            journalctl,
            journal_sha,
        )


def _runtime_tools_current(budget: ResolvedNativeRuntimeBudget) -> None:
    if (
        _executable_sha256(budget.systemd_run) != budget.systemd_run_sha256
        or _executable_sha256(budget.systemctl) != budget.systemctl_sha256
    ):
        raise FleetPlanInvalid("Native runtime resource tools changed")
    if (
        budget.journalctl is not None
        and _executable_sha256(budget.journalctl) != budget.journalctl_sha256
    ):
        raise FleetPlanInvalid("Native resource journal tool changed")


def _finite_native_limit(value: str) -> int:
    result = _native_limit(value)
    if result is None:
        raise FleetPlanInvalid("Native aggregate requires finite resource ceilings")
    return result


def _native_manager_details(
    systemctl: str, environment: Mapping[str, str], journalctl: str | None
) -> tuple[ProcessBirthIdentity, str, str, str, int]:
    """Bind native manager incarnation and its advertised executable artifact.

    The artifact hash is not loaded-image attestation. Real manager PID/birth,
    reported version and journald's trusted origin fields identify the native
    authority; nondumpable manager images need no privileged procfs access.
    """
    uid = os.getuid()
    main = run_command(
        systemctl,
        "--no-pager",
        "show",
        f"user@{uid}.service",
        "--property=MainPID",
        "--value",
        env=dict(environment),
        timeout=5,
        max_capture_bytes_per_stream=1024,
        require_process_group_quiescence=True,
        parent_death_signal=True,
    )
    pid_text = main.stdout.strip()
    if (
        main.returncode != 0
        or not pid_text.isascii()
        or not pid_text.isdigit()
        or int(pid_text) < 1
    ):
        raise FleetPlanInvalid("Original native user manager process is unavailable")
    pid = int(pid_text)
    birth = observe_process_birth(pid)
    if birth is None or Path(f"/proc/{pid}").stat().st_uid != uid:
        raise FleetPlanInvalid("Original native user manager birth is unavailable")
    if journalctl is None:
        raise FleetPlanInvalid("Original native user manager origin reader is unavailable")
    expected = {
        "_PID": str(pid),
        "_UID": str(uid),
        "_COMM": "systemd",
        "_SYSTEMD_UNIT": f"user@{uid}.service",
        "_SYSTEMD_USER_UNIT": "init.scope",
        "_BOOT_ID": birth.boot_id.replace("-", ""),
    }
    journal = run_command(
        journalctl,
        "--user",
        "--no-pager",
        "--quiet",
        "--all",
        "--output=json",
        "--lines=2",
        "--output-fields=" + ",".join((*expected, "_EXE")),
        *(f"{name}={value}" for name, value in expected.items()),
        env=dict(environment),
        timeout=5,
        max_capture_bytes_per_stream=32768,
        require_process_group_quiescence=True,
        parent_death_signal=True,
    )
    if journal.returncode != 0 or journal.stdout_truncated or journal.stderr:
        raise FleetPlanInvalid("Original native user manager trusted origin is unavailable")
    records = journal.stdout.splitlines()
    if not 1 <= len(records) <= 2:
        raise FleetPlanInvalid("Original native user manager trusted origin is missing")
    paths = set()
    for record in records:
        try:
            raw = json.loads(record, object_pairs_hook=_unique_journal_fields)
        except (ValueError, UnicodeError, RecursionError) as error:
            raise FleetPlanInvalid("Original native user manager origin is malformed") from error
        if not isinstance(raw, dict) or any(
            raw.get(name) != value for name, value in expected.items()
        ):
            raise FleetPlanInvalid("Original native user manager origin belongs to another process")
        executable = raw.get("_EXE")
        if (
            not isinstance(executable, str)
            or not executable.isprintable()
            or len(executable) > 4096
            or not Path(executable).is_absolute()
            or executable.endswith(" (deleted)")
        ):
            raise FleetPlanInvalid(
                "Original native user manager advertised artifact is unavailable"
            )
        paths.add(executable)
    if len(paths) != 1:
        raise FleetPlanInvalid("Original native user manager advertised artifact changed")
    executable = paths.pop()
    executable_sha = _executable_sha256(executable)
    version = run_command(
        systemctl,
        "--user",
        "--no-pager",
        "show",
        "--property=Version",
        "--value",
        env=dict(environment),
        timeout=5,
        max_capture_bytes_per_stream=1024,
        require_process_group_quiescence=True,
        parent_death_signal=True,
    )
    text = version.stdout.strip()
    if (
        version.returncode != 0
        or not text
        or len(text) > 128
        or not text.isprintable()
        or observe_process_birth(pid) != birth
    ):
        raise FleetPlanInvalid("Original native user manager implementation changed")
    return birth, executable, executable_sha, text, uid


def _slice_properties(unit: str, systemctl: str, environment: Mapping[str, str]) -> dict[str, str]:
    names = ("Id", "LoadState", "ActiveState", "SubState", "InvocationID", "ControlGroup")
    result = run_command(
        systemctl,
        "--user",
        "--no-pager",
        "show",
        unit,
        "--property=" + ",".join(names),
        env=dict(environment),
        timeout=5,
        max_capture_bytes_per_stream=8192,
        require_process_group_quiescence=True,
        parent_death_signal=True,
    )
    if result.returncode != 0:
        raise FleetPlanInvalid("Selected native aggregate slice is unavailable")
    properties: dict[str, str] = {}
    for line in result.stdout.splitlines():
        name, separator, value = line.partition("=")
        if not separator or name not in names or name in properties:
            raise FleetPlanInvalid("Native aggregate slice observation is ambiguous")
        properties[name] = value
    if (
        properties.get("Id") != unit
        or properties.get("LoadState") != "loaded"
        or properties.get("ActiveState") != "active"
        or properties.get("SubState") != "active"
        or re.fullmatch(r"[0-9a-f]{32}", properties.get("InvocationID", "")) is None
        or properties.get("InvocationID") == "0" * 32
    ):
        raise FleetPlanInvalid("Native aggregate slice identity is unavailable")
    return properties


def _observe_runtime_slice(
    policy: RunConcurrencyPolicy,
    *,
    systemctl: str,
    environment: Mapping[str, str],
    expected: NativeRuntimeSliceEvidence | None = None,
    journalctl: str | None = None,
    controller: bool,
) -> NativeRuntimeSliceEvidence:
    properties = _slice_properties(policy.aggregate_slice, systemctl, environment)
    manager = _native_manager_details(systemctl, environment, journalctl)
    root = Path("/sys/fs/cgroup")
    mountinfo = native_cgroups._standard_mountinfo_snapshot()
    target = native_cgroups._standard_cgroup_path(
        root, properties.get("ControlGroup", ""), mountinfo=mountinfo
    )
    if target.name != policy.aggregate_slice:
        raise FleetPlanInvalid("Selected native aggregate slice mapping changed")
    identity = native_cgroups._exact_cgroup_identity(target)
    if controller:
        birth = observe_process_birth()
        if birth is None or (expected is not None and birth != expected.controller_birth):
            raise FleetPlanInvalid("Native runtime controller process changed")
        caller = native_cgroups._standard_cgroup_directory(root, mountinfo=mountinfo)
        if caller == target or not caller.is_relative_to(target):
            raise FleetPlanInvalid(
                "Controller requires its own native scope inside aggregate slice"
            )
        caller_identity = native_cgroups._exact_cgroup_identity(caller)
        native_cgroups._require_standard_scope_inheritance(
            systemctl, environment, target_slice=policy.aggregate_slice
        )
    elif expected is not None:
        birth = expected.controller_birth
        caller_identity = (expected.controller_cgroup_device, expected.controller_cgroup_inode)
    else:
        raise FleetPlanInvalid("Native runtime controller provenance is missing")
    with native_cgroups._bound_cgroup_directory(target, identity) as handle:
        native_cgroups._require_standard_domain(handle)
        controls = {
            name: native_cgroups._read_bound_cgroup_control(handle, name, 128).strip()
            for name in ("cpu.max", "memory.max", "memory.swap.max", "pids.max")
        }
        if controls["memory.swap.max"] != "0":
            raise FleetPlanInvalid("Native aggregate requires disabled memory swap")
        try:
            evidence = NativeRuntimeSliceEvidence(
                unit_name=policy.aggregate_slice,
                invocation_id=properties["InvocationID"],
                controller_birth=birth,
                controller_cgroup_device=caller_identity[0],
                controller_cgroup_inode=caller_identity[1],
                cgroup_device=identity[0],
                cgroup_inode=identity[1],
                resources=policy.aggregate_resources,
                cpu_max=controls["cpu.max"],
                memory_max_bytes=_finite_native_limit(controls["memory.max"]),
                memory_swap_max_bytes=0,
                pids_max=_finite_native_limit(controls["pids.max"]),
                manager_birth=manager[0],
                manager_executable=manager[1],
                manager_executable_artifact_sha256=manager[2],
                manager_version=manager[3],
                manager_uid=manager[4],
            )
        except ValidationError as error:
            raise FleetPlanInvalid(
                "Native aggregate does not enforce selected full finite ceilings"
            ) from error
        if expected is not None and evidence != expected:
            raise FleetPlanInvalid("Native aggregate identity or controls changed")
        native_cgroups._require_original_cgroup(target, identity)
        if native_cgroups._standard_mountinfo_snapshot() != mountinfo:
            raise FleetPlanInvalid("Native aggregate mount view changed")
    return evidence


def capture_native_runtime_budget(policy: RunConcurrencyPolicy) -> ResolvedNativeRuntimeBudget:
    """Resolve one explicit ordinary aggregate before application stateful work."""
    policy = RunConcurrencyPolicy.model_validate(policy.model_dump(mode="json"), strict=True)
    tools = tuple(shutil.which(name) for name in ("systemd-run", "systemctl", "journalctl"))
    if any(value is None for value in tools):
        raise RunResourceScopeUnavailable("Native two-Run resources require systemd user tools")
    systemd_run, systemctl, journalctl = (
        str(Path(value).resolve(strict=True)) for value in tools if value
    )
    environment = {name: os.environ[name] for name in sorted(_MANAGER_NAMES) if name in os.environ}
    aggregate = _observe_runtime_slice(
        policy, systemctl=systemctl, environment=environment, journalctl=journalctl, controller=True
    )
    budget = ResolvedNativeRuntimeBudget(
        policy,
        aggregate,
        systemd_run,
        _executable_sha256(systemd_run),
        systemctl,
        _executable_sha256(systemctl),
        tuple(environment.items()),
        journalctl,
        _executable_sha256(journalctl),
    )
    require_native_runtime_budget_current(budget)
    return budget


def require_native_runtime_budget_current(
    budget: ResolvedNativeRuntimeBudget, *, controller: bool = True
) -> None:
    _runtime_tools_current(budget)
    _observe_runtime_slice(
        budget.policy,
        systemctl=budget.systemctl,
        environment=budget.environment,
        expected=budget.aggregate,
        journalctl=budget.journalctl,
        controller=controller,
    )


def _scope_intent_identity(
    scope: ResolvedRunResourceScope, budget: ResolvedNativeRuntimeBudget
) -> str:
    payload = json.dumps(
        {"scope": scope.to_payload(), "budget": budget.to_payload()},
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class DeferredWorkerResourceScope:
    """Protected resolved scope intent; actual worker observations are separate."""

    scope: ResolvedRunResourceScope
    admission: RunConcurrencyAdmission
    budget: ResolvedNativeRuntimeBudget

    @classmethod
    def create(
        cls, scope: ResolvedRunResourceScope, budget: ResolvedNativeRuntimeBudget
    ) -> DeferredWorkerResourceScope:
        """Bind passive resolved intent; construction never establishes native custody."""
        return cls(
            scope,
            RunConcurrencyAdmission(
                policy=budget.policy,
                aggregate=budget.aggregate,
                fleet_run_id=scope.fleet_run_id,
                worker_unit_name=scope.unit_name,
                worker_scope_identity=_scope_intent_identity(scope, budget),
            ),
            budget,
        )

    def __post_init__(self) -> None:
        if (
            self.scope.fleet_run_id != self.admission.fleet_run_id
            or self.scope.unit_name != self.admission.worker_unit_name
            or _scope_intent_identity(self.scope, self.budget)
            != self.admission.worker_scope_identity
            or self.admission.policy != self.budget.policy
            or self.admission.aggregate != self.budget.aggregate
            or self.scope.resources != self.budget.policy.worker_resources
            or self.scope.target_slice != self.budget.policy.aggregate_slice
            or self.scope.systemd_run != self.budget.systemd_run
            or self.scope.systemd_run_sha256 != self.budget.systemd_run_sha256
            or self.scope.systemctl != self.budget.systemctl
            or self.scope.systemctl_sha256 != self.budget.systemctl_sha256
            or self.scope.manager_environment != self.budget.manager_environment
        ):
            raise FleetPlanInvalid("Deferred worker scope differs from admitted native budget")

    def to_payload(self) -> dict[str, object]:
        return {
            "schema": _WORKER_SCOPE_SCHEMA,
            "scope": self.scope.to_payload(),
            "admission": self.admission.model_dump(mode="json", exclude_computed_fields=True),
            "budget": self.budget.to_payload(),
        }

    @classmethod
    def from_payload(cls, value: object) -> DeferredWorkerResourceScope:
        if (
            not isinstance(value, dict)
            or set(value) != {"schema", "scope", "admission", "budget"}
            or value["schema"] != _WORKER_SCOPE_SCHEMA
        ):
            raise FleetPlanInvalid("Deferred worker scope transport is invalid")
        try:
            return cls(
                ResolvedRunResourceScope.from_payload(value["scope"]),
                RunConcurrencyAdmission.model_validate(value["admission"]),
                ResolvedNativeRuntimeBudget.from_payload(value["budget"]),
            )
        except ValidationError as error:
            raise FleetPlanInvalid("Deferred worker scope admission is invalid") from error


def prepare_worker_resource_scope(
    fleet_run_id: str, budget: ResolvedNativeRuntimeBudget
) -> DeferredWorkerResourceScope:
    require_native_runtime_budget_current(budget)
    scope = _scope_from_native_tools(
        fleet_run_id,
        budget.policy.worker_resources,
        systemd_run=budget.systemd_run,
        run_sha=budget.systemd_run_sha256,
        systemctl=budget.systemctl,
        query_sha=budget.systemctl_sha256,
        environment=budget.environment,
        target_slice=budget.policy.aggregate_slice,
    )
    return DeferredWorkerResourceScope.create(scope, budget)


def observe_worker_resource_scope(
    deferred: DeferredWorkerResourceScope, *, handoff_sha256: str
) -> RunResourceEnvelopeEvidence:
    require_native_runtime_budget_current(deferred.budget, controller=False)
    evidence = verify_current_run_resource_scope(
        deferred.scope, handoff_sha256=handoff_sha256, subject="worker"
    )
    root = Path("/sys/fs/cgroup")
    mountinfo = native_cgroups._standard_mountinfo_snapshot()
    properties = _slice_properties(
        deferred.budget.policy.aggregate_slice,
        deferred.budget.systemctl,
        deferred.budget.environment,
    )
    aggregate = native_cgroups._standard_cgroup_path(
        root, properties.get("ControlGroup", ""), mountinfo=mountinfo
    )
    worker = native_cgroups._standard_cgroup_directory(root, mountinfo=mountinfo)
    if worker.parent != aggregate:
        raise FleetPlanInvalid("Original worker does not belong to the selected aggregate slice")
    return evidence


@dataclass(frozen=True, slots=True)
class VerifiedWorkerResourceClaim:
    """In-process native claim authority; serialized observations cannot issue it."""

    admission: RunConcurrencyAdmission
    envelope: RunResourceEnvelopeEvidence
    _issuance: object = field(repr=False, compare=False)

    def require_matches(
        self, admission: RunConcurrencyAdmission, envelope: RunResourceEnvelopeEvidence
    ) -> None:
        if (
            self._issuance is not _CLAIM_ISSUANCE
            or self.admission != admission
            or self.envelope != envelope
            or envelope.subject != "worker"
            or envelope.fleet_run_id != admission.fleet_run_id
            or envelope.unit_name != admission.worker_unit_name
            or observe_process_birth() != envelope.process_birth
        ):
            raise FleetPlanInvalid("Native worker resource claim does not match current process")


def verify_worker_resource_claim(
    deferred: DeferredWorkerResourceScope, envelope: RunResourceEnvelopeEvidence
) -> VerifiedWorkerResourceClaim:
    """Recheck receiving native authority after ready ACK and before database claim."""
    current = observe_worker_resource_scope(deferred, handoff_sha256=envelope.handoff_sha256)
    if current != envelope:
        raise FleetPlanInvalid("Native worker resources changed before claim")
    return VerifiedWorkerResourceClaim(deferred.admission, envelope, _CLAIM_ISSUANCE)


@dataclass(frozen=True, slots=True)
class VerifiedWorkerResourceClosure:
    """Opaque original-native closure witness; public models cannot issue it."""

    admission: RunConcurrencyAdmission
    envelope: RunResourceEnvelopeEvidence | None
    observation: RunResourceEnvelopeObservation
    _issuance: object = field(repr=False, compare=False)

    def require_matches(
        self, admission: RunConcurrencyAdmission, envelope: RunResourceEnvelopeEvidence | None
    ) -> None:
        if (
            self._issuance is not _CLOSURE_ISSUANCE
            or self.admission != admission
            or self.envelope != envelope
            or self.observation.cleanup != "VERIFIED"
            or self.observation.fleet_run_id != admission.fleet_run_id
            or self.observation.unit_name != admission.worker_unit_name
            or (envelope is not None and self.observation.invocation_id != envelope.invocation_id)
        ):
            raise FleetPlanInvalid("Native worker closure does not match original reservation")


def finish_worker_resource_scope(
    deferred: DeferredWorkerResourceScope,
    envelope: RunResourceEnvelopeEvidence | None,
    *,
    pinned: PinnedRunResourceScope | None,
) -> VerifiedWorkerResourceClosure:
    """Close only the original observed native scope; preserve its kernel handles."""
    if envelope is None or envelope.subject != "worker" or pinned is None:
        raise FleetPlanInvalid("Original worker native resource closure is unproven")
    if (
        envelope.fleet_run_id != deferred.admission.fleet_run_id
        or envelope.unit_name != deferred.admission.worker_unit_name
        or envelope.resources != deferred.budget.policy.worker_resources
    ):
        raise FleetPlanInvalid("Original worker scope differs from its admission")
    pinned._require_binding(deferred.scope, envelope)
    observation = finish_run_resource_scope(
        deferred.scope,
        envelope,
        expected_birth=envelope.process_birth,
        handoff_sha256=envelope.handoff_sha256,
        pinned=pinned,
    )
    if observation.cleanup != "VERIFIED":
        raise FleetPlanInvalid("Original worker native resource closure remains incomplete")
    _require_worker_not_executing(envelope)
    if not pinned._retired():
        events = native_cgroups._read_bound_cgroup_control(pinned.fileno(), "cgroup.events", 4096)
        fields = dict(line.split(maxsplit=1) for line in events.splitlines())
        if fields.get("populated") != "0":
            raise FleetPlanInvalid("Original worker cgroup is not empty")
    identity = "sha256:" + hashlib.sha256(envelope.model_dump_json().encode()).hexdigest()
    observation = observation.model_copy(
        update={
            "native_witness": "original_kernel_scope",
            "native_witness_identity": identity,
        }
    )
    return VerifiedWorkerResourceClosure(
        deferred.admission, envelope, observation, _CLOSURE_ISSUANCE
    )


_UNIT_RESOURCES_MESSAGE = "ae8f7b866b0347b9af31fe1c80b127c0"
_JOURNAL_FIELDS = (
    "MESSAGE_ID",
    "USER_UNIT",
    "USER_INVOCATION_ID",
    "_UID",
    "_PID",
    "_COMM",
    "_EXE",
    "_SYSTEMD_UNIT",
    "_SYSTEMD_USER_UNIT",
    "_BOOT_ID",
    "__CURSOR",
    "__MONOTONIC_TIMESTAMP",
)


def _require_worker_not_executing(envelope: RunResourceEnvelopeEvidence) -> None:
    observed = observe_process_birth(envelope.process_birth.pid)
    if observed is not None and observed != envelope.process_birth:
        return  # A positively different birth is not the old native worker.
    if observe_process_liveness(envelope.process_birth.pid) is not False:
        raise FleetPlanInvalid("Original worker executable lifetime is not closed")


def _unique_journal_fields(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for name, value in pairs:
        if name in result:
            raise ValueError("Native resource journal record contains duplicate fields")
        result[name] = value
    return result


def _recorded_scope_terminal_witness(
    deferred: DeferredWorkerResourceScope, envelope: RunResourceEnvelopeEvidence
) -> dict[str, str]:
    """Consume exact structured manager output, never arbitrary unit-log prose."""
    budget, aggregate = deferred.budget, deferred.budget.aggregate
    manager = aggregate.manager_birth
    if (
        budget.journalctl is None
        or manager is None
        or aggregate.manager_uid is None
        or aggregate.manager_executable is None
        or aggregate.manager_executable_artifact_sha256 is None
        or aggregate.manager_version is None
        or not aggregate.manager_version.startswith("259.9")
        or manager.boot_id != envelope.process_birth.boot_id
    ):
        raise FleetPlanInvalid("Original native manager restart witness is not qualified")
    expected = {
        "MESSAGE_ID": _UNIT_RESOURCES_MESSAGE,
        "USER_UNIT": envelope.unit_name,
        "USER_INVOCATION_ID": envelope.invocation_id,
        "_UID": str(aggregate.manager_uid),
        "_PID": str(manager.pid),
        "_COMM": "systemd",
        "_EXE": aggregate.manager_executable,
        "_SYSTEMD_UNIT": f"user@{aggregate.manager_uid}.service",
        "_SYSTEMD_USER_UNIT": "init.scope",
        "_BOOT_ID": manager.boot_id.replace("-", ""),
    }
    result = run_command(
        budget.journalctl,
        "--user",
        "--no-pager",
        "--quiet",
        "--all",
        "--output=json",
        "--output-fields=" + ",".join(_JOURNAL_FIELDS),
        "--lines=2",
        *(f"{name}={value}" for name, value in expected.items()),
        env=budget.environment,
        timeout=5,
        max_capture_bytes_per_stream=32768,
        require_process_group_quiescence=True,
        parent_death_signal=True,
    )
    if result.returncode != 0 or result.stdout_truncated or result.stderr:
        raise FleetPlanInvalid("Native manager terminal journal evidence is unavailable")
    lines = result.stdout.splitlines()
    if len(lines) != 1:
        raise FleetPlanInvalid("Native manager terminal record is missing or ambiguous")
    try:
        raw = json.loads(lines[0], object_pairs_hook=_unique_journal_fields)
    except (ValueError, UnicodeError, RecursionError) as error:
        raise FleetPlanInvalid("Native manager terminal record is malformed") from error
    if not isinstance(raw, dict) or any(raw.get(name) != value for name, value in expected.items()):
        raise FleetPlanInvalid("Native manager terminal record belongs to another authority")
    cursor, timestamp = raw.get("__CURSOR"), raw.get("__MONOTONIC_TIMESTAMP")
    if (
        not isinstance(cursor, str)
        or not cursor
        or len(cursor) > 1024
        or not cursor.isprintable()
        or not isinstance(timestamp, str)
        or not timestamp.isascii()
        or not timestamp.isdigit()
    ):
        raise FleetPlanInvalid("Native manager terminal record identity is unavailable")
    return {**expected, "__CURSOR": cursor, "__MONOTONIC_TIMESTAMP": timestamp}


def recover_worker_resource_scope(
    deferred: DeferredWorkerResourceScope, envelope: RunResourceEnvelopeEvidence
) -> VerifiedWorkerResourceClosure:
    """Qualified same-manager restart readback of an already GCed original scope.

    On the qualified systemd implementation, UNIT_RESOURCES is emitted when the
    unit enters inactive/failed. Its collector refuses units with nonempty
    cgroups. Neither fact alone establishes closure: require their exact joined
    original invocation, unchanged aggregate and manager, and absent original
    kernel path. Missing storage or manager restart remains unproven.
    """
    if (
        envelope.subject != "worker"
        or envelope.fleet_run_id != deferred.admission.fleet_run_id
        or envelope.unit_name != deferred.admission.worker_unit_name
        or envelope.resources != deferred.budget.policy.worker_resources
        or envelope.control_group is None
    ):
        raise FleetPlanInvalid("Original recorded worker scope is unavailable for recovery")
    require_native_runtime_budget_current(deferred.budget, controller=False)
    _require_worker_not_executing(envelope)
    properties = _query(deferred.scope)
    if not _native_unit_retired(deferred.scope, properties):
        raise FleetPlanInvalid("Original native worker unit is retained or active")
    root = Path("/sys/fs/cgroup")
    mountinfo = native_cgroups._standard_mountinfo_snapshot()
    original = native_cgroups._standard_cgroup_path(
        root, envelope.control_group, mountinfo=mountinfo
    )
    aggregate_properties = _slice_properties(
        deferred.budget.policy.aggregate_slice,
        deferred.budget.systemctl,
        deferred.budget.environment,
    )
    aggregate = native_cgroups._standard_cgroup_path(
        root, aggregate_properties.get("ControlGroup", ""), mountinfo=mountinfo
    )
    if original.name != envelope.unit_name or original.parent != aggregate:
        raise FleetPlanInvalid("Original native worker kernel path differs from aggregate")
    try:
        original.lstat()
    except FileNotFoundError:
        pass
    else:
        raise FleetPlanInvalid("Original native worker cgroup remains present or replaced")
    record = _recorded_scope_terminal_witness(deferred, envelope)
    require_native_runtime_budget_current(deferred.budget, controller=False)
    if (
        not _native_unit_retired(deferred.scope, _query(deferred.scope))
        or native_cgroups._standard_mountinfo_snapshot() != mountinfo
    ):
        raise FleetPlanInvalid("Original native worker closure context changed")
    try:
        original.lstat()
    except FileNotFoundError:
        pass
    else:
        raise FleetPlanInvalid("Original native worker kernel path reappeared")
    payload = json.dumps(record, sort_keys=True, separators=(",", ":")).encode()
    observation = RunResourceEnvelopeObservation(
        fleet_run_id=envelope.fleet_run_id,
        unit_name=envelope.unit_name,
        invocation_id=envelope.invocation_id,
        state="INCOMPLETE",
        cleanup="VERIFIED",
        reason_code="ORIGINAL_MANAGER_CLOSURE_WITNESS",
        native_witness="original_systemd_journal",
        native_witness_identity="sha256:" + hashlib.sha256(payload).hexdigest(),
    )
    return VerifiedWorkerResourceClosure(
        deferred.admission, envelope, observation, _CLOSURE_ISSUANCE
    )
