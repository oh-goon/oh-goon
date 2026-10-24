"""Provider configuration and explicit text requests without Fleet or database setup."""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict
from pathlib import Path

from pydantic import ValidationError

from oh_goon.core.models.agent_providers import (
    AgentConnection,
    AgentMessage,
    AgentModel,
    AgentProvider,
    AgentProviderError,
    AgentProvidersConfig,
    ProviderApi,
)
from oh_goon.integrations.agent_provider_client import AgentProviderClient
from oh_goon.runtime.agent_providers import (
    agent_providers_path,
    create_agent_providers,
    load_agent_providers,
)

_PROVIDERS: dict[str, tuple[ProviderApi, str, str | None]] = {
    "ollama": ("ollama", "http://127.0.0.1:11434", None),
    "litellm": ("openai-chat-completions", "http://127.0.0.1:4000/v1", None),
    "openrouter": ("openai-chat-completions", "https://openrouter.ai/api/v1", "OPENROUTER_API_KEY"),
    "openai": ("openai-responses", "https://api.openai.com/v1", "OPENAI_API_KEY"),
    "anthropic": ("anthropic-messages", "https://api.anthropic.com/v1", "ANTHROPIC_API_KEY"),
}


def agent_connection_from_args(args: argparse.Namespace) -> AgentConnection:
    path = agent_providers_path(os.environ, explicit=getattr(args, "agent_providers_file", None))
    return AgentConnection.select(
        load_agent_providers(path), getattr(args, "agent_model_profile", None)
    )


def add_provider_commands(commands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    providers = commands.add_parser(
        "providers", help="Configure external model provider connections"
    )
    actions = providers.add_subparsers(dest="provider_command", required=True)
    for name, help_text in (
        ("setup", "Create provider/model configuration and its JSON schema without connecting"),
        ("schema", "Print the provider configuration JSON schema"),
        ("path", "Print the selected provider configuration path"),
        ("list", "List configured providers and model profiles without connecting"),
        ("validate", "Validate configuration offline, without credentials or installed servers"),
        ("request", "Send explicitly supplied text to one configured model profile"),
    ):
        action = actions.add_parser(name, help=help_text)
        action.add_argument(
            "--config", type=Path, help="Select one JSON or TOML configuration file"
        )
        action.add_argument("--json", action="store_true", dest="as_json")
        if name == "setup":
            action.add_argument("--provider", required=True, choices=tuple(_PROVIDERS))
            action.add_argument("--model", required=True, help="Installed model or gateway alias")
            action.add_argument("--profile", default="default")
            action.add_argument("--base-url", help="Override the provider's setup URL")
            action.add_argument("--api-key-env", help="Environment variable containing the API key")
        if name == "request":
            action.add_argument(
                "--model", help="Configured model profile; otherwise use default_model"
            )
            source = action.add_mutually_exclusive_group(required=True)
            source.add_argument("--prompt", help="Explicit text to send")
            source.add_argument(
                "--prompt-file", type=Path, help="UTF-8 text file, or - for standard input"
            )


def run_provider_command(args: argparse.Namespace) -> int:
    path = agent_providers_path(os.environ, args.config)
    report: dict[str, object]
    try:
        if args.provider_command == "schema":
            print(json.dumps(AgentProvidersConfig.model_json_schema(), indent=2))
            return 0
        elif args.provider_command == "setup":
            api, base_url, key_name = _PROVIDERS[args.provider]
            config = AgentProvidersConfig(
                default_model=args.profile,
                providers={
                    args.provider: AgentProvider(
                        api=api,
                        base_url=args.base_url or base_url,
                        api_key_env=args.api_key_env or key_name,
                    )
                },
                models={args.profile: AgentModel(provider=args.provider, model=args.model)},
            )
            create_agent_providers(path, config)
            if args.as_json:
                print(json.dumps({"status": "configured_not_connected", "config": str(path)}))
            else:
                print(f"Configured {args.provider}/{args.model}: {path}")
            return 0
        elif args.provider_command == "path":
            report = {"config": str(path)}
        else:
            config = load_agent_providers(path)
            if args.provider_command == "request":
                selected = config.resolve(args.model, environment=os.environ)
                prompt = _prompt(args, selected.provider.max_request_bytes)
                reply = AgentProviderClient().complete(selected, [AgentMessage("user", prompt)])
                if args.as_json:
                    print(
                        json.dumps({"status": "model_reply", **asdict(reply)}, ensure_ascii=False)
                    )
                else:
                    print(reply.text)
                return 0
            report = {
                "status": "configured_not_connected",
                "config": str(path),
                "default_model": config.default_model,
                "providers": {
                    name: {
                        "api": provider.api,
                        "base_url": provider.base_url,
                        "api_key_env": provider.api_key_env,
                        "header_names": list(provider.headers),
                        "headers_from_env": provider.headers_from_env,
                    }
                    for name, provider in config.providers.items()
                },
                "models": {
                    name: {"provider": model.provider, "model": model.model}
                    for name, model in config.models.items()
                },
            }
    except (AgentProviderError, OSError, ValueError) as exc:
        message = (
            "Invalid provider configuration: "
            + exc.errors(include_input=False, include_context=False, include_url=False)[0]["msg"]
            if isinstance(exc, ValidationError)
            else str(exc)
        )
        if args.as_json:
            print(json.dumps({"status": "rejected", "error": message}), file=sys.stderr)
        else:
            print(message, file=sys.stderr)
        return 2
    if args.as_json:
        print(json.dumps(report, indent=2))
    elif args.provider_command == "path":
        print(path)
    else:
        print(f"Provider configuration: {path}")
        print("Configuration is valid. No provider connection or installation check performed.")
        for name, provider in config.providers.items():
            print(f"  {name}: {provider.api} at {provider.base_url}")
        for name, model in config.models.items():
            suffix = " (default)" if name == config.default_model else ""
            print(f"  model {name}: {model.provider} / {model.model}{suffix}")
    return 0


def _prompt(args: argparse.Namespace, limit: int) -> str:
    try:
        data: bytes
        if args.prompt is not None:
            data = args.prompt.encode("utf-8")
        elif str(args.prompt_file) == "-":
            data = sys.stdin.buffer.read(limit + 1)
        else:
            with args.prompt_file.open("rb") as stream:
                data = stream.read(limit + 1)
        if not data or len(data) > limit:
            raise AgentProviderError(
                "Prompt must be non-empty and fit the configured request byte limit"
            )
        return data.decode("utf-8", errors="strict")
    except (OSError, UnicodeError) as exc:
        raise AgentProviderError("Cannot read the explicit prompt as bounded UTF-8 text") from exc
