"""Provider configuration and explicit text requests without Fleet or database setup."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import asdict
from pathlib import Path
from typing import cast

from pydantic import ValidationError

from oh_goon.core.errors import FleetDomainError
from oh_goon.core.models.agent_providers import (
    AgentConnection,
    AgentMessage,
    AgentModel,
    AgentProvider,
    AgentProviderError,
    AgentProvidersConfig,
    CodexLoginConnection,
    CodexServiceConnection,
    NativeAgentDefinition,
    NativeAgentName,
    NativeAgentProfile,
    ProviderApi,
)
from oh_goon.integrations.agent_provider_client import AgentProviderClient
from oh_goon.runtime.agent_providers import (
    agent_providers_path,
    create_agent_providers,
    load_agent_providers,
    read_agent_provider_catalog,
    require_agent_provider_authoring_paths,
    resolve_agent_provider_catalog,
    resolve_native_agent,
    save_agent_provider_catalog,
)

_PROVIDERS: dict[str, tuple[ProviderApi, str, str | None]] = {
    "ollama": ("ollama", "http://127.0.0.1:11434", None),
    "litellm": ("openai-chat-completions", "http://127.0.0.1:4000/v1", None),
    "openrouter": ("openai-chat-completions", "https://openrouter.ai/api/v1", "OPENROUTER_API_KEY"),
    "openai": ("openai-responses", "https://api.openai.com/v1", "OPENAI_API_KEY"),
    "anthropic": ("anthropic-messages", "https://api.anthropic.com/v1", "ANTHROPIC_API_KEY"),
}


class UnsupportedNativeSetupCatalog(AgentProviderError):
    code = "UNSUPPORTED_CATALOG_FORMAT"


def _provider_error_message(error: Exception) -> str:
    messages = {
        "UNSUPPORTED_CATALOG_FORMAT": (
            "Guided setup writes JSON catalogs; select a .json path or author TOML manually"
        ),
        "PROVIDER_CONFIGURATION_INVALID": "Provider configuration does not satisfy the catalog contract",
        "PROVIDER_IO_FAILED": "Cannot access the selected provider configuration or input",
        "PROVIDER_REQUEST_INVALID": "Invalid provider configuration or request",
        "FLEET_PLAN_INVALID": "Selected native policy does not satisfy the execution contract",
    }
    return messages.get(
        _provider_error_code(error), "Selected provider or native request was refused"
    )


def _provider_error_code(error: Exception) -> str:
    if isinstance(error, ValidationError):
        return "PROVIDER_CONFIGURATION_INVALID"
    if isinstance(error, FleetDomainError):
        return error.code
    return "PROVIDER_IO_FAILED" if isinstance(error, OSError) else "PROVIDER_REQUEST_INVALID"


def _print_provider_refusal(args: argparse.Namespace, error: Exception, *, native: bool) -> None:
    code = _provider_error_code(error)
    if code == "UNSUPPORTED_CATALOG_FORMAT":
        action = "Use --config with a .json path, or manually author the existing TOML catalog."
    elif native:
        action = (
            "Inspect the public executable, a new profile name, --workspace-root and --mode; "
            "supply all required options and keep catalog/schema paths outside the native home. "
            "Existing profiles are preserved."
        )
    else:
        action = (
            "Inspect the selected catalog and provider/model selection, including named "
            "environment-variable references; use agent providers validate before requesting work."
        )
    report: dict[str, object] = {
        "status": "rejected",
        "code": code,
        "error": _provider_error_message(error),
        "retryable": error.retryable if isinstance(error, FleetDomainError) else False,
        "safe_next_action": action,
    }
    if native:
        report["schema_version"] = "oh-goon.native-agent-setup/v1"
        report["execution_admitted"] = False
    else:
        report["schema"] = "oh-goon.provider-refusal/v1"
    if args.as_json:
        print(json.dumps(report))
    else:
        print(f"{code}: {report['error']}", file=sys.stderr)
        print(action, file=sys.stderr)


def add_native_setup_command(
    commands: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    setup = commands.add_parser(
        "setup", help="Configure one installed native agent; observe its version without model work"
    )
    setup.add_argument("harness", choices=("codex", "pi", "opencode"))
    setup.add_argument("--config", type=Path, help="Existing or new JSON provider catalog")
    setup.add_argument("--profile", help="New execution profile name")
    setup.add_argument("--executable", type=Path, help="Selected installed public executable")
    setup.add_argument("--runtime-root", type=Path, action="append", default=[])
    setup.add_argument("--mode", choices=("managed", "codex-login", "codex-service"))
    setup.add_argument(
        "--workspace-root", type=Path, action="append", default=[], help="Allowed workspace root"
    )
    setup.add_argument("--inference-profile", help="Existing model profile for managed inference")
    setup.add_argument(
        "--home", type=Path, help="Explicit existing native Codex authentication home"
    )
    setup.add_argument("--model", help="Explicit model for the existing typed Codex connection")
    setup.add_argument("--reasoning-effort", help="Explicit native Codex reasoning selection")
    setup.add_argument("--make-default", action="store_true", help="Select this native default")
    setup.add_argument("--no-input", action="store_true", help="Refuse missing policy choices")
    setup.add_argument("--json", action="store_true", dest="as_json")


def _setup_choice(args: argparse.Namespace, value: str | None, label: str) -> str:
    if value is None:
        if args.no_input or args.as_json or not (sys.stdin.isatty() and sys.stderr.isatty()):
            raise AgentProviderError(f"Native setup requires {label}; provide its explicit option")
        print(f"{label}: ", end="", file=sys.stderr, flush=True)
        try:
            value = input().strip()
        except EOFError:
            raise AgentProviderError(f"Native setup requires {label}") from None
    if not value.strip():
        raise AgentProviderError(f"Native setup requires {label}")
    return value


def _require_setup_catalog_separation(home: str | None, requested: Path, resolved: Path) -> None:
    from oh_goon.integrations.agent_native_execution import (
        require_codex_authentication_home_separation,
    )

    paths = (
        requested,
        requested.with_suffix(".schema.json"),
        resolved,
        resolved.with_suffix(".schema.json"),
    )
    if home is not None:
        require_codex_authentication_home_separation(Path(home), paths)
    require_agent_provider_authoring_paths(paths)


def run_native_setup_command(args: argparse.Namespace) -> int:
    """Add one explicit native selection; native login and model state stay untouched."""
    from oh_goon.integrations.agent_native_execution import native_executable_sha256
    from oh_goon.integrations.fleet_plan_native_steps import require_native_agent_compatibility

    try:
        mode = (
            args.mode if args.mode is not None else ("managed" if args.harness != "codex" else None)
        )
        mode = _setup_choice(args, mode, "--mode (managed, codex-login or codex-service)")
        if mode not in {"managed", "codex-login", "codex-service"}:
            raise AgentProviderError("Select managed, codex-login or codex-service setup mode")
        if args.harness != "codex" and mode != "managed":
            raise AgentProviderError("Protected native login/service setup requires Codex")
        if mode == "managed" and any(
            value is not None for value in (args.home, args.model, args.reasoning_effort)
        ):
            raise AgentProviderError("Managed setup uses its configured inference profile")
        if mode != "managed" and args.inference_profile is not None:
            raise AgentProviderError("Protected Codex setup cannot select managed inference")
        roots = args.workspace_root or [Path(_setup_choice(args, None, "--workspace-root"))]
        workspace_roots = tuple(str(root.expanduser().resolve()) for root in roots)
        home = model = reasoning = None
        if mode != "managed":
            home = str(
                Path(_setup_choice(args, str(args.home) if args.home else None, "--home"))
                .expanduser()
                .resolve()
            )
            model = _setup_choice(args, args.model, "--model")
            reasoning = _setup_choice(args, args.reasoning_effort, "--reasoning-effort")
        requested_path = agent_providers_path(os.environ, args.config, resolve=False)
        path = requested_path.resolve()
        if path.suffix.lower() != ".json":
            raise UnsupportedNativeSetupCatalog(
                "Guided setup writes JSON catalogs; select --config with a .json path or "
                "author the existing TOML catalog manually"
            )
        _require_setup_catalog_separation(home, requested_path, path)
        catalog = read_agent_provider_catalog(path, missing_ok=True, for_authoring=True)
        config = catalog.config
        profile_name = args.profile if args.profile is not None else f"{args.harness}-{mode}"
        if profile_name in config.native_profiles:
            raise AgentProviderError("Native profile already exists; select a new --profile name")
        harness = cast(NativeAgentName, args.harness)
        definition = config.native_agents.get(harness)
        if definition is None:
            definition = NativeAgentDefinition(
                executable=str(args.executable.expanduser().resolve())
                if args.executable is not None
                else None,
                runtime_roots=tuple(str(root.expanduser().resolve()) for root in args.runtime_root),
            )
        elif args.executable is not None or args.runtime_root:
            raise AgentProviderError(
                "Native agent definition already exists; reuse it without --executable or --runtime-root"
            )
        if mode == "managed":
            inference = _setup_choice(
                args,
                args.inference_profile
                if args.inference_profile is not None
                else config.default_model,
                "--inference-profile from the provider catalog",
            )
            profile = NativeAgentProfile(
                backend="bubblewrap",
                allowed_agents=(harness,),
                workspace_roots=workspace_roots,
                inference_profile=inference,
            )
        else:
            assert home is not None and model is not None and reasoning is not None
            connection = {"home": home, "model": model, "reasoning_effort": reasoning}
            profile = NativeAgentProfile(
                backend="local",
                allowed_agents=(harness,),
                workspace_roots=workspace_roots,
                codex_login=CodexLoginConnection(**connection) if mode == "codex-login" else None,
                codex_service=CodexServiceConnection(**connection)
                if mode == "codex-service"
                else None,
            )
        candidate = AgentProvidersConfig.model_validate(
            {
                **config.model_dump(mode="python", by_alias=True, exclude_unset=True),
                "native_agents": {**config.native_agents, harness: definition},
                "native_profiles": {**config.native_profiles, profile_name: profile},
                "default_native_profile": profile_name
                if args.make_default
                else config.default_native_profile,
            }
        )
        resolved = resolve_agent_provider_catalog(candidate, path)
        selected = resolve_native_agent(
            resolved,
            harness,
            profile_name,
            workspace=Path(workspace_roots[0]),
            environment=os.environ,
        )
        require_native_agent_compatibility(selected)
        assert selected.definition.executable is not None
        executable = Path(selected.definition.executable)
        from oh_goon.integrations.hashmarks.runtime_contract import require_loaded_hashmarks_runtime

        require_loaded_hashmarks_runtime()
        _require_setup_catalog_separation(home, requested_path, path)
        version = _native_setup_version(executable, harness)
        if native_executable_sha256(executable) != selected.executable_sha256:
            raise AgentProviderError("Selected native executable changed during setup")
        if harness not in config.native_agents:
            candidate = candidate.model_copy(
                update={"native_agents": {**candidate.native_agents, harness: selected.definition}}
            )
        _require_setup_catalog_separation(home, requested_path, path)
        save_agent_provider_catalog(path, candidate, expected_revision=catalog.revision)
        report = {
            "schema_version": "oh-goon.native-agent-setup/v1",
            "status": "configured_not_qualified",
            "config": str(path),
            "agent": harness,
            "profile": profile_name,
            "default_native_profile": candidate.default_native_profile,
            "mode": mode,
            "executable": str(executable),
            "executable_sha256": selected.executable_sha256,
            "native_version": version,
            "execution_admitted": False,
            "qualification": "NOT_QUALIFIED",
            "native_authentication": "unverified",
        }
    except (AgentProviderError, FleetDomainError, OSError, ValueError) as exc:
        _print_provider_refusal(args, exc, native=True)
        return 2
    if args.as_json:
        print(json.dumps(report, ensure_ascii=False))
    else:
        print(f"Configured native {harness} profile {profile_name}: {path}")
        print(f"Observed native version {version}. Execution remains NOT_QUALIFIED.")
    return 0


def _native_setup_version(executable: Path, harness: NativeAgentName) -> str:
    from oh_goon.engine.command import (
        CommandCancelledError,
        CommandOutputLimitExceeded,
        CommandSupervisionTerminated,
        CommandTimeoutExpired,
        run_command,
    )

    try:
        result = run_command(
            str(executable),
            "--version",
            timeout=10,
            env=dict(os.environ),
            require_process_group_quiescence=True,
            parent_death_signal=True,
            max_capture_bytes_per_stream=4096,
        )
    except (
        CommandCancelledError,
        CommandOutputLimitExceeded,
        CommandSupervisionTerminated,
        CommandTimeoutExpired,
        OSError,
    ):
        raise AgentProviderError("Native executable version observation did not complete") from None
    prefix = {"codex": r"(?:codex-cli )?", "pi": r"(?:pi )?", "opencode": r"(?:opencode )?"}
    version = re.fullmatch(
        prefix[harness] + r"v?(\d+\.\d+\.\d+(?:[-+][A-Za-z0-9.-]+)?)", result.stdout.strip()
    )
    if (
        result.returncode != 0
        or not result.supervisor.final_process_group_quiescent
        or result.stdout_truncated
        or version is None
    ):
        raise AgentProviderError("Native executable did not report a bounded version observation")
    return version.group(1)


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
            if config.default_native_profile is not None:
                report["default_native_profile"] = config.default_native_profile
    except (FleetDomainError, OSError, ValueError) as exc:
        _print_provider_refusal(args, exc, native=False)
        return 2
    if args.as_json:
        print(json.dumps(report, indent=2))
    elif args.provider_command == "path":
        print(path)
    else:
        print(f"Provider configuration: {path}")
        print("Configuration is valid. No provider connection or installation check performed.")
        if config.default_native_profile is not None:
            print(f"Default native profile: {config.default_native_profile}")
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
