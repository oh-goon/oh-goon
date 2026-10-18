from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

from oh_goon.core import local_access_protocol
from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.models.fleet_plan import (
    ResolvedGitStep,
    ResolvedLocalSearchAgentStep,
    ResolvedPlanStep,
)
from oh_goon.core.models.fleet_plan_values import (
    HostEnvironmentPart,
    LiteralEnvironmentPart,
    ResolvedEnvironmentBinding,
)
from oh_goon.core.sqlite_journal_vocabulary import SQLITE_JOURNAL_MODE_ENV
from oh_goon.core.strict_executor_authority import EXECUTORS_FILE_ENV


def _non_exportable_host_environment() -> frozenset[str]:
    return frozenset(
        {
            "OH_GOON_DB",
            EXECUTORS_FILE_ENV,
            "OH_GOON_LEAGUE_FILE",
            local_access_protocol.LOCAL_ACCESS_ENV,
            SQLITE_JOURNAL_MODE_ENV,
        }
    )


_RUNTIME_OWNED_ENV = frozenset(
    {
        "PATH",
        "HOME",
        "PYTHONHOME",
        "PYTHONPATH",
        "VIRTUAL_ENV",
        "UV_PROJECT_ENVIRONMENT",
        "USERPROFILE",
        "TMPDIR",
        "TMP",
        "TEMP",
        "XDG_CACHE_HOME",
        "UV_CACHE_DIR",
        "PRE_COMMIT_HOME",
        "XDG_CONFIG_HOME",
        "XDG_DATA_HOME",
        "XDG_STATE_HOME",
        # Loader/interpreter injection changes executable semantics and can make
        # direct execution load host bytes outside admitted tool/runtime authority.
        "LD_PRELOAD",
        "LD_LIBRARY_PATH",
        "DYLD_INSERT_LIBRARIES",
        "DYLD_LIBRARY_PATH",
        "DYLD_FRAMEWORK_PATH",
        "BASH_ENV",
        "ENV",
        "NODE_OPTIONS",
        "PERL5OPT",
        "RUBYOPT",
    }
)

_GIT_EXECUTION_CONTROL_ENV = frozenset(
    {
        "GIT_ASKPASS",
        "GIT_CONFIG_COUNT",
        "GIT_CONFIG_GLOBAL",
        "GIT_CONFIG_SYSTEM",
        "GIT_EXEC_PATH",
        "GIT_EXTERNAL_DIFF",
        "GIT_SSH",
        "GIT_SSH_COMMAND",
        "GIT_TEMPLATE_DIR",
        "SSH_ASKPASS",
    }
)


def _is_git_execution_control_environment(name: str) -> bool:
    folded = name.casefold()
    return (
        folded in {value.casefold() for value in _GIT_EXECUTION_CONTROL_ENV}
        or folded.startswith("git_config_key_")
        or folded.startswith("git_config_value_")
    )


def git_execution_environment(environment: Mapping[str, str]) -> dict[str, str]:
    env = {
        name: value
        for name, value in environment.items()
        if not _is_git_execution_control_environment(name)
    }
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    env["GIT_CONFIG_GLOBAL"] = os.devnull
    env["GIT_TERMINAL_PROMPT"] = "0"
    return env


_PLAN_BASE_ENV = (
    "PATH",
    "SYSTEMROOT",
    "WINDIR",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
)


def step_host_environment_names(step: ResolvedPlanStep) -> tuple[str, ...]:
    names = [
        part.name
        for binding in getattr(step, "environment", ())
        for part in binding.parts
        if isinstance(part, HostEnvironmentPart)
    ]
    credentials = getattr(step, "credentials", None)
    credential_names = getattr(credentials, "host_environment_names", ())
    names.extend(credential_names)
    if isinstance(step, ResolvedLocalSearchAgentStep) and step.connection is not None:
        provider = step.connection.provider
        if provider.api_key_env is not None:
            names.append(provider.api_key_env)
        names.extend(provider.headers_from_env.values())
    return tuple(dict.fromkeys(names))


