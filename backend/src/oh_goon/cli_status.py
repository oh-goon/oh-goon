from __future__ import annotations

import argparse
import json
from pathlib import Path

from oh_goon.core.launch_status import safe_launch_status
from oh_goon.runtime.local_launch_binding import (
    LocalLaunchBindingStore,
    require_binding_matches_manifest,
)
from oh_goon.runtime.local_launch_state import LocalLaunchEventStore

LAUNCH_STATUS_ERROR_SCHEMA = "oh-goon.launch-status-error/v1"


def run_status_command(args: argparse.Namespace) -> int | None:
    if args.command != "status":
        return None
    workspace = Path.cwd().resolve()
    bindings = LocalLaunchBindingStore.configured()
    events = LocalLaunchEventStore.configured()
    try:
        binding = bindings.resolve(workspace)
        manifest = events.manifest(binding.launch_id)
        require_binding_matches_manifest(binding, manifest)
        status = safe_launch_status(manifest)
    except (OSError, ValueError) as exc:
        if args.as_json:
            print(json.dumps({"error": "launch_status_unavailable", "schema": LAUNCH_STATUS_ERROR_SCHEMA}, sort_keys=True, separators=(",", ":")))
        else:
            print("OH-GOON LAUNCH STATUS UNAVAILABLE")
            print(f"Workspace: {workspace}")
            print(f"Reason: {exc}")
        return 1

    if args.as_json:
        print(status.to_json())
        return 0
    print("OH-GOON LAUNCH")
    print(f"Launch:        {status.launch_id}")
    print(f"Profile:       {status.profile}")
    print(f"Lifecycle:     {status.lifecycle.value}")
    print(f"Freshness:     {status.observation_freshness.value}")
    print(f"Owner proof:   {status.owner_proof.value}")
    return 0
