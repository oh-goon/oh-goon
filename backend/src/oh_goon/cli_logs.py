from __future__ import annotations

import argparse
from pathlib import Path

from oh_goon.core.launch_activity import safe_launch_log
from oh_goon.runtime.local_launch_binding import (
    LocalLaunchBindingStore,
    require_binding_matches_manifest,
)
from oh_goon.runtime.local_launch_state import LocalLaunchEventStore


def run_logs_command(args: argparse.Namespace) -> int | None:
    if args.command != "logs":
        return None
    workspace = Path.cwd().resolve()
    bindings = LocalLaunchBindingStore.configured()
    events = LocalLaunchEventStore.configured()
    try:
        binding = bindings.resolve(workspace)
        manifest = events.manifest(binding.launch_id)
        require_binding_matches_manifest(binding, manifest)
        log = safe_launch_log(events.events(binding.launch_id))
    except (OSError, ValueError) as exc:
        if args.as_json:
            print('{"error":"launch_log_unavailable"}')
        else:
            print("OH-GOON LAUNCH LOG UNAVAILABLE")
            print(f"Workspace: {workspace}")
            print(f"Reason: {exc}")
        return 1
    if args.as_json:
        print(log.to_json())
        return 0
    print("OH-GOON LAUNCH LOG")
    print(f"Launch: {log.launch_id}")
    print("Source: structured Launch Events; native process stdout remains with the launch owner")
    for entry in log.entries:
        service = "" if entry.service is None else f" · {entry.service} g{entry.service_generation}"
        detail = ""
        if entry.details:
            detail = " · " + ", ".join(f"{key}={value}" for key, value in entry.details.items())
        print(f"{entry.sequence:03d} · {entry.timestamp} · {entry.event}{service}{detail}")
    return 0