def require_environment(step: ResolvedPlanStep) -> None:
    for binding in getattr(step, "environment", ()):
        require_exportable_host_environment_name(binding.name)
        if isinstance(step, ResolvedGitStep) and _is_git_execution_control_environment(
            binding.name
        ):
            raise FleetPlanInvalid(
                f"Fleet git steps cannot replace Git execution-control environment value {binding.name}"
            )
    names = step_host_environment_names(step)
    for name in names:
        require_exportable_host_environment_name(name)
    missing = [name for name in names if name not in os.environ]
    if missing:
        raise FleetPlanInvalid(
            f"Fleet plan step {step.step_id} requires missing imported host environment values: "
            + ", ".join(sorted(set(missing)))
        )


def direct_plan_environment(
    bindings: tuple[ResolvedEnvironmentBinding, ...],
    *,
    home: Path | None = None,
    tmp: Path | None = None,
    cache: Path | None = None,
    config: Path | None = None,
    host_environment: Mapping[str, str],
) -> dict[str, str]:
    return _plan_environment(
        bindings,
        base={name: host_environment[name] for name in _PLAN_BASE_ENV if name in host_environment},
        home=home,
        tmp=tmp,
        cache=cache,
        config=config,
        host_environment=host_environment,
    )


def strict_plan_environment(
    bindings: tuple[ResolvedEnvironmentBinding, ...],
    *,
    home: Path | None = None,
    tmp: Path | None = None,
    cache: Path | None = None,
    config: Path | None = None,
    host_environment: Mapping[str, str],
) -> dict[str, str]:
    return _plan_environment(
        bindings,
        base={
            "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "LC_CTYPE": "C.UTF-8",
        },
        home=home,
        tmp=tmp,
        cache=cache,
        config=config,
        host_environment=host_environment,
    )


def _plan_environment(
    bindings: tuple[ResolvedEnvironmentBinding, ...],
    *,
    base: Mapping[str, str],
    home: Path | None,
    tmp: Path | None,
    cache: Path | None,
    config: Path | None,
    host_environment: Mapping[str, str],
) -> dict[str, str]:
    env = dict(base)
    if home is not None:
        env["HOME"] = str(home)
        env["USERPROFILE"] = str(home)
    if tmp is not None:
        env["TMPDIR"] = str(tmp)
        env["TMP"] = str(tmp)
        env["TEMP"] = str(tmp)
    if cache is not None:
        env["XDG_CACHE_HOME"] = str(cache)
        env["UV_CACHE_DIR"] = str(cache / "uv")
        env["PRE_COMMIT_HOME"] = str(cache / "pre-commit")
    if config is not None:
        env["XDG_CONFIG_HOME"] = str(config)
    if home is not None:
        env["XDG_DATA_HOME"] = str(home / ".local" / "share")
        env["XDG_STATE_HOME"] = str(home / ".local" / "state")
    for binding in bindings:
        require_exportable_host_environment_name(binding.name)
        if binding.name.upper() in _RUNTIME_OWNED_ENV:
            raise FleetPlanInvalid(
                f"Fleet steps cannot replace runtime-owned environment value {binding.name}"
            )
        env[binding.name] = _environment_binding_value(binding, host_environment=host_environment)
    return env


def _environment_binding_value(
    binding: ResolvedEnvironmentBinding,
    *,
    host_environment: Mapping[str, str],
) -> str:
    chunks: list[str] = []
    for part in binding.parts:
        if isinstance(part, LiteralEnvironmentPart):
            chunks.append(part.value)
        elif isinstance(part, HostEnvironmentPart):
            require_exportable_host_environment_name(part.name)
            try:
                chunks.append(host_environment[part.name])
            except KeyError as exc:
                raise FleetPlanInvalid(
                    f"Fleet step import {binding.name} requires missing host environment value {part.name}"
                ) from exc
        else:  # pragma: no cover
            raise AssertionError(f"Unhandled Fleet environment value part: {type(part).__name__}")
    return "".join(chunks)


def require_exportable_host_environment_name(name: str) -> None:
    if name.upper() in _non_exportable_host_environment():
        raise FleetPlanInvalid(
            f"Fleet steps cannot import process-local control-plane environment value {name}"
        )
