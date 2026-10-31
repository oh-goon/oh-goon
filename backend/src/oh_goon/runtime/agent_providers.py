"""Read one user-selected provider catalog without probing or installing services."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import sys
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from pydantic import ValidationError

from oh_goon.core.models.agent_providers import (
    DEFAULT_NATIVE_AGENT_PROFILE,
    AgentConnection,
    AgentProviderError,
    AgentProvidersConfig,
    NativeAgentDefinition,
    NativeAgentName,
    NativeAgentProfile,
    ResolvedNativeAgent,
)
from oh_goon.integrations.agent_native_execution import (
    native_executable_sha256,
    require_codex_authentication_home_separation,
    validate_native_agent_arguments,
)
from oh_goon.integrations.exact_file_write import (
    exact_file_write_lock,
    prepared_text_file,
    publish_text_file,
    replace_prepared_file,
    require_expected_exact_file_revision,
)

_MAX_CONFIG_BYTES = 262_144


@dataclass(frozen=True, slots=True)
class AgentProviderCatalog:
    """One bounded authored catalog and its exact publication revision."""

    config: AgentProvidersConfig
    revision: str | None


def agent_providers_path(
    environment: Mapping[str, str], explicit: Path | None = None, *, resolve: bool = True
) -> Path:
    if explicit is not None:
        path = explicit
    elif environment.get("OH_GOON_PROVIDERS_FILE"):
        path = Path(environment["OH_GOON_PROVIDERS_FILE"])
    else:
        if os.name == "nt" and environment.get("APPDATA"):
            base = Path(environment["APPDATA"])
        elif sys.platform == "darwin":
            base = Path.home() / "Library" / "Application Support"
        else:
            config_home = environment.get("XDG_CONFIG_HOME")
            base = Path(config_home).expanduser() if config_home else Path.home() / ".config"
        path = base / "oh-goon" / "providers.json"
    expanded = path.expanduser()
    return expanded.resolve() if resolve else expanded.absolute()


def _json_fields(items: list[tuple[str, object]]) -> dict[str, object]:
    result = {}
    for name, value in items:
        if name in result:
            raise ValueError("duplicate configuration field")
        result[name] = value
    return result


def _catalog_authoring_metadata(path: Path) -> os.stat_result | None:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return None
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_nlink != 1
        or metadata.st_size > _MAX_CONFIG_BYTES
    ):
        raise AgentProviderError(
            "Catalog authoring requires one bounded regular file without aliases"
        )
    return metadata


def require_agent_provider_authoring_paths(paths: tuple[Path, ...]) -> None:
    """Admit only the exact selected regular catalog/schema authoring paths."""
    for path in dict.fromkeys(paths):
        _catalog_authoring_metadata(path)


def read_agent_provider_catalog(
    path: Path, *, missing_ok: bool = False, for_authoring: bool = False
) -> AgentProviderCatalog:
    """Read authored values once, retaining relative paths for catalog updates."""
    try:
        if for_authoring:
            metadata = _catalog_authoring_metadata(path)
            if metadata is None:
                if missing_ok:
                    return AgentProviderCatalog(AgentProvidersConfig(), None)
                raise AgentProviderError("Selected catalog is unavailable for authoring")
            if not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_NONBLOCK"):
                raise AgentProviderError(
                    "Safe catalog authoring requires native no-follow file operations"
                )
            descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            try:
                opened = os.fstat(descriptor)
                if (
                    not stat.S_ISREG(opened.st_mode)
                    or opened.st_nlink != 1
                    or (opened.st_dev, opened.st_ino) != (metadata.st_dev, metadata.st_ino)
                ):
                    raise AgentProviderError("Selected catalog changed before its authoring read")
                with os.fdopen(descriptor, "rb", closefd=False) as stream:
                    data = stream.read(_MAX_CONFIG_BYTES + 1)
            finally:
                os.close(descriptor)
        else:
            if missing_ok and not path.exists():
                return AgentProviderCatalog(AgentProvidersConfig(), None)
            if not path.is_file():
                raise AgentProviderError(f"Cannot read provider configuration at {path}")
            with path.open("rb") as stream:
                data = stream.read(_MAX_CONFIG_BYTES + 1)
    except OSError as exc:
        raise AgentProviderError(f"Cannot read provider configuration at {path}") from exc
    if len(data) > _MAX_CONFIG_BYTES:
        raise AgentProviderError("Provider configuration exceeds 262144 bytes")
    try:
        text = data.decode("utf-8")
        payload = (
            json.loads(text, object_pairs_hook=_json_fields)
            if path.suffix.lower() == ".json"
            else tomllib.loads(text)
        )
        config = AgentProvidersConfig.model_validate(payload)
    except ValidationError as exc:
        # Pydantic's normal display includes raw inputs, potentially including an
        # accidentally pasted credential. Report only the contract message.
        message = exc.errors(include_input=False, include_context=False, include_url=False)[0][
            "msg"
        ]
        raise AgentProviderError(f"Invalid provider configuration: {message}") from None
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise AgentProviderError(
            "Provider configuration must be valid UTF-8 JSON or TOML with unique keys"
        ) from exc
    if any(
        name in {"codex", "opencode"} and definition.configuration_directory is not None
        for name, definition in config.native_agents.items()
    ):
        raise AgentProviderError(
            "Native authentication and configuration remain service-owned; "
            "Codex/OpenCode configuration directories cannot be exposed to agent tools"
        )
    return AgentProviderCatalog(config, hashlib.sha256(data).hexdigest())


def load_agent_providers(path: Path) -> AgentProvidersConfig:
    path = path.expanduser().resolve()
    return resolve_agent_provider_catalog(read_agent_provider_catalog(path).config, path)


def resolve_agent_provider_catalog(
    config: AgentProvidersConfig, path: Path
) -> AgentProvidersConfig:
    """Resolve the captured catalog's paths without another source read."""
    providers = {
        name: provider.model_copy(
            update={"ca_file": str((path.parent / Path(provider.ca_file).expanduser()).resolve())}
        )
        if provider.ca_file is not None
        else provider
        for name, provider in config.providers.items()
    }
    native_agents = {
        name: definition.model_copy(
            update={
                "executable": _configuration_path(path, definition.executable),
                "runtime_roots": tuple(
                    str((path.parent / Path(root).expanduser()).resolve())
                    for root in definition.runtime_roots
                ),
                "configuration_directory": _configuration_path(
                    path, definition.configuration_directory
                ),
            }
        )
        for name, definition in config.native_agents.items()
    }
    native_profiles = {
        name: profile.model_copy(
            update={
                "codex_login": profile.codex_login.model_copy(
                    update={"home": _configuration_path(path, profile.codex_login.home)}
                )
                if profile.codex_login is not None
                else None,
                "codex_service": profile.codex_service.model_copy(
                    update={"home": _configuration_path(path, profile.codex_service.home)}
                )
                if profile.codex_service is not None
                else None,
                "workspace_roots": tuple(
                    str((path.parent / Path(root).expanduser()).resolve())
                    for root in profile.workspace_roots
                ),
            }
        )
        for name, profile in config.native_profiles.items()
    }
    return config.model_copy(
        update={
            "providers": providers,
            "native_agents": native_agents,
            "native_profiles": native_profiles,
        }
    )


