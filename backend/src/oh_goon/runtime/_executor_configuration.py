from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.execution_environment import (
    ResolvedExecutionEnvironment,
    configured_environment_backend,
)
from oh_goon.core.models.fleet_executor import FleetExecutorIdentity
from oh_goon.core.path_authority import StrictHostPathAuthority, require_absolute_host_path
from oh_goon.core.strict_executor_authority import EXECUTORS_FILE_ENV
from oh_goon.integrations.fleet_plan_bubblewrap_isolation import BubblewrapProcessIsolation
from oh_goon.integrations.fleet_plan_opensandbox_isolation import (
    OpenSandboxProcessIsolation,
    OpenSandboxSettings,
)
from oh_goon.integrations.fleet_plan_process_isolation import (
    ProcessIsolationBackend,
)
from oh_goon.runtime.executor_capacity import ExecutorCapacitySettings
from oh_goon.runtime.schema_version_policy import (
    DurableSchemaDisposition,
    DurableSchemaPolicy,
)

EXECUTOR_CONFIG_VERSION = 8
EXECUTOR_CONFIG_SCHEMA = DurableSchemaPolicy(
    name="Fleet executor inventory",
    current=EXECUTOR_CONFIG_VERSION,
)


@dataclass(frozen=True, slots=True)
class ConfiguredFleetExecutor:
    """One deployment-owned physical executor candidate; not persisted as runtime state."""

    priority: int
    backend: ProcessIsolationBackend
    capacity: ExecutorCapacitySettings

    @property
    def identity(self) -> FleetExecutorIdentity:
        """Keep executor identity flat for callers while capacity remains its single owner."""
        return self.capacity.executor

    def resolve_environment(
        self, path_authority: StrictHostPathAuthority
    ) -> ResolvedExecutionEnvironment:
        """Project one backend's existing facts; do not reselect or assert admission."""
        capabilities = self.backend.capabilities
        return ResolvedExecutionEnvironment.from_authorities(
            backend=capabilities.backend_id,
            executor_id=self.identity.executor_id,
            executor_generation=self.identity.generation,
            backend_configuration_identity=self.backend.configuration_identity(),
            capability_statement=capabilities.model_dump(mode="json"),
            deployment_authority={
                "runtime_inspector": str(path_authority.runtime_inspector),
                "trusted_host_roots": [str(p) for p in path_authority.trusted_roots],
                "tool_search_paths": [str(p) for p in path_authority.tool_search_paths],
                "cpu_millis": self.capacity.cpu_millis,
                "memory_mib": self.capacity.memory_mib,
                "max_processes": self.capacity.max_processes,
                "cgroup_root": str(self.capacity.cgroup_root)
                if self.capacity.cgroup_root
                else None,
            },
        )


@dataclass(frozen=True, slots=True)
class ExecutorPoolConfiguration:
    """Validated executor inventory loaded from one operator-owned TOML file."""

    placement_wait_seconds: int
    placement_queue_limit: int
    path_authority: StrictHostPathAuthority
    executors: tuple[ConfiguredFleetExecutor, ...]


def configured_executor_pool(
    *, control_state_root: Path | None = None
) -> ExecutorPoolConfiguration | None:
    raw = os.environ.get(EXECUTORS_FILE_ENV)
    if not raw:
        return None
    path = require_absolute_host_path(raw, label=EXECUTORS_FILE_ENV)
    return load_executor_pool(path, control_state_root=control_state_root)


def load_executor_pool(
    path: Path, *, control_state_root: Path | None = None
) -> ExecutorPoolConfiguration:
    """Load one executor inventory through the canonical runtime parser."""
    if not path.is_file():
        raise FleetPlanInvalid(f"Fleet executor inventory does not exist: {path}")
    try:
        payload = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise FleetPlanInvalid(f"Fleet executor inventory is not valid TOML: {path}") from exc
    return _parse_executor_pool(payload, source=path, control_state_root=control_state_root)


