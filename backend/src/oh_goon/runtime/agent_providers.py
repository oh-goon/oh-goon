"""Read one user-selected provider catalog without probing or installing services."""

from __future__ import annotations

import os
import sys
import tomllib
from collections.abc import Mapping
from pathlib import Path

from pydantic import ValidationError

from oh_goon.core.models.agent_providers import AgentProviderError, AgentProvidersConfig

_MAX_CONFIG_BYTES = 262_144


def agent_providers_path(environment: Mapping[str, str], explicit: Path | None = None) -> Path:
    if explicit is not None:
        return explicit.expanduser().resolve()
    if environment.get("OH_GOON_PROVIDERS_FILE"):
        return Path(environment["OH_GOON_PROVIDERS_FILE"]).expanduser().resolve()
    if os.name == "nt" and environment.get("APPDATA"):
        base = Path(environment["APPDATA"])
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        config_home = environment.get("XDG_CONFIG_HOME")
        base = Path(config_home).expanduser() if config_home else Path.home() / ".config"
    return (base / "oh-goon" / "providers.toml").resolve()


def load_agent_providers(path: Path) -> AgentProvidersConfig:
    path = path.expanduser().resolve()
    if not path.is_file():
        raise AgentProviderError(f"Cannot read provider configuration at {path}")
    try:
        with path.open("rb") as stream:
            data = stream.read(_MAX_CONFIG_BYTES + 1)
    except OSError as exc:
        raise AgentProviderError(f"Cannot read provider configuration at {path}") from exc
    if len(data) > _MAX_CONFIG_BYTES:
        raise AgentProviderError("Provider configuration exceeds 262144 bytes")
    try:
        payload = tomllib.loads(data.decode("utf-8"))
        config = AgentProvidersConfig.model_validate(payload)
    except (UnicodeError, tomllib.TOMLDecodeError) as exc:
        raise AgentProviderError(
            "Provider configuration must be valid UTF-8 TOML with unique keys"
        ) from exc
    except ValidationError as exc:
        # Pydantic's normal display includes raw inputs, potentially including an
        # accidentally pasted credential. Report only the contract message.
        message = exc.errors(include_input=False, include_context=False, include_url=False)[0][
            "msg"
        ]
        raise AgentProviderError(f"Invalid provider configuration: {message}") from None
    providers = {
        name: provider.model_copy(
            update={"ca_file": str((path.parent / Path(provider.ca_file).expanduser()).resolve())}
        )
        if provider.ca_file is not None
        else provider
        for name, provider in config.providers.items()
    }
    return config.model_copy(update={"providers": providers})
