from __future__ import annotations

import argparse
from collections.abc import Callable

from oh_goon.application import ApplicationRuntime
from oh_goon.cli_runtime_goon_import import import_gitlab

GoonHandler = Callable[[ApplicationRuntime, argparse.Namespace], int]


def run_goon_command(runtime: ApplicationRuntime, args: argparse.Namespace) -> int | None:
    if args.command != "goon":
        return None
    handlers: dict[str, GoonHandler] = {"import-gitlab": import_gitlab}
    handler = handlers.get(args.goon_command)
    return handler(runtime, args) if handler is not None else 2