def _parse_executor_pool(
    payload: dict[str, Any], *, source: Path, control_state_root: Path | None = None
) -> ExecutorPoolConfiguration:
    raw_version = payload.get("version")
    if not isinstance(raw_version, int) or isinstance(raw_version, bool):
        raise FleetPlanInvalid(
            f"Fleet executor inventory {source} must declare integer version = {EXECUTOR_CONFIG_VERSION}"
        )
    disposition = EXECUTOR_CONFIG_SCHEMA.classify(raw_version)
    if disposition is not DurableSchemaDisposition.CURRENT:
        raise FleetPlanInvalid(
            f"Fleet executor inventory {source}: "
            f"{EXECUTOR_CONFIG_SCHEMA.version_status_text(raw_version)}; "
            f"required version = {EXECUTOR_CONFIG_VERSION}"
        )
    _reject_unknown_fields(
        payload,
        {
            "version",
            "placement_wait_seconds",
            "placement_queue_limit",
            "runtime_inspector",
            "trusted_host_roots",
            "tool_search_paths",
            "executors",
        },
        source=source,
        context="top level",
    )
    placement_wait_seconds = _required_int(payload, "placement_wait_seconds", source, minimum=0)
    placement_queue_limit = _required_int(payload, "placement_queue_limit", source, minimum=1)
    # Validate pure policy scalars before touching host path authority.  Invalid
    # placement policy must be reported deterministically even when certification
    # intentionally hides ambient host paths.
    if placement_wait_seconds > 3600:
        raise FleetPlanInvalid(
            f"Fleet executor inventory {source} requires placement_wait_seconds <= 3600"
        )
    if placement_queue_limit > 10000:
        raise FleetPlanInvalid(
            f"Fleet executor inventory {source} requires placement_queue_limit <= 10000"
        )
    path_authority = _parse_path_authority(payload, source=source)
    raw_executors = payload.get("executors")
    if not isinstance(raw_executors, list) or not raw_executors:
        raise FleetPlanInvalid(f"Fleet executor inventory {source} requires [[executors]] entries")

    executors: list[ConfiguredFleetExecutor] = []
    ids: set[str] = set()
    local_seen = False
    for index, raw in enumerate(raw_executors):
        if not isinstance(raw, dict):
            raise FleetPlanInvalid(f"Fleet executor inventory entry {index} must be a table")
        candidate, is_local = _parse_executor(
            raw, source=source, control_state_root=control_state_root
        )
        if candidate.identity.executor_id in ids:
            raise FleetPlanInvalid(f"Duplicate Fleet executor id: {candidate.identity.executor_id}")
        if is_local and local_seen:
            raise FleetPlanInvalid(
                "Fleet executor inventory may define only one local Bubblewrap executor; "
                "duplicating one host under several ids would double-count capacity"
            )
        ids.add(candidate.identity.executor_id)
        local_seen = local_seen or is_local
        executors.append(candidate)
    return ExecutorPoolConfiguration(
        placement_wait_seconds=placement_wait_seconds,
        placement_queue_limit=placement_queue_limit,
        path_authority=path_authority,
        executors=tuple(executors),
    )


