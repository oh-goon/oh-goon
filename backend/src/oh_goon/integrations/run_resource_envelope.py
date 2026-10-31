"""Native resource scope for the original waited CLI, before Run preparation."""

from __future__ import annotations

import hashlib
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

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.models.fleet_plan_isolation import CicdResourcePolicy
from oh_goon.core.models.run_resources import (
    RunResourceEnvelopeEvidence,
    RunResourceEnvelopeObservation,
)
from oh_goon.core.process_birth import ProcessBirthIdentity, observe_process_birth
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
        return {
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
        if not isinstance(payload, dict) or set(payload) != keys or payload["schema"] != _SCHEMA:
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
    selected = tuple(shutil.which(name) for name in ("systemd-run", "systemctl"))
    if any(path is None for path in selected):
        raise FleetPlanInvalid("Whole-Run resources require native systemd-run and systemctl")
    systemd_run, systemctl = (str(Path(path).resolve(strict=True)) for path in selected if path)
    run_sha, query_sha = _executable_sha256(systemd_run), _executable_sha256(systemctl)
    environment = {name: os.environ[name] for name in sorted(_MANAGER_NAMES) if name in os.environ}
    command = native_cgroups.standard_resource_scope_command(
        resources, systemd_run, query_executable=systemctl, manager_environment=environment
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
    scope = ResolvedRunResourceScope(
        fleet_run_id,
        resources,
        unit,
        systemd_run,
        run_sha,
        systemctl,
        query_sha,
        tuple(environment.items()),
        prefix,
    )
    _require_tools_current(scope)
    return scope


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
            fleet_run_id=scope.fleet_run_id,
            unit_name=scope.unit_name,
            invocation_id=invocation,
            handoff_sha256=handoff_sha256,
            process_birth=birth,
            resources=scope.resources,
            cgroup_device=identity[0],
            cgroup_inode=identity[1],
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


@dataclass(slots=True)
class PinnedRunResourceScope:
    """Parent-owned readonly original directory handle, acquired before ready ACK."""

    resolved: ResolvedRunResourceScope
    started: RunResourceEnvelopeEvidence
    _directory: Path = field(repr=False)
    _descriptor: int | None = field(repr=False)
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
        return self._metadata().st_nlink == 0


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
        )
        if current != started or observe_process_birth(expected_birth.pid) != expected_birth:
            raise FleetPlanInvalid("Whole-Run waiting process observations changed")
        if native_cgroups._standard_mountinfo_snapshot() != mountinfo:
            raise FleetPlanInvalid("Whole-Run waiting mount view changed")
        native_cgroups._require_original_cgroup(target, identity)
        return PinnedRunResourceScope(scope, started, target, handle, lifetime.pop_all())


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
