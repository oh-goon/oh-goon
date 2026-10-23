"""Offline native-service recipes; no provider, process or Run is selected."""

from __future__ import annotations

import argparse
import json
import shlex
import sys
from pathlib import Path
from typing import cast

from oh_goon.core.models.agent_providers import AgentProviderError
from oh_goon.integrations.agent_native_execution import prepare_native_service_setup


def add_service_commands(
    commands: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    service = commands.add_parser(
        "service", help="Prepare native service setup without starting it"
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


def run_service_command(args: argparse.Namespace) -> int:
    try:
        recipe = prepare_native_service_setup(
            args.agent, executable=args.executable, socket_path=args.socket
        )
    except AgentProviderError as exc:
        message = str(exc)
        print(
            json.dumps({"status": "rejected", "error": message}) if args.as_json else message,
            file=sys.stderr,
        )
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