def _parse_path_authority(payload: dict[str, Any], *, source: Path) -> StrictHostPathAuthority:
    runtime_inspector = _required_path(payload, "runtime_inspector", source)
    raw_roots = payload.get("trusted_host_roots")
    if not isinstance(raw_roots, list) or not raw_roots:
        raise FleetPlanInvalid(
            f"Fleet executor inventory {source} requires non-empty trusted_host_roots"
        )
    trusted_roots: list[Path] = []
    for index, raw_root in enumerate(raw_roots):
        if not isinstance(raw_root, str) or not raw_root.strip():
            raise FleetPlanInvalid(
                f"Fleet executor inventory {source} trusted_host_roots[{index}] must be an absolute path"
            )
        trusted_roots.append(
            require_absolute_host_path(
                raw_root,
                label=f"Fleet executor inventory {source} trusted_host_roots[{index}]",
            )
        )
    raw_search_paths = payload.get("tool_search_paths")
    if not isinstance(raw_search_paths, list) or not raw_search_paths:
        raise FleetPlanInvalid(
            f"Fleet executor inventory {source} requires non-empty tool_search_paths"
        )
    tool_search_paths: list[Path] = []
    for index, raw_path in enumerate(raw_search_paths):
        if not isinstance(raw_path, str) or not raw_path.strip():
            raise FleetPlanInvalid(
                f"Fleet executor inventory {source} tool_search_paths[{index}] must be an absolute path"
            )
        tool_search_paths.append(
            require_absolute_host_path(
                raw_path,
                label=f"Fleet executor inventory {source} tool_search_paths[{index}]",
            )
        )
    return StrictHostPathAuthority.create(
        trusted_roots=tuple(trusted_roots),
        tool_search_paths=tuple(tool_search_paths),
        runtime_inspector=runtime_inspector,
    )


def _parse_executor(
    raw: dict[str, Any], *, source: Path, control_state_root: Path | None = None
) -> tuple[ConfiguredFleetExecutor, bool]:
    executor_id = _required_string(raw, "id", source)
    identity = FleetExecutorIdentity(
        executor_id=executor_id,
        generation=_required_int(raw, "generation", source, minimum=1),
    )
    priority = _required_int(raw, "priority", source, minimum=0)
    backend, is_local, cgroup_root = _parse_executor_backend(
        raw,
        source=source,
        executor_id=executor_id,
        control_state_root=control_state_root,
    )
    capacity = ExecutorCapacitySettings(
        executor=identity,
        cpu_millis=_required_int(raw, "cpu_millis", source, minimum=100),
        memory_mib=_required_int(raw, "memory_mib", source, minimum=64),
        max_processes=_optional_int(raw, "max_processes", source, minimum=16),
        cgroup_root=cgroup_root,
    )
    return (
        ConfiguredFleetExecutor(
            priority=priority,
            backend=backend,
            capacity=capacity,
        ),
        is_local,
    )


def _parse_executor_backend(
    raw: dict[str, Any],
    *,
    source: Path,
    executor_id: str,
    control_state_root: Path | None = None,
) -> tuple[ProcessIsolationBackend, bool, Path | None]:
    common_fields = {
        "id",
        "generation",
        "execution",
        "priority",
        "cpu_millis",
        "memory_mib",
        "max_processes",
    }
    # This is the only backend-selection owner.  No kind alias, ordered candidate
    # selection, omitted value, or implicit compatibility default is accepted.
    kind = configured_environment_backend(raw.get("execution"))
    if kind == "bubblewrap":
        allowed = common_fields | {
            "bwrap_executable",
            "cgroup_root",
            "ip_executable",
            "nft_executable",
            "egress_pool",
        }
        _reject_unknown_fields(
            raw, allowed, source=source, context=f"Bubblewrap executor {executor_id!r}"
        )
        cgroup_root = _required_path(raw, "cgroup_root", source)
        backend = _bubblewrap_backend(
            raw,
            source=source,
            cgroup_root=cgroup_root,
            control_state_root=control_state_root,
        )
        return backend, True, cgroup_root
    if kind == "opensandbox":
        allowed = common_fields | {
            "domain",
            "protocol",
            "image",
            "runtime",
            "api_key_env",
            "cli_executable",
            "runtime_attestation_file",
            "runtime_attestation_public_key_file",
        }
        _reject_unknown_fields(
            raw, allowed, source=source, context=f"OpenSandbox executor {executor_id!r}"
        )
        return OpenSandboxProcessIsolation(_opensandbox_settings(raw, source=source)), False, None
    raise FleetPlanInvalid(f"Fleet executor {executor_id!r} has unsupported kind {kind!r}")