def _configuration_path(catalog: Path, value: str | None) -> str | None:
    return None if value is None else str((catalog.parent / Path(value).expanduser()).resolve())


def resolve_native_agent(
    config: AgentProvidersConfig,
    agent: str,
    profile_name: str | None,
    *,
    workspace: Path,
    environment: Mapping[str, str],
) -> ResolvedNativeAgent:
    """Resolve one native program through OS lookup and explicit caller policy.

    The caller loads its catalog once, or explicitly selects an empty native
    catalog. This function never reads another catalog, native agent settings,
    credentials, package metadata or model inventory and never installs a tool.
    A workspace path is checked against policy, not promoted to workspace authority.
    """
    if agent not in ("opencode", "pi", "codex"):
        raise AgentProviderError("Select a supported native agent: opencode, pi or codex")
    if profile_name is None:
        profile_name = config.default_native_profile or DEFAULT_NATIVE_AGENT_PROFILE
    name = cast(NativeAgentName, agent)
    profile = config.native_profiles.get(profile_name)
    if profile is None:
        if profile_name == "local":
            profile = NativeAgentProfile(backend="local")
        elif profile_name == "isolated-offline":
            profile = NativeAgentProfile(backend="bubblewrap")
        elif profile_name == DEFAULT_NATIVE_AGENT_PROFILE:
            profile = NativeAgentProfile(
                backend="bubblewrap", inference_profile=config.default_model
            )
        else:
            raise AgentProviderError("Select a configured native execution profile")
    if name not in profile.allowed_agents:
        raise AgentProviderError("Selected native agent is not allowed by the execution profile")
    try:
        selected_workspace = workspace.resolve(strict=True)
        if not selected_workspace.is_dir():
            raise OSError("not a directory")
        workspace_roots = tuple(
            _existing_native_path(root, directory=True) for root in profile.workspace_roots
        )
    except OSError:
        raise AgentProviderError(
            "Selected native workspace or policy root is unavailable"
        ) from None
    if workspace_roots and not any(
        selected_workspace.is_relative_to(root) for root in workspace_roots
    ):
        raise AgentProviderError(
            "Selected native workspace is outside the profile's workspace roots"
        )
    definition = config.native_agents.get(name, NativeAgentDefinition())
    if name in {"codex", "opencode"} and definition.configuration_directory is not None:
        raise AgentProviderError(
            "Native authentication and configuration remain service-owned; "
            "Codex/OpenCode configuration directories cannot be exposed to agent tools"
        )
    service = profile.codex_service
    if service is not None and (
        name != "codex"
        or definition.arguments
        or definition.configuration_directory is not None
        or profile.codex_login is not None
        or profile.inference_profile is not None
        or profile.backend != "local"
        or profile.sandbox_owner != "harness"
    ):
        raise AgentProviderError(
            "Codex service requires Codex with its native tool sandbox and no login-home, "
            "broker inference or native argument/config overrides"
        )
    service_home = None
    if service is not None:
        try:
            service_home = _existing_native_path(service.home, directory=True)
        except OSError:
            raise AgentProviderError(
                "Selected Codex service state directory is unavailable"
            ) from None
        require_codex_authentication_home_separation(service_home, (selected_workspace,))
        profile = profile.model_copy(
            update={"codex_service": service.model_copy(update={"home": str(service_home)})}
        )
    login = profile.codex_login
    if login is not None:
        if (
            name != "codex"
            or definition.arguments
            or definition.configuration_directory is not None
        ):
            raise AgentProviderError(
                "Codex login requires Codex with its typed connection and no native argument/config overrides"
            )
        if profile.backend != "local" or profile.sandbox_owner != "harness":
            raise AgentProviderError(
                "Protected native sign-in requires a qualified service connection; "
                "authentication and configuration files cannot be mounted into agent tools"
            )
        try:
            native_home = _existing_native_path(login.home, directory=True)
        except OSError:
            raise AgentProviderError("Selected Codex authentication home is unavailable") from None
        if selected_workspace.is_relative_to(native_home) or native_home.is_relative_to(
            selected_workspace
        ):
            raise AgentProviderError(
                "Codex authentication home must be outside the captured workspace"
            )
        profile = profile.model_copy(
            update={"codex_login": login.model_copy(update={"home": str(native_home)})}
        )
    validate_native_agent_arguments(name, definition.arguments, sandbox_owner=profile.sandbox_owner)
    executable = definition.executable
    if executable is None:
        executable = shutil.which(name, path=environment.get("PATH", ""))
        if executable is None:
            raise AgentProviderError("Selected native agent is not installed on the supplied PATH")
    try:
        selected_executable = _existing_native_path(executable, directory=False)
        runtime_roots = tuple(_existing_native_path(root) for root in definition.runtime_roots)
        configuration = (
            _existing_native_path(definition.configuration_directory, directory=True)
            if definition.configuration_directory is not None
            else None
        )
        if service_home is not None:
            require_codex_authentication_home_separation(
                service_home, (selected_executable, *runtime_roots)
            )
        digest = native_executable_sha256(selected_executable)
    except OSError:
        raise AgentProviderError(
            "Selected native executable or public resource is unavailable"
        ) from None
    frozen_definition = definition.model_copy(
        deep=True,
        update={
            "executable": str(selected_executable),
            "runtime_roots": tuple(str(root) for root in runtime_roots),
            "configuration_directory": str(configuration) if configuration is not None else None,
        },
    )
    frozen_profile = profile.model_copy(
        deep=True, update={"workspace_roots": tuple(str(root) for root in workspace_roots)}
    )
    connection = (
        AgentConnection.select(config, profile.inference_profile)
        if profile.inference_profile is not None
        else None
    )
    return ResolvedNativeAgent(
        agent=name,
        profile_name=profile_name,
        definition=frozen_definition,
        profile=frozen_profile,
        executable_sha256=digest,
        connection=connection,
    )


