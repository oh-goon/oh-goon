"""Explicit native-service preparation and connection without Run admission."""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import re
import shlex
import sys
from pathlib import Path
from typing import cast

from pydantic import SecretStr

from oh_goon.core.models.agent_providers import AgentProviderError
from oh_goon.integrations.agent_native_execution import prepare_native_service_setup


def add_service_commands(
    commands: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    service = commands.add_parser(
        "service", help="Prepare or explicitly connect to a native service"
    )
    actions = service.add_subparsers(dest="service_command", required=True)
    prepare = actions.add_parser("prepare", help="Print an offline native launch/connect recipe")
    prepare.add_argument("--agent", choices=("codex", "opencode"), required=True)
    prepare.add_argument(
        "--executable",
        type=Path,
        required=True,
        help="Explicit public executable path; not inspected",
    )
    prepare.add_argument(
        "--socket", type=Path, help="Explicit advertised Unix socket path; required for Codex"
    )
    prepare.add_argument("--json", action="store_true", dest="as_json")
    connect = actions.add_parser("connect", help="Connect for bounded readiness; do not admit work")
    connect.add_argument("--agent", choices=("codex", "opencode"), required=True)
    connect.add_argument("--socket", type=Path, help="Explicit advertised Codex Unix socket")
    connect.add_argument("--endpoint", help="Explicit numeric-loopback OpenCode HTTP endpoint")
    connect.add_argument("--password-env", help="Explicit OpenCode transport-password variable")
    connect.add_argument("--timeout-seconds", type=float, default=10.0)
    connect.add_argument("--json", action="store_true", dest="as_json")


def run_service_command(args: argparse.Namespace) -> int:
    try:
        if args.service_command == "connect":
            report = _connection_metadata(args)
            print(json.dumps(report, indent=2, ensure_ascii=False))
            return 0
        recipe = prepare_native_service_setup(
            args.agent, executable=args.executable, socket_path=args.socket
        )
    except AgentProviderError as exc:
        connecting = args.service_command == "connect"
        report = {
            "schema": "oh-goon.native-service/v1",
            "status": "rejected",
            "agent": args.agent,
            "operation": args.service_command,
            "code": exc.code,
            "retryable": exc.retryable,
            "execution_admitted": False,
            "error": "Native service connection was refused or incomplete"
            if connecting
            else "Native service setup arguments were refused",
            "safe_next_action": (
                "Check the explicit agent, endpoint/socket, timeout and native readiness. "
                "No closed-transport or successful-operation result was established."
                if connecting
                else "Select the supported agent, absolute public executable and required socket. "
                "No service was started or connected."
            ),
        }
        if args.as_json:
            print(json.dumps(report))
        else:
            print(f"{report['code']}: {report['error']}", file=sys.stderr)
            print(report["safe_next_action"], file=sys.stderr)
        return 2
    if args.as_json:
        print(json.dumps(recipe, indent=2, ensure_ascii=False))
    else:
        print("Native service setup prepared. Nothing started, connected or admitted.")
        print("Native launch: " + shlex.join(cast(list[str], recipe["launch_argv"])))
        if recipe["connect_argv"] is not None:
            print("Native connection: " + shlex.join(cast(list[str], recipe["connect_argv"])))
        print(json.dumps(recipe, indent=2, ensure_ascii=False))
    return 0


def _connection_metadata(args: argparse.Namespace) -> dict[str, object]:
    timeout = args.timeout_seconds
    if not math.isfinite(timeout) or not 0 < timeout <= 60:
        raise AgentProviderError("Connection timeout must be within 60 seconds")
    if args.agent == "codex":
        if args.socket is None or args.endpoint is not None or args.password_env is not None:
            raise AgentProviderError("Codex connection requires only its explicit Unix socket")
        return asyncio.run(_codex_metadata(args.socket, timeout))
    if args.socket is not None or not args.endpoint or not args.password_env:
        raise AgentProviderError("OpenCode connection requires endpoint and password-env")
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,255}", args.password_env) is None:
        raise AgentProviderError("Service password reference must name an environment variable")
    return asyncio.run(_opencode_metadata(args.endpoint, args.password_env, timeout))


async def _codex_metadata(socket_path: Path, timeout: float) -> dict[str, object]:
    from oh_goon.integrations.agent_codex_service import CodexServiceClient

    async with CodexServiceClient(socket_path, timeout_seconds=timeout) as client:
        metadata = client.metadata
    return {
        **metadata,
        "transport_cleanup": client.metadata["transport_cleanup"],
        "connection_closed": True,
    }


async def _opencode_metadata(endpoint: str, password_env: str, timeout: float) -> dict[str, object]:
    from oh_goon.integrations.agent_opencode_service import (
        OpenCodeServiceClient,
        validate_opencode_service_endpoint,
    )

    selected_endpoint = validate_opencode_service_endpoint(endpoint)
    value = os.environ.get(password_env)
    if not value:
        raise AgentProviderError("Selected service transport password is unavailable")
    password = SecretStr(value)
    async with OpenCodeServiceClient(
        selected_endpoint, password=password, timeout_seconds=timeout
    ) as client:
        metadata = client.metadata
        assert metadata is not None
        facts = {"service_version": metadata.version, "service_pid": metadata.pid}
    return {
        "status": "connected_not_qualified",
        "agent": "opencode",
        "execution_admitted": False,
        "native_authentication": "unverified",
        "phase_policy": "unverified",
        "private_store_exclusion": "unverified",
        "tool_quiescence": "unverified",
        "connection_closed": True,
        **facts,
    }