def _bubblewrap_backend(
    raw: dict[str, Any],
    *,
    source: Path,
    cgroup_root: Path,
    control_state_root: Path | None = None,
) -> BubblewrapProcessIsolation:
    bwrap_executable = _required_path(raw, "bwrap_executable", source)
    ip_executable = _optional_path(raw, "ip_executable", source)
    nft_executable = _optional_path(raw, "nft_executable", source)
    egress_pool = _optional_string(raw, "egress_pool")
    values = (ip_executable, nft_executable, egress_pool)
    if any(value is not None for value in values) and not all(
        value is not None for value in values
    ):
        raise FleetPlanInvalid(
            f"Fleet executor inventory {source} must declare ip_executable, "
            "nft_executable, and egress_pool together when restricted egress is configured"
        )
    return BubblewrapProcessIsolation(
        executable=str(bwrap_executable),
        ip_executable=str(ip_executable) if ip_executable is not None else None,
        nft_executable=str(nft_executable) if nft_executable is not None else None,
        egress_pool=egress_pool,
        control_state_root=control_state_root,
        cgroup_root=cgroup_root,
        require_local_resource_intent=True,
    )


def _opensandbox_settings(raw: dict[str, Any], *, source: Path) -> OpenSandboxSettings:
    api_key_env = _required_string(raw, "api_key_env", source)
    api_key = os.environ.get(api_key_env)
    if not api_key:
        raise FleetPlanInvalid(
            f"Fleet OpenSandbox executor requires API key environment variable {api_key_env}"
        )
    settings = OpenSandboxSettings(
        domain=_required_string(raw, "domain", source),
        api_key=api_key,
        api_key_env=api_key_env,
        image=_required_string(raw, "image", source),
        cli_executable=str(_required_path(raw, "cli_executable", source)),
        runtime_attestation_file=str(_required_path(raw, "runtime_attestation_file", source)),
        runtime_attestation_public_key_file=str(
            _required_path(raw, "runtime_attestation_public_key_file", source)
        ),
        runtime=_required_string(raw, "runtime", source),
        protocol=_required_string(raw, "protocol", source),
    )
    settings.validate()
    return settings


def _reject_unknown_fields(
    raw: dict[str, Any],
    allowed: set[str],
    *,
    source: Path,
    context: str,
) -> None:
    unknown = sorted(set(raw) - allowed)
    if unknown:
        raise FleetPlanInvalid(
            f"Fleet executor inventory {source} has unknown {context} field(s): "
            + ", ".join(unknown)
        )


def _required_string(raw: dict[str, Any], name: str, source: Path) -> str:
    value = raw.get(name)
    if not isinstance(value, str) or not value.strip():
        raise FleetPlanInvalid(f"Fleet executor inventory {source} requires non-empty {name}")
    return value.strip()


def _optional_string(raw: dict[str, Any], name: str) -> str | None:
    value = raw.get(name)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise FleetPlanInvalid(f"Fleet executor inventory field {name} must be a non-empty string")
    return value.strip()


def _required_int(raw: dict[str, Any], name: str, source: Path, *, minimum: int) -> int:
    value = raw.get(name)
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
        raise FleetPlanInvalid(
            f"Fleet executor inventory {source} requires integer {name} >= {minimum}"
        )
    return value


def _optional_int(raw: dict[str, Any], name: str, source: Path, *, minimum: int) -> int | None:
    value = raw.get(name)
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
        raise FleetPlanInvalid(
            f"Fleet executor inventory {source} requires integer {name} >= {minimum} when set"
        )
    return value


def _required_path(raw: dict[str, Any], name: str, source: Path) -> Path:
    value = _required_string(raw, name, source)
    return require_absolute_host_path(
        value, label=f"Fleet executor inventory {source} field {name}"
    )


def _optional_path(raw: dict[str, Any], name: str, source: Path) -> Path | None:
    value = raw.get(name)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise FleetPlanInvalid(
            f"Fleet executor inventory {source} field {name} must be a non-empty absolute path"
        )
    return require_absolute_host_path(
        value, label=f"Fleet executor inventory {source} field {name}"
    )