def _existing_native_path(value: str, *, directory: bool | None = None) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise OSError("native paths must already be resolved")
    path = path.resolve(strict=True)
    mode = path.stat().st_mode
    if directory is True and not stat.S_ISDIR(mode):
        raise OSError("not a directory")
    if directory is False and not stat.S_ISREG(mode):
        raise OSError("not a regular executable")
    if directory is None and not (stat.S_ISDIR(mode) or stat.S_ISREG(mode)):
        raise OSError("not a native file or directory")
    return path


def create_agent_providers(path: Path, config: AgentProvidersConfig) -> Path:
    """Create one editable JSON catalog and its model-derived editor schema."""
    path = path.expanduser().resolve()
    if path.suffix.lower() != ".json":
        raise AgentProviderError("Provider setup requires a .json configuration path")
    if path.exists():
        raise AgentProviderError(f"Provider configuration already exists; edit {path}")
    return save_agent_provider_catalog(path, config, expected_revision=None)


def save_agent_provider_catalog(
    path: Path, config: AgentProvidersConfig, *, expected_revision: str | None
) -> Path:
    """Publish a catalog update only against its captured authored revision."""
    if path.suffix.lower() != ".json":
        raise AgentProviderError("Guided native setup requires a .json configuration path")
    payload = config.model_dump(mode="json", by_alias=True, exclude_none=True, exclude_unset=True)
    AgentProvidersConfig.model_validate(payload)
    schema_path = path.with_suffix(".schema.json")
    if expected_revision is None:
        payload["$schema"] = schema_path.name
    content = json.dumps(payload, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
    if len(content.encode("utf-8")) > _MAX_CONFIG_BYTES:
        raise AgentProviderError("Provider configuration exceeds 262144 bytes")
    path.parent.mkdir(parents=True, exist_ok=True)
    with exact_file_write_lock(path):
        require_agent_provider_authoring_paths((path, schema_path))
        require_expected_exact_file_revision(
            path,
            expected_revision,
            invalid_error=lambda: AgentProviderError(
                "Provider configuration is not a regular file"
            ),
            changed_error=lambda _current, _expected: AgentProviderError(
                "Provider configuration changed during setup; inspect it and run setup again"
            ),
            removed_error=lambda: AgentProviderError(
                "Provider configuration was removed during setup; run setup again"
            ),
        )
        if expected_revision is None:
            publish_text_file(
                schema_path, json.dumps(AgentProvidersConfig.model_json_schema(), indent=2) + "\n"
            )
        with prepared_text_file(path, content) as prepared:
            replace_prepared_file(prepared, path)
    return path
