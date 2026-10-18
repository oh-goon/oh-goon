"""Read one user-selected provider catalog without probing or installing services."""

from __future__ import annotations

import json
import os
import sys
import tomllib
from collections.abc import Mapping
from pathlib import Path

from pydantic import ValidationError

from oh_goon.core.models.agent_providers import AgentProviderError, AgentProvidersConfig
from oh_goon.integrations.exact_file_write import (
    exact_file_write_lock,
    prepared_text_file,
    publish_text_file,
    replace_prepared_file,
)

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
    return (base / "oh-goon" / "providers.json").resolve()


def _json_fields(items: list[tuple[str, object]]) -> dict[str, object]:
    result = {}
    for name, value in items:
        if name in result:
            raise ValueError("duplicate configuration field")
        result[name] = value
    return result


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
    providers = {
        name: provider.model_copy(
            update={"ca_file": str((path.parent / Path(provider.ca_file).expanduser()).resolve())}
        )
        if provider.ca_file is not None
        else provider
        for name, provider in config.providers.items()
    }
    return config.model_copy(update={"providers": providers})


def create_agent_providers(path: Path, config: AgentProvidersConfig) -> Path:
    """Create one editable JSON catalog and its model-derived editor schema."""
    path = path.expanduser().resolve()
    if path.suffix.lower() != ".json":
        raise AgentProviderError("Provider setup requires a .json configuration path")
    schema_path = path.with_suffix(".schema.json")
    payload = config.model_dump(mode="json", by_alias=True, exclude_none=True, exclude_unset=True)
    payload["$schema"] = schema_path.name
    AgentProvidersConfig.model_validate(payload)
    content = json.dumps(payload, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
    if len(content.encode("utf-8")) > _MAX_CONFIG_BYTES:
        raise AgentProviderError("Provider configuration exceeds 262144 bytes")
    schema = json.dumps(AgentProvidersConfig.model_json_schema(), indent=2) + "\n"
    if path.exists():
        raise AgentProviderError(f"Provider configuration already exists; edit {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    with exact_file_write_lock(path):
        if path.exists():
            raise AgentProviderError(f"Provider configuration already exists; edit {path}")
        publish_text_file(schema_path, schema)
        with prepared_text_file(path, content) as prepared:
            replace_prepared_file(prepared, path)
    return path
