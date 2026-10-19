from __future__ import annotations

import argparse
from pathlib import Path

from oh_goon.core.launch_control import safe_launch_stop_target, signal_launch_stop
from oh_goon.runtime.local_launch_binding import (
    LocalLaunchBindingStore,
    require_binding_matches_manifest,
)
from oh_goon.runtime.local_launch_state import LocalLaunchEventStore


def run_stop_command(args: argparse.Namespace) -> int | None:
    if args.command != "stop":
        return None
    workspace = Path.cwd().resolve()
    bindings = LocalLaunchBindingStore.configured()
    events = LocalLaunchEventStore.configured()
    try:
        binding = bindings.resolve(workspace)
        manifest = events.manifest(binding.launch_id)
        require_binding_matches_manifest(binding, manifest)
        target = safe_launch_stop_target(manifest)
        if target is None:
            print("OH-GOON LAUNCH ALREADY TERMINAL")
            print(f"Launch: {manifest.identity.launch_id}")
            print(f"Lifecycle: {manifest.lifecycle.state.value}")
            return 0
        signal_launch_stop(target)
    except (OSError, ValueError) as exc:
        print("OH-GOON STOP REFUSED")
        print(f"Workspace: {workspace}")
        print(f"Reason: {exc}")
        print("Inspect: oh-goon status")
        return 1
    print("OH-GOON STOP REQUESTED")
    print(f"Launch: {target.launch_id}")
    print("Owner: exact birth-proven process signaled through pidfd")
    print("Lifecycle: unchanged until the launch owner publishes terminal evidence")
    print("Status: oh-goon status")
    return 0
