from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.path_authority import (
    HostSandboxPathMapping,
    RepositoryPathSpace,
    map_host_text_to_sandbox,
)
from oh_goon.core.path_layouts import SANDBOX_PATHS
from oh_goon.integrations.fleet_plan_process_isolation import IsolatedProcessRequest


@dataclass(frozen=True, slots=True)
class SandboxProcessLaunch:
    argv: tuple[str, ...]
    cwd: Path
    env: dict[str, str]


def sandbox_process_launch(request: IsolatedProcessRequest) -> SandboxProcessLaunch:
    """Map one admitted host launch into the canonical sandbox namespace."""

    attempt = request.attempt
    workspace_root = (
        request.read_only_workspace.snapshot_path
        if request.read_only_workspace is not None
        else attempt.paths.workspace
    )
    relative_cwd = RepositoryPathSpace.from_root(workspace_root).relative(
        request.cwd,
        label="Fleet isolated process cwd",
    )
    mappings = _attempt_path_mappings(request)
    argv = tuple(map_host_text_to_sandbox(value, mappings) for value in request.argv)
    tools = {binding.name: binding for binding in attempt.host_tools}
    if argv and "/" not in argv[0] and "\\" not in argv[0]:
        binding = tools.get(argv[0])
        if binding is not None:
            argv = (str(SANDBOX_PATHS.toolchain / binding.name), *argv[1:])
    return SandboxProcessLaunch(
        argv=argv,
        cwd=SANDBOX_PATHS.workspace / relative_cwd,
        env=_sandbox_environment(
            request.env,
            mappings,
            uv_system_python=request.attempt.runtime_root.uv_system_python,
        ),
    )


def _sandbox_environment(
    env: dict[str, str],
    mappings: tuple[HostSandboxPathMapping, ...],
    *,
    uv_system_python: PurePosixPath | None,
) -> dict[str, str]:
    mapped = {name: map_host_text_to_sandbox(value, mappings) for name, value in env.items()}
    mapped.update(
        {
            "HOME": str(SANDBOX_PATHS.home),
            "USERPROFILE": str(SANDBOX_PATHS.home),
            "TMPDIR": str(SANDBOX_PATHS.tmp),
            "TMP": str(SANDBOX_PATHS.tmp),
            "TEMP": str(SANDBOX_PATHS.tmp),
            "XDG_CACHE_HOME": str(SANDBOX_PATHS.cache),
            "UV_CACHE_DIR": str(SANDBOX_PATHS.cache / "uv"),
            # Strict Steps consume the Python runtime frozen during run preparation.
            # uv must not silently provision a newer interpreter from the network;
            # package downloads remain governed separately by the Step egress policy.
            "UV_NO_MANAGED_PYTHON": "1",
            "UV_PYTHON_DOWNLOADS": "never",
            "PRE_COMMIT_HOME": str(SANDBOX_PATHS.cache / "pre-commit"),
            "XDG_CONFIG_HOME": str(SANDBOX_PATHS.config),
            "XDG_DATA_HOME": str(SANDBOX_PATHS.home / ".local" / "share"),
            "XDG_STATE_HOME": str(SANDBOX_PATHS.home / ".local" / "state"),
            "PATH": str(SANDBOX_PATHS.toolchain),
            "LANG": "C",
            "LC_ALL": "C",
            "LC_CTYPE": "C",
            "PYTHONUTF8": "1",
        }
    )
    if uv_system_python is not None:
        mapped["UV_PYTHON"] = str(uv_system_python)
    else:
        mapped.pop("UV_PYTHON", None)
    return mapped


def _attempt_path_mappings(
    request: IsolatedProcessRequest,
) -> tuple[HostSandboxPathMapping, ...]:
    attempt = request.attempt
    workspace_root = (
        request.read_only_workspace.snapshot_path
        if request.read_only_workspace is not None
        else attempt.paths.workspace
    )
    mappings = [
        HostSandboxPathMapping.create(workspace_root, SANDBOX_PATHS.workspace),
        HostSandboxPathMapping.create(attempt.paths.home, SANDBOX_PATHS.home),
        HostSandboxPathMapping.create(attempt.paths.tmp, SANDBOX_PATHS.tmp),
        HostSandboxPathMapping.create(attempt.paths.cache, SANDBOX_PATHS.cache),
        HostSandboxPathMapping.create(attempt.paths.config, SANDBOX_PATHS.config),
    ]
    for binding in attempt.sandbox_bindings:
        if binding.sandbox_path is None:
            raise FleetPlanInvalid("Managed runtime input is missing its sandbox path")
        mappings.append(HostSandboxPathMapping.create(binding.declared_path, binding.sandbox_path))
    return tuple(mappings)
